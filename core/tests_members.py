"""One account for the whole hub: the model, the sign-in, and the merge.

The merge is the part that runs once against real people, so it is tested hardest — especially
the cases the dev database cannot show you: a person reachable by email in one module and only
by phone in the other, two rows in one module that normalise to the same number, and what
happens to two passwords belonging to one person.
"""
from datetime import timedelta
from io import StringIO

from django.contrib.auth.hashers import make_password
from django.core.management import call_command
from django.test import RequestFactory, TestCase
from django.urls import reverse
from django.utils import timezone

from core import auth
from core.models import Member
from hibiscus.models import Person
from pocket import service as pocket_service
from pocket.models import Account


def diary(name='Anand', email=None, phone=None, password='diary-pw', **fields):
    return Person.objects.create(name=name, email=email, phone=phone,
                                 password_hash=make_password(password), **fields)


def money(name='Anand', sign_in_id='anand@example.com', password='money-pw'):
    """A Pocket Money account that has not been merged yet — the shape this command meets.

    Deliberately built the old way, with its own email and password on the module row and no
    member, because that is exactly what is sitting in the live database waiting to be merged.
    """
    email, phone = auth.split_sign_in_id(sign_in_id)
    account = Account.objects.create(name=name, email=email or None, phone=phone or None,
                                     password_hash=make_password(password))
    pocket_service.seed_categories(account)
    return account


def report(*args):
    out = StringIO()
    call_command('merge_identities', *args, stdout=out)
    return out.getvalue()


class MemberTests(TestCase):
    def test_the_small_conveniences(self):
        member = Member.objects.create(name='Anand Kumar', email='a@example.com')
        self.assertEqual(member.initials, 'AK')
        self.assertEqual(member.sign_in_id, 'a@example.com')
        self.assertTrue(member.can_sign_in)
        self.assertFalse(member.can_be_pushed)          # no token yet

    def test_archiving_stops_a_sign_in(self):
        member = Member.objects.create(name='Anand', is_archived=True)
        self.assertFalse(member.can_sign_in)


class PasswordTests(TestCase):
    """Two modules, two passwords, one person — and nobody locked out."""

    def setUp(self):
        self.member = Member.objects.create(name='Anand', email='a@example.com')

    def test_either_password_works_while_both_are_remembered(self):
        self.member.password_hash = make_password('money-pw')
        self.member.legacy_password_hash = make_password('diary-pw')
        self.member.save()
        self.assertTrue(auth.check_member_password(self.member, 'money-pw'))
        self.assertTrue(auth.check_member_password(self.member, 'diary-pw'))
        self.assertFalse(auth.check_member_password(self.member, 'neither'))

    def test_the_one_they_used_settles_it(self):
        self.member.password_hash = make_password('money-pw')
        self.member.legacy_password_hash = make_password('diary-pw')
        self.member.save()
        auth.settle_password(self.member, 'diary-pw')
        self.member.refresh_from_db()
        self.assertEqual(self.member.legacy_password_hash, '')
        self.assertTrue(auth.check_member_password(self.member, 'diary-pw'))
        self.assertFalse(auth.check_member_password(self.member, 'money-pw'))

    def test_setting_a_password_drops_the_spare(self):
        self.member.legacy_password_hash = make_password('old')
        self.member.save()
        auth.set_password(self.member, 'brand-new')
        self.member.refresh_from_db()
        self.assertEqual(self.member.legacy_password_hash, '')

    def test_no_password_means_no_password_sign_in(self):
        self.assertFalse(auth.check_member_password(self.member, ''))
        self.assertFalse(auth.check_member_password(self.member, 'anything'))


