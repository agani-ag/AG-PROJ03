"""Continue with SyncUp — SyncHub's half.

SyncUp's own half does not exist yet, so everything here runs against a fake standing in for
`POST /app/v1/hub/sso/check`, shaped exactly as AG-PROJ04 specified it. What the tests are
really pinning is the judgement, not the transport: that an unverified email never opens
somebody else's account, that the button is absent until a key is set, and that nobody can
strand themselves in an account only the app can reach.
"""
import json
import urllib.error
from io import BytesIO
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse

from core import auth, syncup_sso
from core.models import HubSettings, Member

USER = {'id': 'su_' + 'a' * 32, 'name': 'Ganesh', 'email': 'ganesh@example.com',
        'phone': '9876543210'}


class FakeSyncUp:
    """Stands in for SyncUp's SSO endpoint and keeps what was posted."""

    def __init__(self, user=None, status=None, reason='', success=True):
        self.user, self.status, self.reason, self.success = user or USER, status, reason, success
        self.payload = None

    def __call__(self, request, timeout=None):
        self.payload = json.loads(request.data)
        if self.status:
            body = json.dumps({'success': False, 'error': 'no', 'reason': self.reason}).encode()
            raise urllib.error.HTTPError(request.full_url, self.status, 'no', {}, BytesIO(body))
        body = ({'success': True, 'user': self.user} if self.success
                else {'success': False, 'error': 'refused', 'reason': self.reason})
        fake = json.dumps(body).encode()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return fake

        return Response()


def configure(sso_key='sso-key'):
    hub = HubSettings.load()
    hub.syncup_base = 'https://syncup.example.com'
    hub.site_base = 'https://synchub.example.com'
    hub.syncup_sso_key = sso_key
    hub.save()
    return hub


class ConfigurationTests(TestCase):
    def test_nothing_is_offered_until_a_key_exists(self):
        configure(sso_key='')
        self.assertFalse(syncup_sso.is_configured())
        page = self.client.get(reverse('member_login'))
        self.assertNotContains(page, 'data-sso-url')

    def test_the_button_appears_once_it_does(self):
        configure()
        self.assertTrue(syncup_sso.is_configured())
        self.assertContains(self.client.get(reverse('member_login')), 'Continue with SyncUp')

    def test_posting_a_code_with_no_key_is_refused(self):
        configure(sso_key='')
        r = self.client.post(reverse('member_sso'), {'code': 'anything'}, follow=True)
        self.assertContains(r, 'not set up')
        self.assertFalse(Member.objects.exists())

    def test_the_host_comes_from_the_site_address(self):
        configure()
        self.assertEqual(syncup_sso.host_for(), 'synchub.example.com')


class CheckTests(TestCase):
    def setUp(self):
        configure()

    def test_a_good_code_gives_back_a_person(self):
        fake = FakeSyncUp()
        with patch('core.syncup_sso.urllib.request.urlopen', fake):
            user, error = syncup_sso.check('code-123')
        self.assertEqual(error, '')
        self.assertEqual(user['id'], USER['id'])
        self.assertEqual(fake.payload['key'], 'sso-key')
        self.assertEqual(fake.payload['code'], 'code-123')
        self.assertEqual(fake.payload['host'], 'synchub.example.com')

    def test_the_phone_is_normalised_on_the_way_in(self):
        fake = FakeSyncUp(user={**USER, 'phone': '+91 98765 43210'})
        with patch('core.syncup_sso.urllib.request.urlopen', fake):
            user, _ = syncup_sso.check('code')
        self.assertEqual(user['phone'], '9876543210')

    def test_each_refusal_says_something_a_person_can_act_on(self):
        for status, reason, expected in [(400, 'expired', 'took too long'),
                                         (400, 'bad_code', 'already used'),
                                         (400, 'wrong_host', 'different site'),
                                         (403, 'bad_key', 'disagree about the sign-in key'),
                                         (429, 'slow_down', 'Too many tries')]:
            with patch('core.syncup_sso.urllib.request.urlopen',
                       FakeSyncUp(status=status, reason=reason)):
                user, error = syncup_sso.check('code')
            self.assertIsNone(user)
            self.assertIn(expected, error)

    def test_an_unreachable_syncup_is_not_an_exception(self):
        def boom(request, timeout=None):
            raise urllib.error.URLError('no route')
        with patch('core.syncup_sso.urllib.request.urlopen', boom):
            user, error = syncup_sso.check('code')
        self.assertIsNone(user)
        self.assertIn('Could not reach SyncUp', error)


