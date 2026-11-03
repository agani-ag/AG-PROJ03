"""Use token: signing in to SyncHub from inside the SyncUp app.

The token is not a credential and nothing here treats it as one — it is posted to our server,
which asks SyncUp whose it is. A phone nobody is signed in on has a token of its own and it
never signs anybody in.

Four endings, and one rule behind all of them: a person must never end up with two SyncHub
accounts. Each ending has its tests below, and so do the two session rules that decide what
happens when the phone changes hands.

Nothing reaches the network; `who_is` and `fetch_people` are replaced throughout.

Run: python manage.py test core.tests_use_token
"""
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.urls import reverse

from core import auth, syncup_people
from core.models import HubSettings, Member, SyncUpRefusal

WHO = 'core.syncup_people.who_is'
A_HASH = make_password('their-syncup-password')


def person(pk='su-1', name='Anand', email='anand@example.com', phone='+919876543210',
           token='su_tok_1'):
    return {'id': pk, 'name': name, 'email': email, 'phone': phone,
            'password': A_HASH, 'token': token}


def found(p):
    return ({'success': True, 'person': p}, '')


NOBODY = ({'success': True, 'person': None, 'reason': 'phone'}, '')


class _App(TestCase):
    """A browser inside the SyncUp app, with a token on the page."""

    def setUp(self):
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.syncup_links_key = 'a-key'
        hub.save()

    def press(self, reply=None, token='su_tok_1'):
        with mock.patch(WHO, return_value=reply or found(person())):
            return self.client.post(reverse('member_token'), {'token': token})

    def mine(self, email='mine@example.com', password='my-own-password', **fields):
        row = Member.objects.create(name='Mine', email=email, **fields)
        auth.set_password(row, password)
        return row


class TheButtonTests(_App):

    def test_it_only_shows_when_the_app_gave_the_page_a_token(self):
        """A button that cannot work is worse than no button, so the markup ships hidden."""
        page = self.client.get(reverse('member_login')).content.decode()
        self.assertIn('id="token-form"', page)
        self.assertIn('hidden', page.split('id="token-form"')[1][:60])

    def test_the_button_asks_for_nothing_the_app_does_not_send(self):
        """It required `window.SyncUp.v === 1`, and the app has never set it.

        That version came from the SSO design removed from the app in October. The app
        injects `window.SyncUp = Object.assign(window.SyncUp || {}, {token: "..."})` and
        nothing else, so the test was always false and the button appeared on no phone at
        all. The screens behind it were fine; nobody could reach them.
        """
        from pathlib import Path

        from django.conf import settings

        js = (Path(settings.BASE_DIR) / 'static' / 'core' / 'member.js'
              ).read_text(encoding='utf-8')
        button = js.split('token-form', 1)[1].split('data-dismiss', 1)[0]
        # Comments only, stripped — the comment above the fix names the old check on
        # purpose, and a test that greps the prose would fail on its own explanation.
        code = '\n'.join(line.split('//')[0] for line in button.splitlines())
        self.assertNotIn('.v ===', code)
        self.assertNotIn('.v===', code)
        self.assertIn('window.SyncUp && window.SyncUp.token', code)
        # And it keeps looking: an older WebView injects after this script has run and
        # sends no event, so a single check at load time finds nothing.
        self.assertIn('setInterval', button)

    def test_a_phone_nobody_is_signed_in_on_never_signs_anybody_in(self):
        r = self.press(NOBODY)
        self.assertRedirects(r, reverse('member_login'))
        self.assertNotIn(auth.SESSION_KEY, self.client.session)
        self.assertEqual(Member.objects.count(), 0)

    def test_a_token_syncup_does_not_know(self):
        with mock.patch(WHO, return_value=(None, 'SyncUp said 404: Unknown token')):
            r = self.client.post(reverse('member_token'), {'token': 'rubbish'})
        self.assertRedirects(r, reverse('member_login'))
        self.assertEqual(Member.objects.count(), 0)

    def test_syncup_being_unreachable_does_not_make_an_account(self):
        with mock.patch(WHO, return_value=(None, 'URLError: no route')):
            r = self.client.post(reverse('member_token'), {'token': 'su_tok_1'}, follow=True)
        self.assertContains(r, 'password')
        self.assertEqual(Member.objects.count(), 0)

    def test_it_is_post_only(self):
        self.assertEqual(self.client.get(reverse('member_token')).status_code, 405)


