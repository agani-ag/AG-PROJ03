"""Tests for the shell: PIN sign-in and the cron hub.

The cron tests matter more than they look: the two endpoints are public URLs, and the whole hub's
scheduled work runs through them.
"""
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import cronhub
from .models import AdminPin, CronLock, CronRun

CRON_KEY = 'test-cron-key'


class AdminPinTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')

    def test_pin_is_hashed_and_case_insensitive(self):
        pin = AdminPin(user=self.user)
        pin.set_pin('ag222')
        pin.save()
        self.assertNotIn('AG222', pin.pin_hash)
        self.assertTrue(pin.check_pin('AG222'))
        self.assertTrue(pin.check_pin('ag222'))
        self.assertFalse(pin.check_pin('ZZ999'))

    def test_pin_login_endpoint(self):
        pin = AdminPin(user=self.user)
        pin.set_pin('AG222')
        pin.save()
        r = self.client.post(reverse('pin_auth'), '{"pin": "AG222"}', content_type='application/json')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)

    def test_wrong_pin_is_refused(self):
        r = self.client.post(reverse('pin_auth'), '{"pin": "ZZ999"}', content_type='application/json')
        self.assertEqual(r.status_code, 401)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)


class CronLockTests(TestCase):
    def test_second_run_cannot_take_a_held_lock(self):
        self.assertTrue(CronLock.acquire('job', 60))
        self.assertFalse(CronLock.acquire('job', 60))

    def test_expired_lock_is_stolen(self):
        CronLock.acquire('job', 60)
        CronLock.objects.filter(name='job').update(locked_until=timezone.now() - timedelta(seconds=1))
        self.assertTrue(CronLock.acquire('job', 60))

    def test_release_frees_it(self):
        CronLock.acquire('job', 60)
        CronLock.release('job')
        self.assertTrue(CronLock.acquire('job', 60))


class HubTests(TestCase):
    """The registry and runner, with hooks registered just for the test."""

    def setUp(self):
        self._saved = list(cronhub._hooks)
        cronhub._hooks.clear()

    def tearDown(self):
        cronhub._hooks.clear()
        cronhub._hooks.extend(self._saved)

    def test_hooks_run_in_registration_order_and_items_are_summed(self):
        order = []

        @cronhub.tick('t.one')
        def one(ctx):
            order.append('one')
            return {'items': 2}

        @cronhub.tick('t.two')
        def two(ctx):
            order.append('two')
            return {'sent': 3}

        summary = cronhub.run(cronhub.TICK)
        self.assertEqual(order, ['one', 'two'])
        self.assertEqual(summary['items'], 5)
        self.assertTrue(summary['ok'])

    def test_one_failing_hook_does_not_stop_the_others(self):
        @cronhub.tick('t.bad')
        def bad(ctx):
            raise ValueError('boom')

        @cronhub.tick('t.good')
        def good(ctx):
            return {'items': 1}

        summary = cronhub.run(cronhub.TICK)
        self.assertFalse(summary['ok'])                       # the run reports the failure
        self.assertIn('boom', summary['hooks']['t.bad']['error'])
        self.assertEqual(summary['hooks']['t.good']['items'], 1)   # and the rest still ran

    def test_registering_the_same_name_twice_replaces_it(self):
        @cronhub.tick('t.same')
        def first(ctx):
            return {'items': 1}

        @cronhub.tick('t.same')
        def second(ctx):
            return {'items': 7}

        self.assertEqual(len(cronhub.hooks(cronhub.TICK)), 1)
        self.assertEqual(cronhub.run(cronhub.TICK)['items'], 7)

    def test_options_reach_hooks_that_accept_them(self):
        seen = {}

        @cronhub.cleanup('c.opt')
        def with_opts(ctx, vacuum=False, **_):
            seen['vacuum'] = vacuum
            return {}

        @cronhub.cleanup('c.plain')
        def without_opts(ctx):
            return {}

        cronhub.run(cronhub.CLEANUP, vacuum=True)
        self.assertTrue(seen['vacuum'])                       # and the plain hook did not error

    def test_hook_is_told_how_long_it_has(self):
        seen = {}

        @cronhub.tick('t.budget', budget=3)
        def budgeted(ctx):
            seen['left'] = ctx.seconds_left()
            return {}

        cronhub.run(cronhub.TICK, budget_seconds=20)
        self.assertGreater(seen['left'], 2.0)
        self.assertLessEqual(seen['left'], 3.0)


