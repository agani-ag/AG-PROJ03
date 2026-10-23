"""One sign-in, one person, one device token — walked end to end, across all three modules.

The module test files each prove their own half. Nothing proved the join between them, which is
where this kind of thing actually breaks: a token written by one module and read by another, a
session started in the hub and trusted in J6, a password set in the console and typed at `/in/`.
Every test here crosses at least one of those lines on purpose.

Run: python manage.py test core.tests_one_door
"""
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from core import auth as hub
from core import push as core_push
from core.models import Member
from hibiscus.models import Person
from j6 import auth as j6_auth
from j6 import service as j6_service
from j6.models import Account as ChatAccount
from pocket import service as pocket_service

PASSWORD = 'pw-for-tests'


def member(name='Anand', sign_in_id='anand@example.com', password=PASSWORD):
    email, phone = hub.split_sign_in_id(sign_in_id)
    row = Member.objects.create(name=name, email=email or None, phone=phone or None)
    if password:
        hub.set_password(row, password)
    return row


def with_diary(row, **fields):
    return Person.objects.create(member=row, name=row.name, **fields)


def with_money(row):
    return pocket_service.join(row)


def with_chat(row, name='Bluejay'):
    account = j6_service.create_account(name)
    ChatAccount.objects.filter(pk=account.pk).update(member=row)
    account.refresh_from_db()
    return account


class OneDoorTests(TestCase):
    """Sign in once at /in/, and every module that person has started is open."""

    def setUp(self):
        self.member = member()
        self.diary = with_diary(self.member)
        self.money = with_money(self.member)
        self.chat = with_chat(self.member)

    def door(self, sign_in_id='anand@example.com', password=PASSWORD):
        return self.client.post(reverse('member_login'),
                                {'sign_in_id': sign_in_id, 'password': password})

    def test_one_password_opens_all_three(self):
        self.door()
        for name in ('hibiscus_me_home', 'pocket_tracker', 'j6_inbox'):
            self.assertEqual(self.client.get(reverse(name)).status_code, 200, name)

    def test_the_same_account_opens_with_a_phone_instead(self):
        row = member('Divya', '98765 43210')
        with_diary(row)
        self.client.post(reverse('member_login'),
                         {'sign_in_id': '9876543210', 'password': PASSWORD})
        self.assertEqual(self.client.get(reverse('hibiscus_me_home')).status_code, 200)

    def test_no_module_asks_for_a_password_of_its_own(self):
        """Every module's old sign-in address now points at the one door."""
        for name in ('hibiscus_me_login', 'pocket_login'):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302, name)
            self.assertIn(reverse('member_login'), r['Location'], name)

    def test_j6_opens_without_the_self_id_ever_being_typed(self):
        self.door()
        r = self.client.get(reverse('j6_inbox'))
        self.assertEqual(r.status_code, 200)
        # Nothing of J6's own was written: the account is found through the member.
        self.assertNotIn(j6_auth.SESSION_KEY, self.client.session)

    def test_signing_out_of_the_hub_closes_the_chat_too(self):
        """A shared phone is the whole reason. Sign out means sign out of everything."""
        self.door()
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)
        self.client.post(reverse('member_logout'))
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 302)

    def test_a_member_with_no_chat_account_is_not_handed_one(self):
        other = member('Divya', 'divya@example.com')
        with_diary(other)
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'divya@example.com', 'password': PASSWORD})
        r = self.client.get(reverse('j6_inbox'))
        self.assertEqual(r.status_code, 302)
        self.assertIn(reverse('j6_welcome'), r['Location'])

    def test_a_suspended_chat_account_is_not_opened_by_the_hub(self):
        ChatAccount.objects.filter(pk=self.chat.pk).update(is_active=False)
        self.door()
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 302)