class AlreadyLinkedTests(_App):

    def test_a_used_account_signs_straight_in(self):
        row = Member.objects.create(syncup_id='su-1', name='Anand', email='anand@example.com',
                                    syncup_unused=False)
        r = self.press()
        self.assertRedirects(r, reverse('member_apps'))
        self.assertEqual(self.client.session[auth.SESSION_KEY], row.pk)
        self.assertEqual(self.client.session[auth.VIA_KEY], 'token')

    def test_a_switched_off_account_is_refused(self):
        Member.objects.create(syncup_id='su-1', name='Anand', is_active=False)
        self.press()
        self.assertNotIn(auth.SESSION_KEY, self.client.session)

    def test_signing_in_catches_up_on_what_syncup_changed(self):
        Member.objects.create(syncup_id='su-1', name='Old Name', email='old@example.com')
        self.press(found(person(name='Anand Kumar')))
        self.assertEqual(Member.objects.get(syncup_id='su-1').name, 'Anand Kumar')


class AnOlderAccountTests(_App):
    """Somebody already here under their own account. Nothing new is made, ever."""

    def test_the_same_email_asks_for_that_account_s_password(self):
        self.mine(email='anand@example.com')
        r = self.press()
        self.assertRedirects(r, reverse('member_token_join'))
        self.assertContains(self.client.get(reverse('member_token_join')), 'Mine')
        self.assertEqual(Member.objects.count(), 1)

    def test_the_right_password_joins_the_two(self):
        older = self.mine(email='anand@example.com')
        self.press()
        r = self.client.post(reverse('member_token_join'), {'password': 'my-own-password'})
        older.refresh_from_db()
        self.assertRedirects(r, reverse('member_apps'))
        self.assertEqual(older.syncup_id, 'su-1')
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(self.client.session[auth.SESSION_KEY], older.pk)

    def test_the_wrong_password_joins_nothing(self):
        older = self.mine(email='anand@example.com')
        self.press()
        r = self.client.post(reverse('member_token_join'), {'password': 'a-guess'})
        older.refresh_from_db()
        self.assertContains(r, 'not right')
        self.assertIsNone(older.syncup_id)
        self.assertNotIn(auth.SESSION_KEY, self.client.session)

    def test_the_password_is_what_stops_a_shared_phone_being_a_takeover(self):
        """The match is a good guess. Without the password it would be an open door."""
        older = self.mine(email='other@example.com', syncup_token='su_tok_1')
        self.press()
        self.client.post(reverse('member_token_join'), {'password': ''})
        older.refresh_from_db()
        self.assertIsNone(older.syncup_id)

    def test_after_joining_the_syncup_password_is_the_one_that_works(self):
        older = self.mine(email='anand@example.com')
        self.press()
        self.client.post(reverse('member_token_join'), {'password': 'my-own-password'})
        older.refresh_from_db()
        self.assertTrue(auth.check_member_password(older, 'their-syncup-password'))
        self.assertFalse(auth.check_member_password(older, 'my-own-password'))

    def test_it_isn_t_mine_is_remembered_and_never_asked_again(self):
        older = self.mine(email='other@example.com', syncup_token='su_tok_1')
        self.press()
        r = self.client.post(reverse('member_token_join'), {'what': 'not-mine'})
        older.refresh_from_db()
        self.assertRedirects(r, reverse('member_token_first'))
        self.assertTrue(SyncUpRefusal.objects.filter(member=older, syncup_id='su-1').exists())
        self.assertEqual(older.waiting_for, '')
        # And the next sync gives them their own account rather than offering this one again.
        with mock.patch('core.syncup_people.fetch_people',
                        return_value=({'success': True, 'version': 'v2', 'people': [person()]}, '')):
            syncup_people.run()
        self.assertEqual(Member.objects.count(), 2)

    def test_only_a_same_phone_match_is_offered_the_refusal(self):
        """An email or a number is theirs; a shared phone is the only ambiguous one."""
        self.mine(email='anand@example.com')
        self.press()
        self.assertNotContains(self.client.get(reverse('member_token_join')), 'not-mine')