class SignInTests(TestCase):
    def setUp(self):
        self.member = Member.objects.create(name='Anand', email='a@example.com',
                                            phone='9876543210')
        auth.set_password(self.member, 'pw-for-tests')

    def test_found_by_either_handle_however_it_is_typed(self):
        self.assertEqual(auth.find_member('A@Example.com'), self.member)
        self.assertEqual(auth.find_member('+91 98765 43210'), self.member)
        self.assertIsNone(auth.find_member('nobody@example.com'))

    def test_a_link_lasts_an_hour_and_one_use(self):
        key = auth.new_login_key(self.member)
        self.assertEqual(auth.member_by_login_key(key), self.member)
        auth.spend_login_key(self.member)
        self.assertIsNone(auth.member_by_login_key(key))

    def test_an_old_link_is_dead(self):
        key = auth.new_login_key(self.member)
        Member.objects.filter(pk=self.member.pk).update(
            login_key_expires_at=timezone.now() - timedelta(minutes=1))
        self.assertIsNone(auth.member_by_login_key(key))

    def test_a_link_made_to_be_bookmarked_does_not_expire_or_burn(self):
        """The diary hands these out on purpose; it is the whole way its people get in."""
        key = auth.new_login_key(self.member, hours=None, one_shot=False)
        auth.spend_login_key(self.member)
        self.member.refresh_from_db()
        self.assertEqual(auth.member_by_login_key(key), self.member)
        self.assertEqual(self.member.login_key_uses, 1)

    def test_signing_in_rolls_the_session_id(self):
        """A session id somebody already knows must not survive into the signed-in session."""
        request = RequestFactory().get('/in/')
        from django.contrib.sessions.middleware import SessionMiddleware
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        before = request.session.session_key
        auth.sign_in(request, self.member)
        self.assertNotEqual(request.session.session_key, before)
        self.assertEqual(auth.current(request), self.member)

    def test_an_archived_member_is_not_current_even_mid_session(self):
        request = RequestFactory().get('/in/')
        from django.contrib.sessions.middleware import SessionMiddleware
        SessionMiddleware(lambda r: None).process_request(request)
        auth.sign_in(request, self.member)
        Member.objects.filter(pk=self.member.pk).update(is_archived=True)
        self.assertIsNone(auth.current(request))

    def test_next_never_leaves_the_site(self):
        request = RequestFactory().post('/in/', {'next': 'https://evil.example.com/'})
        request.session = {}
        self.assertEqual(auth.safe_next(request), '/apps/')
        ok = RequestFactory().post('/in/', {'next': '/pocket/tracker'})
        ok.session = {}
        self.assertEqual(auth.safe_next(ok), '/pocket/tracker')


class MergeTests(TestCase):
    """What happens to real people the one time this runs."""

    def test_the_same_email_in_both_is_one_person(self):
        diary('Anand Kumar', email='anand@example.com')
        money('Anand K', 'anand@example.com')
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(Person.objects.get().member, Account.objects.get().member)

    def test_a_phone_typed_two_ways_is_still_one_person(self):
        diary('Divya', phone='+919876543210')
        money('Divya R', '98765 43210')
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.count(), 1)

    def test_email_in_one_and_phone_in_the_other_still_joins_up(self):
        """The case a first-match rule gets wrong, and the reason for the union."""
        diary('Anand', email='anand@example.com', phone='9876543210')
        money('Anand K', '9876543210')
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.count(), 1)
        member = Member.objects.get()
        self.assertEqual(member.email, 'anand@example.com')
        self.assertEqual(member.phone, '9876543210')

    def test_two_people_who_share_nothing_stay_two(self):
        diary('Only Diary', email='diary@example.com')
        money('Only Money', 'money@example.com')
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.count(), 2)

    def test_two_rows_in_one_module_on_one_number_are_left_for_a_human(self):
        diary('Mum', phone='+919000011111')
        diary('Dad', phone='9000011111')
        text = report('--apply')
        self.assertIn('Needs a human', text)
        self.assertIn('Mum', text)
        self.assertIn('Dad', text)
        self.assertEqual(Member.objects.count(), 0)
        self.assertIsNone(Person.objects.filter(name='Mum').first().member)

    def test_somebody_with_neither_handle_gets_their_own(self):
        Person.objects.create(name='No Handles', password_hash=make_password('x'))
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.count(), 1)

    def test_both_passwords_survive_the_merge(self):
        diary('Anand', email='anand@example.com', password='diary-pw')
        money('Anand K', 'anand@example.com', 'money-pw')
        call_command('merge_identities', '--apply', stdout=StringIO())
        member = Member.objects.get()
        self.assertTrue(auth.check_member_password(member, 'diary-pw'))
        self.assertTrue(auth.check_member_password(member, 'money-pw'))

    def test_the_more_recent_profile_names_the_member(self):
        old = diary('Old Name', email='anand@example.com')
        Person.objects.filter(pk=old.pk).update(
            last_opened_at=timezone.now() - timedelta(days=30))
        new = money('New Name', 'anand@example.com')
        Account.objects.filter(pk=new.pk).update(last_opened_at=timezone.now())
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.get().name, 'New Name')

    def test_the_newest_device_token_wins(self):
        diary('Anand', email='anand@example.com')
        account = money('Anand K', 'anand@example.com')
        Account.objects.filter(pk=account.pk).update(syncup_token='newer')
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.get().syncup_token, 'newer')

    def test_a_dry_run_writes_nothing(self):
        diary('Anand', email='anand@example.com')
        money('Anand K', 'anand@example.com')
        text = report('--dry-run')
        self.assertIn('Nothing was written', text)
        self.assertEqual(Member.objects.count(), 0)

    def test_running_it_twice_changes_nothing_the_second_time(self):
        diary('Anand', email='anand@example.com')
        money('Anand K', 'anand@example.com')
        call_command('merge_identities', '--apply', stdout=StringIO())
        first = Member.objects.get().pk
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(Member.objects.get().pk, first)

    def test_one_module_at_a_time_is_allowed(self):
        diary('Anand', email='anand@example.com')
        money('Anand K', 'anand@example.com')
        call_command('merge_identities', '--apply', '--only', 'pocket', stdout=StringIO())
        self.assertEqual(Member.objects.count(), 1)
        self.assertIsNone(Person.objects.get().member)
        self.assertIsNotNone(Account.objects.get().member)

    def test_the_report_counts_what_it_is_about_to_do(self):
        diary('Anand', email='anand@example.com')
        money('Anand K', 'anand@example.com')
        diary('Alone', email='alone@example.com')
        text = report('--dry-run')
        self.assertIn('matched on email   1', text)
        self.assertIn('2 member(s) would exist afterwards', text)

    def test_a_deleted_member_takes_the_profile_with_it(self):
        diary('Anand', email='anand@example.com')
        money('Anand K', 'anand@example.com')
        call_command('merge_identities', '--apply', stdout=StringIO())
        Member.objects.get().delete()
        self.assertFalse(Person.objects.exists())
        self.assertFalse(Account.objects.exists())