class ResolveTests(TestCase):
    """Who a SyncUp person turns out to be — the judgement that matters."""

    def test_a_known_syncup_account_is_just_signed_in(self):
        member = Member.objects.create(name='Ganesh', syncup_id=USER['id'])
        self.assertEqual(syncup_sso.resolve(USER), ('linked', member))

    def test_a_matching_email_is_asked_to_prove_it_not_let_in(self):
        member = Member.objects.create(name='Ganesh', email='ganesh@example.com')
        outcome, found = syncup_sso.resolve(USER)
        self.assertEqual(outcome, 'prove')
        self.assertEqual(found, member)
        found.refresh_from_db()
        self.assertIsNone(found.syncup_id, 'nothing may be linked before the password')

    def test_a_matching_phone_is_treated_the_same_way(self):
        Member.objects.create(name='Ganesh', phone='9876543210')
        self.assertEqual(syncup_sso.resolve(USER)[0], 'prove')

    def test_a_stranger_is_new(self):
        self.assertEqual(syncup_sso.resolve(USER), ('new', None))


class SignInTests(TestCase):
    def setUp(self):
        configure()
        self.fake = FakeSyncUp()

    def _post(self, **extra):
        with patch('core.syncup_sso.urllib.request.urlopen', self.fake):
            return self.client.post(reverse('member_sso'), {'code': 'c', **extra}, follow=True)

    def test_a_first_sign_in_makes_an_account_and_lands_on_apps(self):
        r = self._post()
        member = Member.objects.get()
        self.assertEqual(member.syncup_id, USER['id'])
        self.assertEqual(member.email, 'ganesh@example.com')
        self.assertEqual(member.password_hash, '')
        self.assertContains(r, 'Your apps')

    def test_the_password_nudge_stays_until_there_is_one(self):
        self._post()
        self.assertContains(self.client.get(reverse('member_apps')), 'Add a password?')
        member = Member.objects.get()
        auth.set_password(member, 'pw-for-tests')
        self.assertNotContains(self.client.get(reverse('member_apps')), 'Add a password?')

    def test_coming_back_signs_in_without_making_another(self):
        self._post()
        self.client.post(reverse('member_logout'))
        self._post()
        self.assertEqual(Member.objects.count(), 1)

    def test_a_session_started_this_way_says_so(self):
        self._post()
        self.assertEqual(self.client.session.get(auth.VIA_KEY), 'syncup')

    def test_next_is_honoured(self):
        r = self._post(next='/pocket/tracker')
        # The first hop is what this view decides. Where Pocket sends them after that is
        # Pocket's business, and stops being a second sign-in once it is on the shared session.
        self.assertEqual(r.redirect_chain[0][0], '/pocket/tracker')

    def test_an_archived_member_is_not_let_in(self):
        Member.objects.create(name='Ganesh', syncup_id=USER['id'], is_archived=True)
        r = self._post()
        self.assertContains(r, 'closed here')