class NobodyKnownTests(_App):

    def test_it_asks_before_assuming(self):
        r = self.press()
        self.assertRedirects(r, reverse('member_token_first'))
        self.assertContains(self.client.get(reverse('member_token_first')),
                            'already have a SyncHub account')

    def test_i_am_new_makes_one_account_and_signs_in(self):
        self.press()
        r = self.client.post(reverse('member_token_first'), {'what': 'new'})
        self.assertRedirects(r, reverse('member_apps'))
        made = Member.objects.get(syncup_id='su-1')
        self.assertFalse(made.syncup_unused)
        self.assertEqual(Member.objects.count(), 1)

    def test_i_have_one_joins_it(self):
        older = self.mine(email='elsewhere@example.com')
        self.press()
        r = self.client.post(reverse('member_token_first'),
                             {'sign_in_id': 'elsewhere@example.com',
                              'password': 'my-own-password'})
        older.refresh_from_db()
        self.assertRedirects(r, reverse('member_apps'))
        self.assertEqual(older.syncup_id, 'su-1')
        self.assertEqual(Member.objects.count(), 1)

    def test_a_wrong_password_joins_nothing(self):
        self.mine(email='elsewhere@example.com')
        self.press()
        r = self.client.post(reverse('member_token_first'),
                             {'sign_in_id': 'elsewhere@example.com', 'password': 'no'})
        self.assertContains(r, 'do not go together')
        self.assertEqual(Member.objects.filter(syncup_id='su-1').count(), 0)

    def test_somebody_else_s_synced_account_cannot_be_claimed(self):
        """Typing another SyncUp person's details must not hand their account over."""
        theirs = Member.objects.create(syncup_id='su-9', name='Theirs',
                                       email='theirs@example.com')
        auth.set_password(theirs, 'known-password')
        self.press()
        r = self.client.post(reverse('member_token_first'),
                             {'sign_in_id': 'theirs@example.com', 'password': 'known-password'})
        theirs.refresh_from_db()
        self.assertContains(r, 'do not go together')
        self.assertEqual(theirs.syncup_id, 'su-9')

    def test_an_account_made_ahead_is_used_rather_than_doubled(self):
        made = Member.objects.create(syncup_id='su-1', name='Anand', syncup_unused=True,
                                     email='anand@example.com')
        self.press()
        self.client.post(reverse('member_token_first'), {'what': 'new'})
        made.refresh_from_db()
        self.assertFalse(made.syncup_unused)
        self.assertEqual(Member.objects.count(), 1)

    def test_an_unused_account_made_ahead_is_removed_when_they_join_an_older_one(self):
        """It is empty by definition, so nothing is lost and no duplicate is left behind."""
        Member.objects.create(syncup_id='su-1', name='Anand', syncup_unused=True)
        older = self.mine(email='elsewhere@example.com')
        self.press()
        self.client.post(reverse('member_token_first'),
                         {'sign_in_id': 'elsewhere@example.com',
                          'password': 'my-own-password'})
        older.refresh_from_db()
        self.assertEqual(older.syncup_id, 'su-1')
        self.assertEqual(Member.objects.count(), 1)

    def test_a_used_account_already_there_signs_straight_into_it(self):
        """Nothing is asked: that SyncUp person already has an account here, with things in
        it, so the button simply opens it."""
        used = Member.objects.create(syncup_id='su-1', name='Anand', syncup_unused=False)
        r = self.press()
        self.assertRedirects(r, reverse('member_apps'))
        self.assertEqual(self.client.session[auth.SESSION_KEY], used.pk)

    def test_a_used_account_appearing_mid_flow_is_not_quietly_merged(self):
        """The race: nothing was linked when they pressed the button, and by the time they
        typed a password the cron had made one and somebody had used it. Two accounts with
        things in them is an admin's decision, not a button's."""
        older = self.mine(email='elsewhere@example.com')
        self.press()
        Member.objects.create(syncup_id='su-1', name='Anand', syncup_unused=False)
        r = self.client.post(reverse('member_token_first'),
                             {'sign_in_id': 'elsewhere@example.com',
                              'password': 'my-own-password'}, follow=True)
        older.refresh_from_db()
        self.assertIsNone(older.syncup_id)
        self.assertNotIn(auth.SESSION_KEY, self.client.session)
        self.assertContains(r, 'admin joins these two')