class PeoplePageTests(TestCase):
    """The console page that is about a person rather than a module."""

    def setUp(self):
        from django.contrib.auth.models import User
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)

    def test_it_lists_people_and_what_they_have(self):
        diary('Anand', email='anand@example.com')
        money('Anand K', 'anand@example.com')
        call_command('merge_identities', '--apply', stdout=StringIO())
        r = self.client.get(reverse('people'))
        self.assertContains(r, 'Anand')
        self.assertContains(r, 'Hibiscus')
        self.assertContains(r, 'Pocket Money')

    def test_it_says_when_the_merge_has_not_been_run(self):
        diary('Anand', email='anand@example.com')
        r = self.client.get(reverse('people'))
        self.assertContains(r, 'no person yet')
        self.assertContains(r, 'merge_identities')

    def test_one_person_shows_how_they_sign_in(self):
        member = Member.objects.create(name='Ganesh', email='g@example.com',
                                       syncup_id='su_' + 'a' * 32)
        auth.set_password(member, 'pw-for-tests')
        r = self.client.get(reverse('person', args=[member.pk]))
        self.assertContains(r, 'Password')
        self.assertContains(r, 'SyncUp')

    def test_two_passwords_are_flagged_for_the_admin(self):
        member = Member.objects.create(name='Anand', email='a@example.com',
                                       password_hash='x', legacy_password_hash='y')
        self.assertContains(self.client.get(reverse('person', args=[member.pk])),
                            'either still works')

    def test_archiving_stops_every_module_at_once(self):
        member = Member.objects.create(name='Anand', email='a@example.com')
        self.client.post(reverse('person_action', args=[member.pk, 'archive']))
        member.refresh_from_db()
        self.assertTrue(member.is_archived)

    def test_syncup_can_be_disconnected(self):
        member = Member.objects.create(name='Anand', email='a@example.com',
                                       syncup_id='su_' + 'a' * 32)
        self.client.post(reverse('person_action', args=[member.pk, 'unlink-syncup']))
        member.refresh_from_db()
        self.assertIsNone(member.syncup_id)

    def test_the_page_needs_an_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('people')).status_code, 302)