@override_settings(CRON_KEY=CRON_KEY)
class CronEndpointTests(TestCase):
    def setUp(self):
        self._saved = list(cronhub._hooks)
        cronhub._hooks.clear()

        @cronhub.tick('test.tick')
        def tick(ctx):
            return {'items': 4}

        @cronhub.cleanup('test.cleanup')
        def cleanup(ctx, **_):
            return {'items': 1}

    def tearDown(self):
        cronhub._hooks.clear()
        cronhub._hooks.extend(self._saved)

    def test_no_key_is_a_404_not_a_403(self):
        self.assertEqual(self.client.get('/cron/tick').status_code, 404)
        self.assertEqual(self.client.get('/cron/tick?key=wrong').status_code, 404)

    @override_settings(CRON_KEY='')
    def test_unset_key_closes_the_endpoint(self):
        self.assertEqual(self.client.get(f'/cron/tick?key={CRON_KEY}').status_code, 404)

    def test_tick_runs_hooks_and_writes_a_run_row(self):
        r = self.client.get(f'/cron/tick?key={CRON_KEY}')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()['items'], 4)
        run = CronRun.objects.get(job='tick')
        self.assertTrue(run.ok)
        self.assertEqual(run.items, 4)
        self.assertIn('test.tick', run.detail)

    def test_overlapping_call_answers_200_skipped(self):
        CronLock.acquire('tick', 60)                       # pretend a run is in flight
        r = self.client.get(f'/cron/tick?key={CRON_KEY}')
        self.assertEqual(r.status_code, 200)               # not 5xx: overlap is normal
        self.assertEqual(r.json()['skipped'], 'locked')
        self.assertFalse(CronRun.objects.exists())

    def test_a_failing_hook_makes_the_endpoint_answer_500(self):
        cronhub._hooks.clear()

        @cronhub.tick('test.bad')
        def bad(ctx):
            raise RuntimeError('nope')

        r = self.client.get(f'/cron/tick?key={CRON_KEY}')
        self.assertEqual(r.status_code, 500)               # so the cron service alerts
        self.assertFalse(CronRun.objects.get(job='tick').ok)

    def test_lock_is_released_even_after_a_failure(self):
        cronhub._hooks.clear()

        @cronhub.tick('test.bad')
        def bad(ctx):
            raise RuntimeError('nope')

        self.client.get(f'/cron/tick?key={CRON_KEY}')
        self.assertTrue(CronLock.acquire('tick', 60))       # free again

    def test_cleanup_passes_vacuum_through(self):
        seen = {}
        cronhub._hooks.clear()

        @cronhub.cleanup('test.vac')
        def vac(ctx, vacuum=False, **_):
            seen['vacuum'] = vacuum
            return {}

        self.client.get(f'/cron/cleanup?key={CRON_KEY}&vacuum=1')
        self.assertTrue(seen['vacuum'])

    def test_health_lists_hooks_and_last_run(self):
        self.client.get(f'/cron/tick?key={CRON_KEY}')
        body = self.client.get(f'/cron/health?key={CRON_KEY}').json()
        self.assertIn('test.tick', body['hooks']['tick'])
        self.assertEqual(body['last_run']['tick']['items'], 4)
        self.assertIsNone(body['last_run']['cleanup'])


class CoreCleanupHookTests(TestCase):
    """core/cron.py's own hooks."""

    def test_old_run_rows_are_trimmed(self):
        from .cron import trim_run_log
        old = CronRun.objects.create(job='tick')
        CronRun.objects.filter(pk=old.pk).update(started_at=timezone.now() - timedelta(days=60))
        fresh = CronRun.objects.create(job='tick')

        result = trim_run_log(cronhub.Context(kind=cronhub.CLEANUP))
        self.assertEqual(result['items'], 1)
        self.assertEqual(list(CronRun.objects.values_list('pk', flat=True)), [fresh.pk])

    def test_vacuum_is_skipped_unless_asked(self):
        from .cron import vacuum
        self.assertEqual(vacuum(cronhub.Context(kind=cronhub.CLEANUP))['skipped'], 'not requested')