class WhenThePhoneChangesHandsTests(_App):

    def signed_in_by_token(self):
        Member.objects.create(syncup_id='su-1', name='Anand', email='anand@example.com')
        self.press()
        return Member.objects.get(syncup_id='su-1')

    def signed_in_by_password(self):
        row = self.mine(email='own@example.com')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'own@example.com', 'password': 'my-own-password'})
        return row

    def seen(self, token, reply):
        request = self.client.request().wsgi_request
        request.session = self.client.session
        with mock.patch(WHO, return_value=reply):
            ended = syncup_people.token_changed(request, token)
        request.session.save()
        self.client.cookies['sessionid'] = request.session.session_key
        return ended

    def test_a_token_session_ends_when_syncup_signs_out(self):
        self.signed_in_by_token()
        self.assertTrue(self.seen('a-plain-phone-token', NOBODY))

    def test_a_token_session_ends_when_somebody_else_signs_in(self):
        self.signed_in_by_token()
        self.assertTrue(self.seen('su_tok_2', found(person('su-2', token='su_tok_2'))))

    def test_a_token_session_survives_its_own_token(self):
        self.signed_in_by_token()
        self.assertFalse(self.seen('su_tok_1', found(person())))

    def test_a_password_session_survives_a_signed_out_phone(self):
        """Their own account, their own password. SyncUp signing out is not their business."""
        self.signed_in_by_password()
        self.assertFalse(self.seen('a-plain-phone-token', NOBODY))

    def test_a_password_session_ends_when_a_different_linked_person_appears(self):
        """The shared-phone case, and the only thing that unseats a password session."""
        self.signed_in_by_password()
        Member.objects.create(syncup_id='su-2', name='Someone else')
        self.assertTrue(self.seen('su_tok_2', found(person('su-2', token='su_tok_2'))))

    def test_a_password_session_survives_a_syncup_person_nobody_here_knows(self):
        self.signed_in_by_password()
        self.assertFalse(self.seen('su_tok_9', found(person('su-9', token='su_tok_9'))))


class SigningUpTests(_App):

    def test_somebody_copied_from_syncup_is_told_what_to_do(self):
        Member.objects.create(syncup_id='su-1', name='Anand', email='anand@example.com')
        r = self.client.post(reverse('member_join'),
                             {'name': 'Anand', 'sign_in_id': 'anand@example.com',
                              'password': 'pw-for-tests'})
        self.assertContains(r, 'already have SyncHub through SyncUp')
        self.assertEqual(Member.objects.count(), 1)

    def test_an_ordinary_clash_still_says_the_ordinary_thing(self):
        Member.objects.create(name='Somebody', email='taken@example.com')
        r = self.client.post(reverse('member_join'),
                             {'name': 'Anand', 'sign_in_id': 'taken@example.com',
                              'password': 'pw-for-tests'})
        self.assertContains(r, 'already an account with that email')

    def test_an_address_only_syncup_has_so_far_goes_through(self):
        r = self.client.post(reverse('member_join'),
                             {'name': 'Anand', 'sign_in_id': 'anand@example.com',
                              'password': 'pw-for-tests'})
        self.assertRedirects(r, reverse('member_apps'))
        # and the next run marks it waiting rather than making a second account
        with mock.patch('core.syncup_people.fetch_people',
                        return_value=({'success': True, 'version': 'v1',
                                       'people': [person()]}, '')):
            syncup_people.run()
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(Member.objects.get().waiting_why, Member.Waiting.EMAIL)


class WhatSyncUpOwnsTests(_App):

    def test_a_synced_person_cannot_change_their_name_here(self):
        row = Member.objects.create(syncup_id='su-1', name='Anand', email='a@example.com')
        auth.set_password(row, 'pw-for-tests')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'a@example.com', 'password': 'pw-for-tests'})
        self.client.post(reverse('member_you'), {'what': 'name', 'name': 'Something Else'})
        row.refresh_from_db()
        self.assertEqual(row.name, 'Anand')

    def test_the_page_says_where_it_comes_from(self):
        row = Member.objects.create(syncup_id='su-1', name='Anand', email='a@example.com')
        auth.set_password(row, 'pw-for-tests')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'a@example.com', 'password': 'pw-for-tests'})
        r = self.client.get(reverse('member_you'))
        self.assertContains(r, 'From SyncUp')

    def test_the_theme_is_still_theirs_to_choose(self):
        row = Member.objects.create(syncup_id='su-1', name='Anand', email='a@example.com')
        auth.set_password(row, 'pw-for-tests')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'a@example.com', 'password': 'pw-for-tests'})
        self.client.post(reverse('member_you'), {'what': 'theme', 'theme': 'dark'})
        row.refresh_from_db()
        self.assertEqual(row.theme, 'dark')