class PublicPageTests(TestCase):
    """The three screens the hub itself shows people."""

    def test_anybody_can_make_an_account(self):
        r = self.client.post(reverse('member_join'), {
            'name': 'Anand', 'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})
        self.assertRedirects(r, reverse('member_apps'))
        self.assertTrue(Member.objects.filter(email='anand@example.com').exists())

    def test_the_sign_up_page_refuses_the_obvious_mistakes(self):
        Member.objects.create(name='Taken', email='taken@example.com')
        for data, complaint in [
                ({'name': 'A', 'sign_in_id': 'a@example.com', 'password': 'pw-for-tests'},
                 'at least two characters'),
                ({'name': 'Anand', 'sign_in_id': '', 'password': 'pw-for-tests'},
                 'email address or a phone'),
                ({'name': 'Anand', 'sign_in_id': 'a@example.com', 'password': 'no'},
                 'at least'),
                ({'name': 'Anand', 'sign_in_id': 'taken@example.com',
                  'password': 'pw-for-tests'}, 'already an account')]:
            self.assertContains(self.client.post(reverse('member_join'), data), complaint)
        self.assertEqual(Member.objects.count(), 1)

    def test_the_profile_page_opens_and_can_set_a_first_password(self):
        member = Member.objects.create(name='Ganesh', email='g@example.com',
                                       syncup_id='su_' + 'a' * 32)
        # No password yet — which is exactly the state an SSO-only account is in — so the
        # way in is a link.
        key = auth.new_login_key(member)
        self.client.get(reverse('member_magic', args=[key]))
        r = self.client.get(reverse('member_you'))
        self.assertContains(r, 'Set a password')
        self.client.post(reverse('member_you'),
                         {'what': 'password', 'new_password': 'pw-for-tests'})
        member.refresh_from_db()
        self.assertTrue(auth.check_member_password(member, 'pw-for-tests'))

    def test_changing_a_password_needs_the_old_one(self):
        member = Member.objects.create(name='Anand', email='a@example.com')
        auth.set_password(member, 'first-password')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'a@example.com', 'password': 'first-password'})
        r = self.client.post(reverse('member_you'),
                             {'what': 'password', 'current_password': 'wrong',
                              'new_password': 'second-password'}, follow=True)
        self.assertContains(r, 'not right')
        member.refresh_from_db()
        self.assertTrue(auth.check_member_password(member, 'first-password'))


class MergeKeepsTheWayInTests(TestCase):
    """The one thing this command must never do is lock somebody out on the day it runs."""

    def test_a_diary_link_still_works_afterwards(self):
        person = diary('Divya', email='d@example.com')
        from hibiscus import auth as diary_auth
        # the shape a pre-merge diary row is in: its own key, no member
        person.login_key = 'a-key-somebody-has-bookmarked'
        person.login_key_made_at = timezone.now() - timedelta(days=200)
        person.login_key_uses = 14
        person.save()

        call_command('merge_identities', '--apply', stdout=StringIO())
        member = Member.objects.get()
        self.assertEqual(member.login_key, 'a-key-somebody-has-bookmarked')
        self.assertEqual(member.login_key_uses, 14)
        self.assertEqual(auth.member_by_login_key('a-key-somebody-has-bookmarked'), member)

    def test_the_diary_link_keeps_being_the_durable_kind(self):
        """Two hundred days old and used fourteen times is normal for one of these."""
        person = diary('Divya', email='d@example.com')
        person.login_key = 'bookmarked'
        person.login_key_made_at = timezone.now() - timedelta(days=200)
        person.save()
        call_command('merge_identities', '--apply', stdout=StringIO())
        member = Member.objects.get()
        self.assertIsNone(member.login_key_expires_at, 'the diary\'s links do not expire')
        self.assertFalse(member.login_key_one_shot, 'and they are not one-shot')
        auth.spend_login_key(member)
        member.refresh_from_db()
        self.assertEqual(auth.member_by_login_key('bookmarked'), member)

    def test_a_pocket_link_keeps_its_hour(self):
        account = money('Anand K', 'anand@example.com')
        account.login_key = 'short-lived'
        account.login_key_made_at = timezone.now() - timedelta(minutes=5)
        account.save()
        call_command('merge_identities', '--apply', stdout=StringIO())
        member = Member.objects.get()
        self.assertTrue(member.login_key_one_shot)
        self.assertIsNotNone(member.login_key_expires_at)
        self.assertEqual(auth.member_by_login_key('short-lived'), member)

    def test_the_report_mentions_them(self):
        person = diary('Divya', email='d@example.com')
        person.login_key = 'bookmarked'
        person.login_key_made_at = timezone.now()
        person.save()
        self.assertIn('sign in by link', report('--dry-run'))

    def test_a_password_still_works_afterwards_too(self):
        diary('Divya', email='d@example.com', password='swan7tide')
        call_command('merge_identities', '--apply', stdout=StringIO())
        self.assertTrue(auth.check_member_password(Member.objects.get(), 'swan7tide'))