class SigningOutOfJ6Tests(TestCase):
    """Sign out has to mean something, which is the mistake the old SyncUp sign-in made.

    It signed people quietly back in on the very next page, so Sign out was a redirect with
    extra steps. The hub can open J6 now, and must not do that.
    """

    def setUp(self):
        self.member = member()
        self.chat = with_chat(self.member)
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})

    def test_signing_out_of_j6_sticks_while_the_hub_stays_open(self):
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)
        self.client.get(reverse('j6_logout'))
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 302)
        # And still signed in to the hub — this is J6's door that closed, not the hub's.
        self.assertEqual(self.client.get(reverse('member_apps')).status_code, 200)

    def test_the_syncub_door_opens_it_again(self):
        self.client.get(reverse('j6_logout'))
        self.client.get(reverse('j6_start_hub'))
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)

    def test_a_self_id_opens_it_again(self):
        self.client.get(reverse('j6_logout'))
        self.client.post(reverse('j6_login'), {'self_id': self.chat.self_id})
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)

    def test_an_unlinked_self_id_session_outlives_a_hub_sign_out(self):
        """An anonymous chat account is somebody else as far as this server knows.

        It only shares a browser with the person signing out of the hub, so ending their
        session must not end it — that was true before the hub could open J6 and still is.
        """
        other = member('Divya', 'divya@example.com')     # no chat account of their own
        loose = j6_service.create_account('Nobody')
        fresh = Client()
        fresh.post(reverse('j6_login'), {'self_id': loose.self_id})
        fresh.post(reverse('member_login'),
                   {'sign_in_id': other.sign_in_id, 'password': PASSWORD})
        fresh.post(reverse('member_logout'))
        self.assertEqual(fresh.get(reverse('j6_inbox')).status_code, 200)

    def test_a_linked_self_id_session_does_not(self):
        """Linked means it is the same person, and a shared phone is why this matters."""
        fresh = Client()
        fresh.post(reverse('j6_login'), {'self_id': self.chat.self_id})
        fresh.post(reverse('member_login'),
                   {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        self.assertEqual(fresh.get(reverse('j6_inbox')).status_code, 200)
        fresh.post(reverse('member_logout'))
        self.assertEqual(fresh.get(reverse('j6_inbox')).status_code, 302)


class OneTokenTests(TestCase):
    """One device token per person. Registered anywhere, it works everywhere."""

    def setUp(self):
        self.member = member()
        self.diary = with_diary(self.member)
        self.money = with_money(self.member)
        self.chat = with_chat(self.member)

    def test_a_token_from_the_diary_reaches_the_other_two(self):
        core_push.remember_token(self.diary, 'tok-from-the-diary')
        self.member.refresh_from_db()
        self.assertEqual(self.member.syncup_token, 'tok-from-the-diary')
        for profile in (Person.objects.get(pk=self.diary.pk),
                        type(self.money).objects.get(pk=self.money.pk),
                        ChatAccount.objects.get(pk=self.chat.pk)):
            self.assertEqual(profile.push_token, 'tok-from-the-diary')

    def test_a_token_from_the_chat_reaches_the_diary(self):
        """J6 used to keep its own, which is why this is worth a test of its own."""
        core_push.remember_token(self.chat, 'tok-from-the-chat')
        self.member.refresh_from_db()
        self.assertEqual(self.member.syncup_token, 'tok-from-the-chat')
        self.assertEqual(Person.objects.get(pk=self.diary.pk).push_token, 'tok-from-the-chat')

    def test_the_newest_registration_wins(self):
        core_push.remember_token(self.diary, 'first')
        core_push.remember_token(self.chat, 'second')
        self.member.refresh_from_db()
        self.assertEqual(self.member.syncup_token, 'second')

    def test_an_unlinked_chat_account_keeps_its_own(self):
        """The one case where a profile's own column is still load-bearing."""
        loose = j6_service.create_account('Nobody')
        self.assertTrue(core_push.remember_token(loose, 'tok-anonymous'))
        loose.refresh_from_db()
        self.assertEqual(loose.syncup_token, 'tok-anonymous')
        self.assertEqual(loose.push_token, 'tok-anonymous')
        self.member.refresh_from_db()
        self.assertEqual(self.member.syncup_token, '')

    def test_an_empty_or_unchanged_token_is_not_written(self):
        self.assertFalse(core_push.remember_token(self.diary, ''))
        self.assertTrue(core_push.remember_token(self.diary, 'tok'))
        self.assertFalse(core_push.remember_token(self.diary, 'tok'))

    def test_the_diary_tells_them_their_phone_is_registered(self):
        """It read the profile's column while the save wrote the member's, so it always
        said no."""
        core_push.remember_token(self.chat, 'tok-from-the-chat')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        r = self.client.get(reverse('hibiscus_me_settings'))
        self.assertTrue(r.context['has_token'])

    def test_the_chat_cron_finds_a_token_held_on_the_member(self):
        from django.db.models import Q
        core_push.remember_token(self.diary, 'tok-from-the-diary')
        found = (ChatAccount.objects.filter(is_active=True, notify=True)
                 .filter(Q(member__syncup_token__gt='') | Q(syncup_token__gt='')))
        self.assertIn(self.chat, list(found))

    def test_posting_a_token_from_any_module_lands_on_the_member(self):
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        for url, body in [(reverse('hibiscus_me_token'), '{"token": "from-diary"}'),
                          (reverse('pocket_token'), '{"token": "from-pocket"}'),
                          (reverse('j6_token'), '{"token": "from-chat"}')]:
            self.client.post(url, data=body, content_type='application/json')
        self.member.refresh_from_db()
        self.assertEqual(self.member.syncup_token, 'from-chat')


class ChatMeetsTheHubTests(TestCase):
    """Somebody who came in by Self-ID, getting themselves a way back."""

    def setUp(self):
        self.chat = j6_service.create_account('Bluejay')
        self.client.post(reverse('j6_login'), {'self_id': self.chat.self_id})

    def test_the_profile_offers_both_ways_to_get_a_hub_account(self):
        r = self.client.get(reverse('j6_profile'))
        self.assertContains(r, 'Sign in to SyncHub')
        self.assertContains(r, 'Make a SyncHub account')

    def test_linking_to_an_account_they_already_have(self):
        row = member('Anand')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        self.client.post(reverse('j6_claim'))
        self.chat.refresh_from_db()
        self.assertEqual(self.chat.member, row)

    def test_signing_in_to_an_account_they_have_comes_back_to_the_chat(self):
        """The first of the two routes, and the one with no test until now — which is why a
        stray `/apps/` in a manual walk could not be told from a bug."""
        member('Anand')
        r = self.client.get(f"{reverse('j6_profile')}")
        self.assertContains(r, f"{reverse('member_login')}?next={reverse('j6_profile')}")
        r = self.client.post(reverse('member_login'),
                             {'sign_in_id': 'anand@example.com', 'password': PASSWORD,
                              'next': reverse('j6_profile')})
        self.assertRedirects(r, reverse('j6_profile'))
        self.assertContains(self.client.get(reverse('j6_profile')),
                            'Link this account to it')

    def test_making_an_account_from_the_chat_comes_back_to_the_chat(self):
        """The ?next= used to be dropped by "I am new here", landing people on /apps/."""
        r = self.client.get(f"{reverse('member_login')}?next={reverse('j6_profile')}")
        self.assertContains(r, f"{reverse('member_join')}?next=")

        r = self.client.post(reverse('member_join'),
                             {'name': 'Anand', 'sign_in_id': 'anand@example.com',
                              'password': PASSWORD, 'next': reverse('j6_profile')})
        self.assertRedirects(r, reverse('j6_profile'))

        self.client.post(reverse('j6_claim'))
        self.chat.refresh_from_db()
        self.assertIsNotNone(self.chat.member)

    def test_a_failed_sign_up_keeps_where_they_were_going(self):
        r = self.client.post(reverse('member_join'),
                             {'name': 'A', 'sign_in_id': 'anand@example.com',
                              'password': PASSWORD, 'next': reverse('j6_profile')})
        self.assertContains(r, f'value="{reverse("j6_profile")}"')

    def test_once_linked_the_hub_password_opens_the_chat(self):
        member('Anand')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        self.client.post(reverse('j6_claim'))
        self.client.post(reverse('member_logout'))     # linked, so the chat closes with it
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 302)
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)

    def test_unlinking_leaves_the_self_id_working(self):
        row = member('Anand')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        self.client.post(reverse('j6_claim'))
        self.client.post(reverse('j6_unclaim'))
        self.chat.refresh_from_db()
        self.assertIsNone(self.chat.member)
        self.client.post(reverse('member_logout'))
        self.client.post(reverse('j6_login'), {'self_id': self.chat.self_id})
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)
        self.assertTrue(Member.objects.filter(pk=row.pk).exists())

    def test_one_linked_chat_account_per_person(self):
        row = member('Anand')
        with_chat(row, 'Already')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'anand@example.com', 'password': PASSWORD})
        r = self.client.post(reverse('j6_claim'))
        self.assertEqual(r.status_code, 409)
        self.chat.refresh_from_db()
        self.assertIsNone(self.chat.member)

    def test_the_sign_in_page_offers_the_hub_door_too(self):
        self.client.get(reverse('j6_logout'))
        r = self.client.get(reverse('j6_login'))
        self.assertContains(r, 'Sign in with SyncHub')
        self.assertContains(r, reverse('j6_start_hub'))