class CronPageTests(TestCase):
    def setUp(self):
        User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(User.objects.get(username='admin'))

    def test_page_lists_the_two_jobs(self):
        r = self.client.get(reverse('cron_page'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Tick · every 15 minutes')
        self.assertContains(r, 'Cleanup · nightly')

    def test_run_now_runs_the_job(self):
        r = self.client.post(reverse('cron_run_now', args=['tick']), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(CronRun.objects.filter(job='tick', triggered_by='admin').exists())

    def test_unknown_job_is_404(self):
        self.assertEqual(self.client.post(reverse('cron_run_now', args=['nope'])).status_code, 404)

    def test_page_needs_the_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('cron_page')).status_code, 302)


class MigrationPackageTests(TestCase):
    """A bare `makemigrations` must cover every app in this project.

    Django treats an app with no `migrations/` package as unmigrated and skips it, so a missing
    `__init__.py` makes `makemigrations` answer "No changes detected" instead of writing the
    app's migration. The file is committed on purpose; the generated migrations are not.
    """

    def test_every_local_app_ships_a_migrations_package(self):
        from pathlib import Path

        from django.apps import apps

        root = Path(__file__).resolve().parent.parent
        missing = []
        for config in apps.get_app_configs():
            path = Path(config.path)
            if root not in path.parents:
                continue  # Django's own apps bring their migrations with them
            if not (path / 'migrations' / '__init__.py').exists():
                missing.append(f'{config.label} ({path.name}/migrations/__init__.py)')
        self.assertEqual(missing, [], 'bare makemigrations would skip these apps')

class BrandMarkTests(TestCase):
    """The two marks: SyncHub's repeat arrow, and a module's own icon.

    Both are easy to lose in a template edit and nothing else would fail if they were, so they
    are pinned here.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')

    def test_the_sign_in_page_wears_the_repeat_mark(self):
        r = self.client.get(reverse('login'))
        self.assertContains(r, 'brand-mark is-app')
        self.assertContains(r, 'images/app-icon.svg')

    def test_the_sidebar_wears_it_too(self):
        self.client.force_login(self.admin)
        r = self.client.get(reverse('dashboard'))
        self.assertContains(r, 'brand-mark is-app')
        self.assertContains(r, 'images/app-icon.svg')

    def test_hibiscus_shows_its_flower(self):
        self.client.force_login(self.admin)
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('#i-flower', body)              # sidebar link and dashboard card
        self.assertNotIn('#i-heart-pulse', body)

    def test_every_module_icon_is_in_the_sprite(self):
        from core.modules import MODULES
        sprite = (Path(settings.BASE_DIR) / 'static' / 'icons.svg').read_text(encoding='utf-8')
        for module in MODULES:
            self.assertIn(f'id="i-{module.icon}"', sprite, module.key)

    def test_the_artwork_is_the_repeat_arrow_in_black_and_white(self):
        """One file for the favicon, the sidebar badge and the sign-in page.

        Black tile, white mark — the same treatment as the SyncUp app icon, which is why the
        badge is an image rather than a sprite icon tinted with the accent colour.
        """
        art = (Path(settings.BASE_DIR) / 'static' / 'images' / 'app-icon.svg').read_text(
            encoding='utf-8')
        self.assertIn('SyncHub', art)
        self.assertIn('fill="#000000"', art)          # the tile
        self.assertIn('stroke="#ffffff"', art)        # the arrow
        self.assertIn('a31.5 31.5', art)              # the loop
        self.assertNotIn('M12,6V9l4,-4', art)         # the old SyncUp two-arrow path is gone

class StaticLayoutTests(TestCase):
    """Every asset this project ships lives in the project's own `static/` folder.

    Not in `<app>/static/`, which is Django's other convention: a server whose /static/ points at
    `static/` rather than `staticfiles/` serves a project-level file and 404s anything that only
    appears after collectstatic. That is not theoretical — the diary shipped unstyled on the live
    server for exactly that reason. Keeping assets here means either mapping works.
    """

    def test_no_app_ships_its_own_static_folder(self):
        root = Path(settings.BASE_DIR)
        strays = [p.relative_to(root).as_posix()
                  for p in root.glob('*/static')
                  if p.is_dir() and p.parent.name not in ('staticfiles',)
                  and (root / 'static') != p]
        self.assertEqual(strays, [], 'move these into static/<module>/ — see README, Layout')

    def test_the_diary_assets_are_where_the_pages_ask_for_them(self):
        from django.contrib.staticfiles import finders
        for path in ['hibiscus/user.css', 'hibiscus/user.js', 'hibiscus/flower.svg',
                     'css/app.css', 'icons.svg', 'images/app-icon.svg']:
            found = finders.find(path)
            self.assertIsNotNone(found, path)
            self.assertTrue(Path(found).is_relative_to(Path(settings.BASE_DIR) / 'static'), path)

    def test_the_diary_pages_only_ask_for_files_that_exist(self):
        import re

        from django.contrib.staticfiles import finders
        body = self.client.get(reverse('hibiscus_me_login')).content.decode()
        asked = re.findall(r'(?:href|src)="/static/([^"?]+)', body)
        self.assertTrue(asked)
        for path in asked:
            self.assertIsNotNone(finders.find(path), path)


class HubSettingsTests(TestCase):
    """What every module needs and none of them owns.

    These three were duplicated in Hibiscus and in J6 — two places to set one value, and two
    chances to set it differently. They live here now, and the modules read them.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)

    def test_the_page_needs_the_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('system_settings')).status_code, 302)

    def test_it_is_in_the_sidebar_under_system(self):
        body = self.client.get(reverse('dashboard')).content.decode()
        self.assertIn(reverse('system_settings'), body)

    def test_saving_the_three_values(self):
        from core.models import HubSettings
        r = self.client.post(reverse('system_settings'), {
            'site_base': 'https://synchub.example.com',
            'syncup_base': 'https://syncup.example.com',
            'syncup_notify_key': 'secret-key',
        }, follow=True)
        self.assertEqual(r.status_code, 200)
        hub = HubSettings.load()
        self.assertEqual(hub.syncup_base, 'https://syncup.example.com')
        self.assertEqual(hub.updated_by, self.admin)

    def test_it_warns_when_syncup_points_at_this_site(self):
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = 'https://testserver'
        hub.save()
        self.assertContains(self.client.get(reverse('system_settings')),
                            'The SyncUp address is this same site')

    def test_it_suggests_this_sites_address_while_it_is_blank(self):
        r = self.client.get(reverse('system_settings'))
        self.assertContains(r, 'This site looks like')
        self.assertContains(r, 'testserver')

    def test_it_lists_the_modules_that_use_them(self):
        r = self.client.get(reverse('system_settings'))
        self.assertContains(r, 'Hibiscus')
        self.assertContains(r, 'J6')

    def test_a_link_is_built_only_when_the_address_is_known(self):
        from core.models import HubSettings
        hub = HubSettings.load()
        self.assertEqual(hub.url_for('/j6/'), '')
        hub.site_base = 'https://synchub.example.com/'
        hub.save()
        self.assertEqual(HubSettings.load().url_for('/j6/'),
                         'https://synchub.example.com/j6/')

    def test_the_modules_no_longer_carry_their_own_copies(self):
        """The duplication this page exists to remove must not come back."""
        from hibiscus.models import Settings as DiarySettings
        from j6.models import Settings as ChatSettings
        for model in (DiarySettings, ChatSettings):
            names = {f.name for f in model._meta.get_fields()}
            for moved in ('site_base', 'syncup_base', 'syncup_notify_key'):
                self.assertNotIn(moved, names, f'{model.__module__} still owns {moved}')