class ConnectCardTests(_App):
    """Somebody using their own SyncHub account inside the app, on a phone SyncUp knows.

    The card is the only path that does not begin with Use token, so it carries the same
    safeguard by a different route: the account proves itself with its own password before
    it is handed over.
    """

    def signed_in(self, **fields):
        row = self.mine(email='own@example.com', **fields)
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'own@example.com', 'password': 'my-own-password'})
        return row

    def phone_says(self, p=None, token='su_tok_1'):
        """The token handshake noticing who is on the phone."""
        request = self.client.request().wsgi_request
        request.session = self.client.session
        with mock.patch(WHO, return_value=found(p if p is not None else person())):
            syncup_people.token_changed(request, token)
        request.session.save()
        self.client.cookies['sessionid'] = request.session.session_key

    def test_the_card_appears_once_the_phone_says_who_is_on_it(self):
        self.signed_in()
        self.assertNotContains(self.client.get(reverse('member_apps')), 'different people')
        self.phone_says()
        self.assertContains(self.client.get(reverse('member_apps')), 'different people')

    def test_the_right_password_joins_them(self):
        row = self.signed_in()
        self.phone_says()
        self.client.post(reverse('member_connect'), {'password': 'my-own-password'})
        row.refresh_from_db()
        self.assertEqual(row.syncup_id, 'su-1')
        self.assertEqual(Member.objects.count(), 1)
        self.assertTrue(auth.check_member_password(row, 'their-syncup-password'))

    def test_the_wrong_password_joins_nothing(self):
        row = self.signed_in()
        self.phone_says()
        r = self.client.post(reverse('member_connect'), {'password': 'a-guess'}, follow=True)
        row.refresh_from_db()
        self.assertIsNone(row.syncup_id)
        self.assertContains(r, 'not right')

    def test_not_now_hides_it_without_deciding_anything(self):
        row = self.signed_in()
        self.phone_says()
        self.client.post(reverse('member_connect'), {'what': 'not-now'})
        row.refresh_from_db()
        self.assertNotContains(self.client.get(reverse('member_apps')), 'different people')
        self.assertIsNone(row.syncup_id)
        self.assertFalse(SyncUpRefusal.objects.exists())

    def test_different_people_is_remembered_for_good(self):
        row = self.signed_in()
        self.phone_says()
        self.client.post(reverse('member_connect'), {'what': 'different'})
        self.assertTrue(SyncUpRefusal.objects.filter(member=row, syncup_id='su-1').exists())
        # and it does not come back, even after the phone says the same thing again
        self.phone_says()
        self.assertNotContains(self.client.get(reverse('member_apps')), 'different people')

    def test_a_refusal_clears_the_waiting_mark_the_cron_left(self):
        row = self.signed_in()
        Member.objects.filter(pk=row.pk).update(waiting_for='su-1', waiting_why='token')
        self.phone_says()
        self.client.post(reverse('member_connect'), {'what': 'different'})
        row.refresh_from_db()
        self.assertEqual(row.waiting_for, '')

    def test_somebody_else_s_account_in_use_ends_the_session_instead(self):
        """Nothing is offered, because this is the shared-phone case rather than the
        same-person one: that SyncUp account already has a SyncHub account in use, so the
        person on the phone is somebody else and the session goes."""
        self.signed_in()
        Member.objects.create(syncup_id='su-1', name='Anand', syncup_unused=False)
        self.phone_says()
        self.assertEqual(self.client.get(reverse('member_apps')).status_code, 302)

    def test_an_account_made_ahead_and_unused_does_not_block_it(self):
        self.signed_in()
        Member.objects.create(syncup_id='su-1', name='Anand', syncup_unused=True)
        self.phone_says()
        self.assertContains(self.client.get(reverse('member_apps')), 'different people')

    def test_a_synced_account_is_never_asked(self):
        row = self.mine(email='own@example.com')
        Member.objects.filter(pk=row.pk).update(syncup_id='su-9')
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'own@example.com', 'password': 'my-own-password'})
        self.phone_says()
        self.assertNotContains(self.client.get(reverse('member_apps')), 'different people')

    def test_a_phone_nobody_is_signed_in_on_offers_nothing(self):
        self.signed_in()
        request = self.client.request().wsgi_request
        request.session = self.client.session
        with mock.patch(WHO, return_value=NOBODY):
            syncup_people.token_changed(request, 'a-plain-phone-token')
        request.session.save()
        self.client.cookies['sessionid'] = request.session.session_key
        self.assertNotContains(self.client.get(reverse('member_apps')), 'different people')

    def test_posting_to_it_with_no_offer_standing_does_nothing(self):
        row = self.signed_in()
        r = self.client.post(reverse('member_connect'), {'password': 'my-own-password'})
        row.refresh_from_db()
        self.assertRedirects(r, reverse('member_apps'))
        self.assertIsNone(row.syncup_id)