class ConsolePeopleTests(TestCase):
    """The admin adds somebody, fixes their details, and resets their password."""

    def setUp(self):
        User.objects.create_superuser('admin', 'a@example.com', 'admin-pw')
        self.client.login(username='admin', password='admin-pw')

    def add(self, **fields):
        data = {'name': 'Divya', 'email': 'divya@example.com', 'phone': '',
                'password': PASSWORD}
        data.update(fields)
        return self.client.post(reverse('person_new'), data)

    def test_both_pages_render(self):
        row = member('Divya', 'divya@example.com')
        r = self.client.get(reverse('person_new'))
        self.assertContains(r, 'Add a person')
        self.assertContains(r, 'One of the two is required')
        r = self.client.get(reverse('person_edit', args=[row.pk]))
        self.assertContains(r, 'Edit Divya')
        self.assertContains(r, 'value="divya@example.com"')
        self.assertContains(r, 'Leave blank to keep their current password.')

    def test_somebody_added_here_can_sign_in_straight_away(self):
        self.add()
        row = Member.objects.get(email='divya@example.com')
        self.client.logout()
        r = self.client.post(reverse('member_login'),
                             {'sign_in_id': 'divya@example.com', 'password': PASSWORD})
        self.assertRedirects(r, reverse('member_apps'))
        self.assertEqual(row.created_by.username, 'admin')

    def test_a_phone_on_its_own_is_enough(self):
        self.add(email='', phone='98765 43210')
        row = Member.objects.get(name='Divya')
        self.assertEqual(row.phone, '9876543210')       # stored as the ten digits
        self.assertEqual(row.email, None)

    def test_adding_refuses_the_same_mistakes_the_public_form_does(self):
        member('Taken', 'taken@example.com')
        for fields, complaint in [
                ({'name': 'D'}, 'at least two characters'),
                ({'email': '', 'phone': ''}, 'email address or a phone'),
                ({'phone': '12345', 'email': ''}, 'looks too short'),
                ({'password': 'no'}, 'at least'),
                ({'email': 'taken@example.com'}, 'already an account')]:
            r = self.add(**fields)
            self.assertContains(r, complaint)
        self.assertEqual(Member.objects.filter(name='Divya').count(), 0)

    def test_a_person_added_here_always_has_a_password(self):
        self.add()
        self.assertTrue(Member.objects.get(name='Divya').password_hash)

    def test_editing_changes_what_they_sign_in_with(self):
        row = member('Divya', 'divya@example.com')
        self.client.post(reverse('person_edit', args=[row.pk]),
                         {'name': 'Divya R', 'email': 'new@example.com', 'phone': '',
                          'password': ''})
        row.refresh_from_db()
        self.assertEqual((row.name, row.email), ('Divya R', 'new@example.com'))
        self.client.logout()
        self.assertRedirects(self.client.post(reverse('member_login'),
                                              {'sign_in_id': 'new@example.com',
                                               'password': PASSWORD}),
                             reverse('member_apps'))

    def test_editing_may_keep_their_own_address(self):
        row = member('Divya', 'divya@example.com')
        r = self.client.post(reverse('person_edit', args=[row.pk]),
                             {'name': 'Divya', 'email': 'divya@example.com', 'phone': '',
                              'password': ''})
        self.assertRedirects(r, reverse('person', args=[row.pk]))

    def test_editing_refuses_somebody_elses_address(self):
        member('Taken', 'taken@example.com')
        row = member('Divya', 'divya@example.com')
        r = self.client.post(reverse('person_edit', args=[row.pk]),
                             {'name': 'Divya', 'email': 'taken@example.com', 'phone': '',
                              'password': ''})
        self.assertContains(r, 'already an account')
        row.refresh_from_db()
        self.assertEqual(row.email, 'divya@example.com')

    def test_editing_cannot_leave_them_with_no_way_in(self):
        row = member('Divya', 'divya@example.com')
        r = self.client.post(reverse('person_edit', args=[row.pk]),
                             {'name': 'Divya', 'email': '', 'phone': '', 'password': ''})
        self.assertContains(r, 'email address or a phone')
        row.refresh_from_db()
        self.assertEqual(row.email, 'divya@example.com')

    def test_resetting_a_password_replaces_the_old_one(self):
        row = member('Divya', 'divya@example.com')
        self.client.post(reverse('person_edit', args=[row.pk]),
                         {'name': 'Divya', 'email': 'divya@example.com', 'phone': '',
                          'password': 'a-brand-new-one'})
        row.refresh_from_db()
        self.assertTrue(hub.check_member_password(row, 'a-brand-new-one'))
        self.assertFalse(hub.check_member_password(row, PASSWORD))

    def test_a_blank_password_leaves_the_old_one_alone(self):
        row = member('Divya', 'divya@example.com')
        self.client.post(reverse('person_edit', args=[row.pk]),
                         {'name': 'Divya', 'email': 'divya@example.com', 'phone': '',
                          'password': ''})
        row.refresh_from_db()
        self.assertTrue(hub.check_member_password(row, PASSWORD))

    def test_a_reset_settles_somebody_carrying_two_passwords(self):
        """The merge leaves both hashes until one is used. Setting one clears the spare."""
        row = member('Divya', 'divya@example.com')
        row.legacy_password_hash = row.password_hash
        row.save(update_fields=['legacy_password_hash'])
        self.client.post(reverse('person_edit', args=[row.pk]),
                         {'name': 'Divya', 'email': 'divya@example.com', 'phone': '',
                          'password': 'a-brand-new-one'})
        row.refresh_from_db()
        self.assertEqual(row.legacy_password_hash, '')

    def test_both_an_email_and_a_phone_are_shown(self):
        """`sign_in_id` returns whichever exists first, so a page using it alone hides the
        other one — and both of them work."""
        row = Member.objects.create(name='Divya', email='divya@example.com',
                                    phone='9876543210')
        for url in (reverse('people'), reverse('person', args=[row.pk])):
            r = self.client.get(url)
            self.assertContains(r, 'divya@example.com', msg_prefix=url)
            self.assertContains(r, '9876543210', msg_prefix=url)

    def test_somebody_with_only_a_phone_still_shows_it(self):
        row = Member.objects.create(name='Divya', phone='9876543210')
        self.assertContains(self.client.get(reverse('person', args=[row.pk])), '9876543210')

    def test_a_person_with_neither_is_told_so_on_their_own_page(self):
        row = Member.objects.create(name='No Handles')
        r = self.client.get(reverse('person', args=[row.pk]))
        self.assertContains(r, 'a sign-in link is their only way in')
        self.assertContains(r, reverse('person_edit', args=[row.pk]))

    def test_the_people_page_flags_anybody_with_no_way_to_sign_in(self):
        Member.objects.create(name='No Handles')
        r = self.client.get(reverse('people'))
        self.assertContains(r, 'no email and no phone')
        self.assertContains(r, 'No Handles')

    def test_both_pages_need_an_admin(self):
        row = member('Divya', 'divya@example.com')
        self.client.logout()
        for url in (reverse('person_new'), reverse('person_edit', args=[row.pk])):
            self.assertEqual(self.client.get(url).status_code, 302, url)
        self.assertEqual(Member.objects.filter(name='Divya').count(), 1)