class ProveTests(TestCase):
    """The branch where somebody already has that address."""

    def setUp(self):
        configure()
        self.member = Member.objects.create(name='Ganesh', email='ganesh@example.com')
        auth.set_password(self.member, 'pw-for-tests')
        with patch('core.syncup_sso.urllib.request.urlopen', FakeSyncUp()):
            self.client.post(reverse('member_sso'), {'code': 'c'})

    def test_it_asks_rather_than_letting_them_in(self):
        r = self.client.get(reverse('member_prove'))
        self.assertContains(r, 'already know that address')
        self.assertContains(r, 'does not check that an email address really belongs')
        self.assertIsNone(self.client.session.get(auth.SESSION_KEY))

    def test_the_right_password_joins_the_two(self):
        self.client.post(reverse('member_prove'), {'password': 'pw-for-tests'})
        self.member.refresh_from_db()
        self.assertEqual(self.member.syncup_id, USER['id'])
        self.assertEqual(self.client.session.get(auth.SESSION_KEY), self.member.pk)

    def test_the_wrong_password_joins_nothing(self):
        r = self.client.post(reverse('member_prove'), {'password': 'guess'})
        self.member.refresh_from_db()
        self.assertIsNone(self.member.syncup_id)
        self.assertContains(r, 'not right')

    def test_not_my_account_asks_how_they_will_get_back_in(self):
        r = self.client.post(reverse('member_prove'), {'what': 'separate'}, follow=True)
        self.assertContains(r, 'How will you get back in?')
        self.assertEqual(Member.objects.count(), 1, 'nothing is created until it is reachable')

    def test_a_separate_account_needs_an_identifier_and_a_password(self):
        self.client.post(reverse('member_prove'), {'what': 'separate'})
        for data, complaint in [({'password': 'pw-for-tests'}, 'email address or a phone'),
                                ({'sign_in_id': '9000011111', 'password': 'no'}, 'at least'),
                                ({'sign_in_id': 'ganesh@example.com',
                                  'password': 'pw-for-tests'}, 'already uses that email')]:
            r = self.client.post(reverse('member_separate'), data)
            self.assertContains(r, complaint)
            self.assertEqual(Member.objects.count(), 1)

    def test_and_then_it_can_actually_be_signed_into(self):
        """The whole point: a browser, with no SyncUp anywhere near it."""
        self.client.post(reverse('member_prove'), {'what': 'separate'})
        self.client.post(reverse('member_separate'),
                         {'sign_in_id': '9000011111', 'password': 'pw-for-tests'})
        fresh = Member.objects.exclude(pk=self.member.pk).get()
        self.assertIsNone(fresh.email, 'the address belongs to the other account')
        self.assertEqual(fresh.phone, '9000011111')
        self.assertEqual(fresh.syncup_id, USER['id'])

        self.client.post(reverse('member_logout'))
        r = self.client.post(reverse('member_login'),
                             {'sign_in_id': '9000011111', 'password': 'pw-for-tests'})
        self.assertRedirects(r, reverse('member_apps'))

    def test_the_pending_sign_in_does_not_outlive_its_use(self):
        self.client.post(reverse('member_prove'), {'password': 'pw-for-tests'})
        self.assertNotIn('synchub_sso_pending', self.client.session)
        stale = self.client.get(reverse('member_prove'))
        self.assertEqual(stale['Location'], reverse('member_login'))