class CarrySyncUpCommandTests(TestCase):
    """The command that saves a server's SyncUp settings from the column drop.

    It exists because migrations are generated per environment here, so the data step written on
    a laptop never reaches the server — and a deploy that silently loses a typed-in address is
    the one kind of deploy that is not allowed.
    """

    def _run(self, *args):
        from io import StringIO

        from django.core.management import call_command
        out = StringIO()
        call_command('carry_syncup_to_hub', *args, stdout=out)
        return out.getvalue()

    def test_it_says_so_when_the_columns_are_already_gone(self):
        """Which is the normal case after the migration — it must be harmless, not noisy."""
        output = self._run()
        self.assertIn('Nothing to carry', output)

    def test_it_is_safe_to_run_twice(self):
        self._run()
        self.assertIn('Nothing to carry', self._run())

    def test_it_never_overwrites_what_the_hub_already_has(self):
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.save()
        self._run()
        self.assertEqual(HubSettings.load().syncup_base, 'https://syncup.example.com')

    def test_a_dry_run_changes_nothing(self):
        from core.models import HubSettings
        before = HubSettings.load().syncup_base
        self._run('--dry-run')
        self.assertEqual(HubSettings.load().syncup_base, before)

    def test_it_carries_a_value_that_is_still_in_an_old_column(self):
        """Simulated with a real table shaped like the old one, since ours no longer has it."""
        from django.db import connection

        from core.models import HubSettings
        with connection.cursor() as cursor:
            cursor.execute('CREATE TABLE IF NOT EXISTS j6_settings_old '
                           '(id integer primary key, syncup_base varchar(120))')
            cursor.execute("INSERT INTO j6_settings_old (id, syncup_base) "
                           "VALUES (1, 'https://syncup.example.com')")
        with patch('core.management.commands.carry_syncup_to_hub.SOURCES',
                   [('j6_settings_old', 'J6')]):
            output = self._run()
        self.assertIn('syncup_base', output)
        self.assertEqual(HubSettings.load().syncup_base, 'https://syncup.example.com')


class PointsAtTests(TestCase):
    """One implementation of "is this address us?", shared by the hub and both modules."""

    def _hub(self, address):
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = address
        hub.save()
        return hub

    def test_this_very_site(self):
        self.assertTrue(self._hub('https://testserver').points_at('testserver'))
        self.assertTrue(self._hub('testserver').points_at('testserver'))      # no scheme typed
        self.assertTrue(self._hub('https://testserver:8000/').points_at('testserver'))

    def test_somewhere_else(self):
        self.assertFalse(self._hub('https://syncup.example.com').points_at('testserver'))

    def test_nothing_set(self):
        self.assertFalse(self._hub('').points_at('testserver'))
