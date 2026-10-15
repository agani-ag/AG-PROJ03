"""Tests for the shell: PIN sign-in and the cron hub.

The cron tests matter more than they look: the two endpoints are public URLs, and the whole hub's
scheduled work runs through them.
"""
from datetime import timedelta

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