class SignOutStaysOutTests(TestCase):
    """Pressing Sign out has to mean something while SyncUp is still signed in.

    The quiet hand-off fires on page load, so without a marker it signs the person straight
    back in and sign-out becomes a redirect with extra steps.
    """

    def setUp(self):
        configure()
        with patch('core.syncup_sso.urllib.request.urlopen', FakeSyncUp()):
            self.client.post(reverse('member_sso'), {'code': 'c'})

    def test_signing_out_forbids_the_quiet_sign_in(self):
        self.assertContains(Client().get(reverse('member_login')), 'data-auto-sso',
                            msg_prefix='a visitor who has not signed out is allowed one')

        self.client.post(reverse('member_logout'))
        page = self.client.get(reverse('member_login'))
        self.assertNotContains(page, 'data-auto-sso')       # and not after
        self.assertIsNone(self.client.session.get(auth.SESSION_KEY))

    def test_the_button_is_still_offered_so_they_can_choose_to_return(self):
        self.client.post(reverse('member_logout'))
        self.assertContains(self.client.get(reverse('member_login')),
                            'Continue with SyncUp')

    def test_signing_in_on_purpose_lifts_it(self):
        self.client.post(reverse('member_logout'))
        with patch('core.syncup_sso.urllib.request.urlopen', FakeSyncUp()):
            self.client.post(reverse('member_sso'), {'code': 'c2'})
        self.client.post(reverse('member_logout'))   # out again, to read the page fresh
        self.client.cookies.pop('synchub_no_auto', None)
        self.assertContains(self.client.get(reverse('member_login')), 'data-auto-sso')

    def test_a_password_sign_in_lifts_it_too(self):
        member = Member.objects.get()
        auth.set_password(member, 'pw-for-tests')
        member.email = 'ganesh@example.com'
        member.save(update_fields=['email'])
        self.client.post(reverse('member_logout'))
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'ganesh@example.com', 'password': 'pw-for-tests'})
        self.assertNotIn('synchub_no_auto', [c for c in self.client.cookies
                                             if self.client.cookies[c].value])

    def test_the_marker_is_not_readable_by_the_page(self):
        """httponly, so a script cannot set or clear it to override somebody's choice."""
        self.client.post(reverse('member_logout'))
        self.assertTrue(self.client.cookies['synchub_no_auto']['httponly'])


class ScriptOrderTests(TestCase):
    """A guard for the one mistake this file keeps inviting.

    There is no JavaScript harness here, so this reads the script as text. Crude, but it would
    have caught the real bug it is named after: two early returns were added above the line
    that attaches the click handler, which left a *visible* button doing nothing — right after
    Sign out, and when SyncUp signed in while the page was open.
    """

    def _script(self):
        from pathlib import Path

        from django.conf import settings
        return (Path(settings.BASE_DIR) / 'static' / 'core' / 'member.js').read_text(
            encoding='utf-8')

    def test_the_click_handler_is_wired_before_any_early_return(self):
        body = self._script()
        click = body.index('button.addEventListener("click"')
        for guard in ('if (!signedIn()) { return; }',
                      'if (document.body.dataset.autoSso !== "1")'):
            self.assertLess(click, body.index(guard),
                            f'{guard!r} sits above the click handler, so a visible button '
                            f'would do nothing')

    def test_a_quiet_sign_in_is_never_attempted_without_permission(self):
        body = self._script()
        silent = body.index('signIn({ silent: true })')
        self.assertLess(body.index('dataset.autoSso !== "1"'), silent,
                        'the silent attempt must sit below the sign-out gate')
        self.assertLess(body.index('if (!signedIn()) { return; }'), silent)


class SettingsPageTests(TestCase):
    """The key has to be typeable. It was in the model and read by the code, with no input."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)

    def test_the_sign_in_key_has_a_field_on_the_system_page(self):
        r = self.client.get(reverse('system_settings'))
        self.assertContains(r, 'SyncUp sign-in key')
        self.assertContains(r, 'name="syncup_sso_key"')

    def test_pasting_it_saves_and_switches_sso_on(self):
        from core.models import HubSettings
        self.assertFalse(syncup_sso.is_configured())
        self.client.post(reverse('system_settings'), {
            'site_base': 'https://synchub.example.com',
            'syncup_base': 'https://syncup.example.com',
            'syncup_notify_key': '', 'syncup_links_key': '',
            'syncup_sso_key': 'a-real-key-from-syncup'})
        self.assertEqual(HubSettings.load().syncup_sso_key, 'a-real-key-from-syncup')
        self.assertTrue(syncup_sso.is_configured())

    def test_and_the_button_then_appears_on_the_sign_in_page(self):
        configure()
        self.client.logout()
        self.assertContains(self.client.get(reverse('member_login')), 'data-sso-url')