class AppsPageLinksTests(TestCase):
    """Where "Your apps" actually sends people.

    It sent them to /hibiscus/admin/ and /pocket/admin/ for a while — the console, which would
    only bounce an ordinary person to an admin sign-in they have no business seeing. The
    registry's url_name is the console landing, because that is what the sidebar needs; a
    person needs a different one.
    """

    def setUp(self):
        self.member = Member.objects.create(name='Riya', email='riya@example.com')
        auth.set_password(self.member, 'pw-for-tests')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'riya@example.com', 'password': 'pw-for-tests'})

    def test_an_app_you_are_in_opens_the_app_not_the_console(self):
        from hibiscus.models import Person
        Person.objects.create(member=self.member, name='Riya')
        body = self.client.get(reverse('member_apps')).content.decode()
        self.assertIn(f'href="{reverse("hibiscus_me_home")}"', body)
        self.assertNotIn('/hibiscus/admin/', body)

    def test_an_app_you_are_not_in_offers_the_join_page(self):
        body = self.client.get(reverse('member_apps')).content.decode()
        self.assertIn(f'href="{reverse("pocket_join")}"', body)
        self.assertIn(f'href="{reverse("j6_start_hub")}"', body)
        self.assertNotIn('/pocket/admin/', body)
        self.assertNotIn('/j6/admin/', body)

    def test_no_console_address_reaches_this_page_at_all(self):
        from pocket import service as pocket_service
        pocket_service.join(self.member)
        body = self.client.get(reverse('member_apps')).content.decode()
        self.assertNotIn('/admin/', body,
                         'nothing on a person\'s own page should point at a console')

    def test_every_live_module_says_where_a_person_goes(self):
        """A new module that forgets these would send its people to an admin sign-in."""
        from core.modules import MODULES
        for module in MODULES:
            if module.is_live:
                self.assertTrue(module.user_url_name, f'{module.key} has no user_url_name')
                self.assertTrue(module.join_url_name, f'{module.key} has no join_url_name')


class DeletingAPersonTests(TestCase):
    """Deleting from System → People takes the person and what the modules hold for them."""

    def setUp(self):
        from django.contrib.auth.models import User
        from pocket import service as pocket_service
        from hibiscus.models import Person
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.member = Member.objects.create(name='Riya', email='riya@example.com')
        Person.objects.create(member=self.member, name='Riya')
        pocket_service.join(self.member)
        self.url = reverse('person_delete', args=[self.member.pk])

    def test_the_page_says_what_would_go(self):
        r = self.client.get(reverse('person', args=[self.member.pk]))
        self.assertContains(r, 'Delete this person')
        self.assertContains(r, 'Hibiscus')
        self.assertContains(r, 'Pocket Money')
        self.assertContains(r, 'Type <b>Riya</b> to confirm', html=False)

    def test_the_name_has_to_be_typed(self):
        from hibiscus.models import Person
        r = self.client.post(self.url, {'confirm': 'riy'}, follow=True)
        self.assertContains(r, 'Type the name exactly')
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())
        self.assertTrue(Person.objects.exists())

    def test_the_right_name_takes_the_person_and_both_modules(self):
        from hibiscus.models import Person
        from pocket.models import Account
        r = self.client.post(self.url, {'confirm': '  riya '}, follow=True)
        self.assertContains(r, 'Riya is gone')
        self.assertFalse(Member.objects.filter(pk=self.member.pk).exists())
        self.assertFalse(Person.objects.exists())
        self.assertFalse(Account.objects.exists())

    def test_a_chat_account_survives_unless_it_is_asked_for(self):
        """It is anonymous and its Self-ID is the real credential, so it is not assumed."""
        from j6 import service as j6_service
        from j6.models import Account as Chat
        chat = j6_service.create_account('GANNY')
        Chat.objects.filter(pk=chat.pk).update(member=self.member)
        self.client.post(self.url, {'confirm': 'Riya'}, follow=True)
        chat.refresh_from_db()
        self.assertIsNone(chat.member, 'anonymous again, and still reachable by its Self-ID')
        self.assertTrue(Chat.objects.filter(pk=chat.pk).exists())

    def test_but_it_goes_when_it_is(self):
        from j6 import service as j6_service
        from j6.models import Account as Chat
        chat = j6_service.create_account('GANNY')
        Chat.objects.filter(pk=chat.pk).update(member=self.member)
        r = self.client.post(self.url, {'confirm': 'Riya', 'with_chat': '1'}, follow=True)
        self.assertFalse(Chat.objects.filter(pk=chat.pk).exists())
        self.assertContains(r, 'with their chat account')

    def test_the_page_warns_that_the_chat_is_separate(self):
        from j6 import service as j6_service
        from j6.models import Account as Chat
        chat = j6_service.create_account('GANNY')
        Chat.objects.filter(pk=chat.pk).update(member=self.member)
        r = self.client.get(reverse('person', args=[self.member.pk]))
        self.assertContains(r, 'chat account is separate')
        self.assertContains(r, 'GANNY')

    def test_only_an_admin_can(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {'confirm': 'Riya'}).status_code, 302)
        self.assertTrue(Member.objects.filter(pk=self.member.pk).exists())

    def test_it_is_post_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
