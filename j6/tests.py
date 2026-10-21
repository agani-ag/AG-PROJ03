"""J6 — the server side.

The identity decisions are the ones worth pinning: two IDs, a required name, no recovery. After
that it is the rules in `service` — who may see what, what counts as read, what the caps refuse —
because the browser and the desktop clients both go through them and must never disagree.
"""
import json
import time
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core import cronhub
from core.models import HubSettings

from . import service
from .cron import push, retention
from .models import Account, Chat, Member, Message, Settings, new_account_id, new_self_id


def make(name='Anand'):
    return service.create_account(name)


class FakeSyncUp:
    """Stands in for SyncUp's partner endpoint and keeps what was posted to it."""

    def __init__(self, delivered=1, raises=None):
        self.delivered = delivered
        self.raises = raises
        self.url = None
        self.payload = None

    def __call__(self, request, timeout=None):
        self.url = request.full_url
        self.payload = json.loads(request.data)
        if self.raises:
            raise self.raises
        fake = self

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({'delivered': fake.delivered}).encode()

        return Response()


def _ctx(kind, seconds=8):
    """A hook context with a real deadline.

    `Context('tick', 8)` looks right and is not: the second argument is `now`, and `deadline`
    defaults to 0 — so the hook would believe it was already out of time and do nothing.
    """
    return cronhub.Context(kind, deadline=time.monotonic() + seconds)


class IdTests(TestCase):
    """Two IDs, both permanent, neither guessable."""

    def test_an_account_id_is_short_enough_to_share(self):
        value = new_account_id()
        self.assertTrue(value.startswith('j6-'))
        self.assertEqual(len(value), 22)                      # j6- + 4 groups of 4 + 3 dashes

    def test_a_self_id_is_longer_because_it_is_the_credential(self):
        value = new_self_id()
        self.assertTrue(value.startswith('j6s-'))
        self.assertGreater(len(value), len(new_account_id()))

    def test_ids_do_not_repeat(self):
        self.assertEqual(len({new_account_id() for _ in range(200)}), 200)
        self.assertEqual(len({new_self_id() for _ in range(200)}), 200)

    def test_no_look_alike_characters(self):
        for value in (new_account_id(), new_self_id()):
            for bad in 'ilo01':
                self.assertNotIn(bad, value.split('-', 1)[1])

    def test_both_are_given_at_creation_and_never_change(self):
        account = make()
        before = (account.account_id, account.self_id)
        service.rename(account, 'Anand K')
        account.refresh_from_db()
        self.assertEqual((account.account_id, account.self_id), before)


class NameTests(TestCase):
    """A name is required, so no screen ever has to show an ID where a person should be."""

    def test_two_letters_is_enough(self):
        self.assertEqual(make('Mi').name, 'MI')

    def test_one_letter_is_not(self):
        with self.assertRaises(ValidationError):
            make('M')

    def test_blank_is_not(self):
        with self.assertRaises(ValidationError):
            make('   ')

    def test_it_is_a_label_not_an_identity(self):
        one, two = make('Riya'), make('Riya')
        self.assertNotEqual(one.account_id, two.account_id)   # same name, different people

    def test_initials_come_from_the_name(self):
        self.assertEqual(make('Anand Kumar').initials, 'AK')
        self.assertEqual(make('Mani').initials, 'MA')

    def test_colour_is_stable_for_an_account(self):
        account = make()
        self.assertEqual(account.colour, Account.objects.get(pk=account.pk).colour)


class SignUpTests(TestCase):
    def test_the_welcome_screen_makes_an_account(self):
        r = self.client.post(reverse('j6_welcome'), {'name': 'Anand'}, follow=True)
        self.assertEqual(r.status_code, 200)
        account = Account.objects.get()
        self.assertContains(r, account.account_id)
        self.assertContains(r, account.self_id)               # shown once, on purpose

    def test_a_short_name_is_refused_with_a_reason(self):
        r = self.client.post(reverse('j6_welcome'), {'name': 'M'})
        self.assertContains(r, 'at least 2 characters')
        self.assertEqual(Account.objects.count(), 0)

    def test_closing_sign_ups_closes_them_everywhere(self):
        config = Settings.load()
        config.signup_open = False
        config.save()
        self.assertContains(self.client.get(reverse('j6_welcome')), 'New accounts are closed')
        r = self.client.post(reverse('j6_api_start'), data=json.dumps({'name': 'Mani'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Account.objects.count(), 0)


class SignInTests(TestCase):
    def setUp(self):
        self.account = make()

    def test_the_self_id_signs_you_in(self):
        r = self.client.post(reverse('j6_login'), {'self_id': self.account.self_id})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)

    def test_a_wrong_self_id_does_not(self):
        r = self.client.post(reverse('j6_login'), {'self_id': 'j6s-nope'})
        self.assertContains(r, 'does not work')

    def test_the_one_tap_link_signs_in_and_redirects(self):
        r = self.client.get(reverse('j6_magic', args=[self.account.self_id]))
        self.assertEqual(r.status_code, 302)
        self.assertNotIn(self.account.self_id, r['Location'])  # the key leaves the address bar
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)

    def test_a_suspended_account_cannot_get_in_by_any_door(self):
        self.account.is_active = False
        self.account.save()
        self.client.post(reverse('j6_login'), {'self_id': self.account.self_id})
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 302)
        r = self.client.post(reverse('j6_api_hello'), HTTP_X_J6_SELF=self.account.self_id)
        self.assertEqual(r.status_code, 401)

    def test_suspending_somebody_stops_them_at_once(self):
        self.client.post(reverse('j6_login'), {'self_id': self.account.self_id})
        self.account.is_active = False
        self.account.save()
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 302)

    def test_the_self_id_is_readable_by_its_owner_for_ever(self):
        self.client.post(reverse('j6_login'), {'self_id': self.account.self_id})
        r = self.client.get(reverse('j6_profile'))
        self.assertContains(r, self.account.self_id)          # masked in the page, present in it


class ChatTests(TestCase):
    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')

    def test_a_chat_is_the_pair_whichever_way_round(self):
        one, created = service.start_chat(self.anand, self.mani.account_id)
        self.assertTrue(created)
        two, created_again = service.start_chat(self.mani, self.anand.account_id)
        self.assertFalse(created_again)
        self.assertEqual(one.pk, two.pk)
        self.assertEqual(Chat.objects.count(), 1)

    def test_an_unknown_id_creates_nothing(self):
        with self.assertRaises(ValidationError):
            service.start_chat(self.anand, 'j6-aaaa-bbbb-cccc-dddd')
        self.assertEqual(Chat.objects.count(), 0)

    def test_you_cannot_chat_with_yourself(self):
        with self.assertRaises(ValidationError):
            service.start_chat(self.anand, self.anand.account_id)

    def test_both_sides_get_a_member_row(self):
        chat, _ = service.start_chat(self.anand, self.mani.account_id)
        self.assertEqual(chat.members.count(), 2)

    def test_seq_counts_per_chat_not_globally(self):
        chat, _ = service.start_chat(self.anand, self.mani.account_id)
        other, _ = service.start_chat(self.anand, make('Riya').account_id)
        service.send(self.anand, chat, 'one')
        first = service.send(self.anand, other, 'one elsewhere')
        self.assertEqual(first.seq, 1)                        # a fresh chat starts at 1 again


class MessageTests(TestCase):
    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)

    def test_sending_and_reading(self):
        service.send(self.anand, self.chat, 'morning')
        thread = service.thread(self.mani, self.chat)
        self.assertEqual(thread['messages'][0]['body'], 'morning')
        self.assertFalse(thread['messages'][0]['mine'])
        self.assertEqual(service.chat_rows(self.mani)[0]['unread'], 1)
        service.mark_read(self.mani, self.chat)
        self.assertEqual(service.chat_rows(self.mani)[0]['unread'], 0)

    def test_handing_a_message_over_is_what_makes_it_delivered(self):
        message = service.send(self.anand, self.chat, 'hello')
        self.assertIsNone(message.delivered_at)
        service.thread(self.mani, self.chat)                  # their client collects it
        message.refresh_from_db()
        self.assertIsNotNone(message.delivered_at)

    def test_sending_counts_as_reading_your_own_side(self):
        service.send(self.mani, self.chat, 'hi')
        service.send(self.anand, self.chat, 'hi back')
        self.assertEqual(service.chat_rows(self.anand)[0]['unread'], 0)

    def test_a_read_mark_never_goes_backwards(self):
        service.send(self.anand, self.chat, 'one')
        service.send(self.anand, self.chat, 'two')
        service.mark_read(self.mani, self.chat, 2)
        service.mark_read(self.mani, self.chat, 1)
        self.assertEqual(Member.objects.get(chat=self.chat, account=self.mani).last_read_seq, 2)

    def test_an_empty_message_is_refused(self):
        with self.assertRaises(ValidationError):
            service.send(self.anand, self.chat, '   ')

    def test_a_message_longer_than_the_cap_is_refused(self):
        config = Settings.load()
        with self.assertRaises(ValidationError):
            service.send(self.anand, self.chat, 'x' * (config.max_chars + 1))

    def test_the_rate_limit_bites(self):
        config = Settings.load()
        config.msgs_per_minute = 3
        config.save()
        for i in range(3):
            service.send(self.anand, self.chat, f'{i}')
        with self.assertRaises(ValidationError):
            service.send(self.anand, self.chat, 'one too many')

    def test_the_new_chat_limit_bites(self):
        config = Settings.load()
        config.new_chats_per_hour = 1
        config.save()
        with self.assertRaises(ValidationError):
            service.start_chat(self.anand, make('Riya').account_id)


class BlockingTests(TestCase):
    """Blocking is one-sided and silent: you stop seeing them, they are told nothing."""

    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        service.send(self.mani, self.chat, 'hello?')
        service.set_blocked(self.anand, self.chat, True)

    def test_the_blocker_stops_seeing_them(self):
        thread = service.thread(self.anand, self.chat)
        self.assertEqual(thread['messages'], [])
        self.assertTrue(thread['blocked'])

    def test_the_blocked_side_is_told_nothing(self):
        thread = service.thread(self.mani, self.chat)
        self.assertFalse(thread['blocked'])
        self.assertEqual(len(thread['messages']), 1)

    def test_a_blocked_chat_shows_no_unread(self):
        service.send(self.mani, self.chat, 'still here')
        self.assertEqual(service.chat_rows(self.anand)[0]['unread'], 0)
        self.assertTrue(service.chat_rows(self.anand)[0]['blocked'])

    def test_unblocking_brings_it_back(self):
        service.set_blocked(self.anand, self.chat, False)
        self.assertEqual(len(service.thread(self.anand, self.chat)['messages']), 1)


class PollTests(TestCase):
    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        self.client.post(reverse('j6_login'), {'self_id': self.anand.self_id})

    def test_a_quiet_poll_answers_304_with_no_body(self):
        first = self.client.get(reverse('j6_poll') + f'?chat={self.chat.pk}&since=0')
        self.assertEqual(first.status_code, 200)
        again = self.client.get(reverse('j6_poll') + f'?chat={self.chat.pk}&since=0',
                                HTTP_IF_NONE_MATCH=first['ETag'])
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b'')

    def test_a_new_message_changes_the_answer(self):
        first = self.client.get(reverse('j6_poll') + f'?chat={self.chat.pk}&since=0')
        service.send(self.mani, self.chat, 'something new')
        again = self.client.get(reverse('j6_poll') + f'?chat={self.chat.pk}&since=0',
                                HTTP_IF_NONE_MATCH=first['ETag'])
        self.assertEqual(again.status_code, 200)
        self.assertEqual(again.json()['thread']['messages'][0]['body'], 'something new')

    def test_a_visible_tab_marks_the_thread_read(self):
        service.send(self.mani, self.chat, 'unread')
        self.client.get(reverse('j6_poll') + f'?chat={self.chat.pk}&since=0&seen=1')
        self.assertEqual(service.chat_rows(self.anand)[0]['unread'], 0)

    def test_polling_without_seen_leaves_it_unread(self):
        service.send(self.mani, self.chat, 'unread')
        self.client.get(reverse('j6_poll') + f'?chat={self.chat.pk}&since=0')
        self.assertEqual(service.chat_rows(self.anand)[0]['unread'], 1)

    def test_somebody_elses_chat_is_a_404(self):
        theirs, _ = service.start_chat(make('Riya'), make('Karthik').account_id)
        r = self.client.get(reverse('j6_poll') + f'?chat={theirs.pk}&since=0')
        self.assertEqual(r.status_code, 404)

    def test_typing_expires_on_its_own(self):
        service.set_typing(self.mani, self.chat)
        self.assertTrue(service.thread(self.anand, self.chat)['typing'])
        member = Member.objects.get(chat=self.chat, account=self.mani)
        member.typing_until = timezone.now() - timedelta(seconds=1)
        member.save()
        self.assertFalse(service.thread(self.anand, self.chat)['typing'])


class ClientApiTests(TestCase):
    """The token API: what the Windows app and the terminal client speak."""

    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)

    def _post(self, name, data=None, who=None):
        return self.client.post(reverse(name), data=json.dumps(data or {}),
                                content_type='application/json',
                                HTTP_X_J6_SELF=(who or self.anand).self_id)

    def test_start_makes_an_account_from_a_client(self):
        r = self.client.post(reverse('j6_api_start'), data=json.dumps({'name': 'Riya'}),
                             content_type='application/json')
        self.assertEqual(r.status_code, 201)
        body = r.json()
        self.assertTrue(body['account_id'].startswith('j6-'))
        self.assertTrue(body['self_id'].startswith('j6s-'))
        self.assertTrue(Account.objects.filter(name='RIYA').exists())

    def test_hello_says_who_you_are_and_what_you_have(self):
        body = self._post('j6_api_hello').json()
        self.assertEqual(body['name'], 'ANAND')
        self.assertEqual(body['chats'][0]['name'], 'MANI')
        self.assertIn('media', body)

    def test_no_token_means_401(self):
        r = self.client.post(reverse('j6_api_hello'))
        self.assertEqual(r.status_code, 401)

    def test_a_client_can_send_and_the_browser_sees_it(self):
        r = self._post('j6_api_send', {'chat': self.chat.pk, 'body': 'from the exe'})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(service.thread(self.mani, self.chat)['messages'][0]['body'],
                         'from the exe')

    def test_a_client_can_start_a_chat(self):
        riya = make('Riya')
        r = self._post('j6_api_new', {'account_id': riya.account_id})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()['name'], 'RIYA')

    def test_a_client_can_move_its_read_mark(self):
        service.send(self.mani, self.chat, 'hello')
        r = self._post('j6_api_read', {'chat': self.chat.pk})
        self.assertEqual(r.json()['read_upto'], 1)

    def test_the_caps_apply_to_clients_too(self):
        config = Settings.load()
        config.max_chars = 5
        config.save()
        r = self._post('j6_api_send', {'chat': self.chat.pk, 'body': 'far too long'})
        self.assertEqual(r.status_code, 400)

    def test_version_carries_the_download_link(self):
        body = self.client.get(reverse('j6_api_version')).json()
        self.assertIn('version', body)
        self.assertIn('releases/latest/download/j6.exe', body['download'])

    def test_the_api_needs_no_csrf_token(self):
        """A desktop client has no cookie and no page, so it cannot have a CSRF token."""
        enforcing = Client(enforce_csrf_checks=True)
        r = enforcing.post(reverse('j6_api_send'),
                           data=json.dumps({'chat': self.chat.pk, 'body': 'no csrf here'}),
                           content_type='application/json',
                           HTTP_X_J6_SELF=self.anand.self_id)
        self.assertEqual(r.status_code, 201)


class ConsoleTests(TestCase):
    """The console reads by account: a chat id alone is never a page."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        service.send(self.anand, self.chat, 'morning')
        service.send(self.mani, self.chat, 'https://media.example.com/party.gif')

    def test_every_page_needs_the_admin(self):
        self.client.logout()
        for url in [reverse('j6_accounts'), reverse('j6_account', args=[self.anand.pk]),
                    reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk]),
                    reverse('j6_settings')]:
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_the_landing_page_lists_accounts_with_their_names(self):
        r = self.client.get(reverse('j6_accounts'))
        self.assertContains(r, 'ANAND')
        self.assertContains(r, self.anand.account_id)

    def test_search_matches_a_name_or_an_id(self):
        self.assertContains(self.client.get(reverse('j6_accounts') + '?q=Mani'), 'MANI')
        r = self.client.get(reverse('j6_accounts') + f'?q={self.anand.account_id[:9]}')
        self.assertContains(r, 'ANAND')

    def test_an_account_page_shows_their_chats_not_pair_titles(self):
        r = self.client.get(reverse('j6_account', args=[self.anand.pk]))
        self.assertContains(r, "ANAND's chats")
        self.assertContains(r, 'MANI')
        self.assertNotContains(r, 'ANAND ↔ MANI')

    def test_a_conversation_is_shown_from_that_account_side(self):
        r = self.client.get(reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk]))
        self.assertContains(r, 'Shown as <b>ANAND</b> sees it')
        body = r.content.decode()
        self.assertIn('j6-msg right', body)                   # their own messages, right-aligned

    def test_media_in_the_console_is_lazy_and_sends_no_referrer(self):
        """It is shown now — asked for — but on the gentlest terms available.

        A picture in somebody's chat is a link to whatever host they pasted, so loading it here
        tells that host the admin's address. It is lazy, referrer-free, and one switch away from
        going back behind a button (see ConsoleThreadTests).
        """
        body = self.client.get(
            reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk])).content.decode()
        self.assertIn('<img src="https://media.example.com', body)
        picture = body.split('<img src="https://media.example.com', 1)[1][:200]
        self.assertIn('loading="lazy"', picture)
        self.assertIn('referrerpolicy="no-referrer"', picture)

    def test_a_chat_that_is_not_theirs_is_a_404(self):
        riya = make('Riya')
        r = self.client.get(reverse('j6_admin_chat', args=[riya.pk, self.chat.pk]))
        self.assertEqual(r.status_code, 404)

    def test_suspend_and_resume(self):
        self.client.post(reverse('j6_account_state', args=[self.anand.pk, 'suspend']))
        self.assertFalse(Account.objects.get(pk=self.anand.pk).is_active)
        self.client.post(reverse('j6_account_state', args=[self.anand.pk, 'resume']))
        self.assertTrue(Account.objects.get(pk=self.anand.pk).is_active)

    def test_deleting_an_account_takes_its_chats(self):
        self.client.post(reverse('j6_account_delete', args=[self.anand.pk]))
        self.assertFalse(Account.objects.filter(pk=self.anand.pk).exists())
        self.assertEqual(Chat.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)

    def test_deleting_one_chat_leaves_the_accounts(self):
        self.client.post(reverse('j6_chat_delete', args=[self.anand.pk, self.chat.pk]))
        self.assertEqual(Chat.objects.count(), 0)
        self.assertEqual(Account.objects.count(), 2)

    def test_settings_save(self):
        config = Settings.load()
        data = {f: getattr(config, f) for f in
                ['new_chats_per_hour', 'msgs_per_minute', 'max_chars', 'keep_days',
                 'idle_account_days', 'autoplay_seconds', 'max_media_px']}
        data.update({'signup_open': 'on', 'render_media': 'on', 'allow_svg': 'on',
                     'push_enabled': 'on', 'keep_days': 30})
        r = self.client.post(reverse('j6_settings'), data, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Settings.load().keep_days, 30)


class CronTests(TestCase):
    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)

    def test_both_hooks_are_registered_with_the_hub(self):
        self.assertIn('j6.push', [h.name for h in cronhub.hooks('tick')])
        self.assertIn('j6.retention', [h.name for h in cronhub.hooks('cleanup')])

    def test_retention_deletes_old_messages_and_keeps_new_ones(self):
        old = service.send(self.anand, self.chat, 'ancient')
        Message.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=200))
        service.send(self.anand, self.chat, 'recent')
        result = retention(_ctx('cleanup'))
        self.assertEqual(result['messages_deleted'], 1)
        self.assertEqual(Message.objects.count(), 1)

    def test_retention_retires_an_idle_account(self):
        long_ago = timezone.now() - timedelta(days=400)
        Account.objects.filter(pk=self.mani.pk).update(last_seen_at=long_ago,
                                                       created_at=long_ago)
        retention(_ctx('cleanup'))
        self.assertFalse(Account.objects.filter(pk=self.mani.pk).exists())
        self.assertEqual(Chat.objects.count(), 0)             # their chats go with them

    def test_push_says_so_when_syncup_is_not_set_up(self):
        self.assertIn('note', push(_ctx('tick')))

    def test_push_sends_one_summary_and_does_not_repeat_it(self):
        """The backstop's job: two messages nobody managed to push become one summary.

        The token is set *after* the messages, so the send-time push had nothing to work with —
        which is exactly the case this hook exists for (a failed send, a restart mid-flight).
        """
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.site_base = 'https://synchub.example.com'
        hub.save()
        service.send(self.anand, self.chat, 'one')
        service.send(self.anand, self.chat, 'two')
        Account.objects.filter(pk=self.mani.pk).update(
            syncup_token='tok', last_seen_at=timezone.now() - timedelta(minutes=10))

        sent = []

        class Fake:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return b'{"delivered": 1}'

        def fake_open(request, timeout=None):
            sent.append(json.loads(request.data))
            return Fake()

        with patch('core.push.urllib.request.urlopen', fake_open):
            first = push(_ctx('tick'))
            second = push(_ctx('tick'))

        self.assertEqual(len(sent), 1, 'a second tick must not buzz again')
        self.assertEqual(first['sent'], 1)
        self.assertEqual(second['items'], 0)
        self.assertIn('2 new messages', sent[0]['body'])
        self.assertTrue(sent[0]['url'].endswith(reverse('j6_inbox')))

    def test_push_skips_somebody_whose_page_is_open(self):
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.save()
        service.send(self.anand, self.chat, 'hello')
        Account.objects.filter(pk=self.mani.pk).update(syncup_token='tok',
                                                       last_seen_at=timezone.now())
        with patch('core.push.urllib.request.urlopen', side_effect=AssertionError('sent anyway')):
            result = push(_ctx('tick'))
        self.assertEqual(result['items'], 0)


class StaticLayoutTests(TestCase):
    """J6's assets live in the project's static/ folder, like every other asset here."""

    def test_the_app_ships_no_static_folder_of_its_own(self):
        self.assertFalse((Path(django_settings.BASE_DIR) / 'j6' / 'static').exists())

    def test_the_two_files_are_where_the_pages_ask_for_them(self):
        from django.contrib.staticfiles import finders
        for path in ['j6/user.css', 'j6/user.js']:
            found = finders.find(path)
            self.assertIsNotNone(found, path)
            self.assertTrue(Path(found).is_relative_to(
                Path(django_settings.BASE_DIR) / 'static'), path)


class MessageDeleteTests(TestCase):
    """The console can remove a line outright — no placeholder, nothing left behind."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        self.keep = service.send(self.anand, self.chat, 'keep this one')
        self.drop = service.send(self.mani, self.chat, 'delete this one')
        self.url = reverse('j6_message_delete',
                           args=[self.anand.pk, self.chat.pk, self.drop.seq])

    def test_the_thread_offers_it_on_every_message(self):
        r = self.client.get(reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk]))
        self.assertContains(r, self.url)

    def test_it_deletes_the_row_rather_than_marking_it(self):
        self.client.post(self.url)
        self.assertFalse(Message.objects.filter(pk=self.drop.pk).exists())
        self.assertTrue(Message.objects.filter(pk=self.keep.pk).exists())

    def test_nothing_is_left_in_its_place(self):
        self.client.post(self.url)
        body = self.client.get(
            reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk])).content.decode()
        self.assertNotIn('delete this one', body)
        self.assertNotIn('was deleted', body)
        self.assertNotIn('removed by', body)

    def test_it_disappears_for_both_of_them(self):
        self.client.post(self.url)
        for account in (self.anand, self.mani):
            bodies = [m['body'] for m in service.thread(account, self.chat)['messages']]
            self.assertNotIn('delete this one', bodies)

    def test_the_gap_in_seq_breaks_nothing(self):
        self.client.post(self.url)
        after = service.send(self.anand, self.chat, 'life goes on')
        self.assertGreater(after.seq, self.drop.seq)
        seqs = [m['seq'] for m in service.thread(self.mani, self.chat)['messages']]
        self.assertEqual(seqs, [self.keep.seq, after.seq])

    def test_post_only_and_admin_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertTrue(Message.objects.filter(pk=self.drop.pk).exists())

    def test_a_message_from_another_chat_is_a_404(self):
        other, _ = service.start_chat(make('Riya'), make('Karthik').account_id)
        service.send(other.low, other, 'elsewhere')
        r = self.client.post(reverse('j6_message_delete', args=[self.anand.pk, other.pk, 1]))
        self.assertEqual(r.status_code, 404)


class FactoryResetTests(TestCase):
    """Empty J6 and start again — for the weeks when everything in it is a test."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        service.send(self.anand, self.chat, 'one')
        service.send(self.mani, self.chat, 'two')
        self.url = reverse('j6_reset')

    def test_the_page_says_exactly_what_will_go(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'Factory reset')
        self.assertContains(r, '>2</b>')                      # two accounts, two messages
        self.assertContains(r, 'no undo')

    def test_it_is_reachable_from_module_settings(self):
        self.assertContains(self.client.get(reverse('j6_settings')), self.url)

    def test_nothing_happens_without_the_word(self):
        r = self.client.post(self.url, {'confirm': 'yes please'}, follow=True)
        self.assertContains(r, 'type RESET')
        self.assertEqual(Account.objects.count(), 2)

    def test_it_empties_every_table(self):
        r = self.client.post(self.url, {'confirm': 'RESET'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Account.objects.count(), 0)
        self.assertEqual(Chat.objects.count(), 0)
        self.assertEqual(Message.objects.count(), 0)
        self.assertEqual(Member.objects.count(), 0)

    def test_lower_case_is_accepted_because_people_type_it(self):
        self.client.post(self.url, {'confirm': 'reset'})
        self.assertEqual(Account.objects.count(), 0)

    def test_settings_are_kept_unless_asked(self):
        config = Settings.load()
        config.keep_days = 30
        config.save()
        self.client.post(self.url, {'confirm': 'RESET'})
        self.assertEqual(Settings.load().keep_days, 30)

    def test_settings_can_be_reset_too(self):
        config = Settings.load()
        config.keep_days = 30
        config.save()
        self.client.post(self.url, {'confirm': 'RESET', 'settings': 'on'})
        self.assertEqual(Settings.load().keep_days, 90)       # back to the default

    def test_it_touches_nothing_outside_j6(self):
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.save()
        self.client.post(self.url, {'confirm': 'RESET'})
        self.assertEqual(HubSettings.load().syncup_base, 'https://syncup.example.com')

    def test_it_needs_the_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'confirm': 'RESET'}).status_code, 302)
        self.assertEqual(Account.objects.count(), 2)

    def test_afterwards_a_new_account_starts_clean(self):
        self.client.post(self.url, {'confirm': 'RESET'})
        fresh = make('Riya')
        self.assertEqual(Account.objects.count(), 1)
        self.assertEqual(service.chat_rows(fresh), [])


class SettingsFormRenderingTests(TestCase):
    """A checkbox is not a text box with a tick in it.

    Rendered as an ordinary field, a boolean ends up as a bare checkbox floating under its own
    label — which is exactly what the console looked like before. Booleans go through the shared
    `_field.html` include and come out as switch rows: the label and its explanation on the left,
    a toggle on the right.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)

    def _body(self, name='j6_settings'):
        return self.client.get(reverse(name)).content.decode()

    def test_every_boolean_is_a_switch_row(self):
        import re
        body = self._body()
        for field in ('signup_open', 'render_media', 'allow_svg', 'push_enabled'):
            pattern = r'<div class="switch-row[^"]*">.{0,500}?name="' + field + '"'
            self.assertRegex(body, re.compile(pattern, re.S), f'{field} is not a switch row')

    def test_no_checkbox_is_left_floating_in_a_field(self):
        import re
        body = self._body()
        stray = re.findall(r'<div class="field[^"]*">\s*<label class="label"[^>]*>[^<]*</label>'
                           r'\s*<input type="checkbox"', body)
        self.assertEqual(stray, [])

    def test_a_toggle_carries_its_track(self):
        """Without the track span the switch has nothing to draw."""
        body = self._body()
        self.assertIn('<span class="track"></span>', body)

    def test_the_help_text_sits_with_the_label_not_under_the_toggle(self):
        import re
        body = self._body()
        m = re.search(r'<div class="text">\s*<b>Render media links</b>\s*'
                      r'<span class="help">([^<]+)</span>', body)
        self.assertIsNotNone(m)
        self.assertIn('every link stays a link', m.group(1))

    def test_a_text_field_still_looks_like_one(self):
        body = self._body()
        self.assertIn('<label class="label" for="id_keep_days">', body)
        self.assertIn('name="keep_days"', body)

    def test_the_diary_uses_the_same_include(self):
        """One shape for every module's settings, which is why it lives in core."""
        import re
        body = self._body('hibiscus_settings')
        self.assertRegex(body, re.compile(r'<div class="switch-row[^"]*">.{0,500}?'
                                          r'name="week_starts_sunday"', re.S))


class AccountTestPushTests(TestCase):
    """The button that turns "push is not working" into a sentence.

    Four things can be wrong — no address, no token, a refusal, or an address pointing at this
    very server — and from the outside they look identical. This says which.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.account = make('Riya')
        Account.objects.filter(pk=self.account.pk).update(syncup_token='tok-123')
        self.url = reverse('j6_account_test_push', args=[self.account.pk])
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.syncup_notify_key = 'k'
        hub.site_base = 'https://synchub.example.com'
        hub.save()

    def _send(self, fake):
        with patch('core.push.urllib.request.urlopen', fake):
            return self.client.post(self.url, follow=True)

    def test_the_button_is_on_the_account_page_when_there_is_a_token(self):
        r = self.client.get(reverse('j6_account', args=[self.account.pk]))
        self.assertContains(r, self.url)

    def test_no_button_without_a_token_but_an_explanation(self):
        Account.objects.filter(pk=self.account.pk).update(syncup_token='')
        r = self.client.get(reverse('j6_account', args=[self.account.pk]))
        self.assertNotContains(r, self.url)
        self.assertContains(r, 'have not opened J6 inside SyncUp')

    def test_a_delivery_is_reported_with_its_count(self):
        fake = FakeSyncUp(delivered=2)
        r = self._send(fake)
        self.assertEqual(fake.url, 'https://syncup.example.com/app/v1/partner/notify')
        self.assertEqual(fake.payload['token'], 'tok-123')
        self.assertIn('test message', fake.payload['title'])
        self.assertTrue(fake.payload['url'].endswith(reverse('j6_inbox')))
        self.assertContains(r, 'delivered it to 2 devices')

    def test_a_browser_only_account_is_told_what_it_would_take(self):
        Account.objects.filter(pk=self.account.pk).update(syncup_token='')
        fake = FakeSyncUp()
        r = self._send(fake)
        self.assertIsNone(fake.url)
        self.assertContains(r, 'no device token')
        self.assertContains(r, 'only notified while a tab is open')

    def test_no_syncup_address(self):
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = ''
        hub.save()
        r = self._send(FakeSyncUp())
        self.assertContains(r, 'System → Settings')

    def test_an_address_pointing_at_this_server(self):
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = 'https://testserver'
        hub.save()
        fake = FakeSyncUp()
        r = self._send(fake)
        self.assertIsNone(fake.url, 'it tried to call itself')
        self.assertContains(r, 'this same site')

    def test_a_timeout_names_the_whitelist(self):
        r = self._send(FakeSyncUp(raises=TimeoutError('The read operation timed out')))
        self.assertContains(r, 'whitelist')

    def test_delivered_to_nothing_means_a_stale_token(self):
        r = self._send(FakeSyncUp(delivered=0))
        self.assertContains(r, 'stale')

    def test_post_only_and_admin_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        fake = FakeSyncUp()
        with patch('core.push.urllib.request.urlopen', fake):
            self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertIsNone(fake.url)


class RunInline:
    """A Thread stand-in that runs the target at once, so a push can be asserted on."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args = target, args
        self.kwargs = kwargs or {}
        self.daemon = daemon
        self.started = False

    def start(self):
        self.started = True
        self.target(*self.args, **self.kwargs)


class PushAtSendTests(TestCase):
    """A message to somebody who is not looking goes out now, not on the next quarter hour.

    This is the difference between a diary and a chat. Hibiscus nudges on a schedule, so the
    hub's 15-minute tick is exactly right for it; a chat message that takes up to seventeen
    minutes to reach a phone is simply broken, which is what J6 looked like.
    """

    def setUp(self):
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.site_base = 'https://synchub.example.com'
        hub.save()
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        self._away(self.mani)

    def _away(self, account, minutes=5):
        Account.objects.filter(pk=account.pk).update(
            syncup_token='tok-123', last_seen_at=timezone.now() - timedelta(minutes=minutes))

    def _send(self, body='are you there?'):
        fake = FakeSyncUp()
        with patch('j6.push.threading.Thread', RunInline), \
             patch('core.push.urllib.request.urlopen', fake):
            message = service.send(self.anand, self.chat, body)
        return fake, message

    def test_an_away_recipient_is_pushed_at_once(self):
        fake, message = self._send()
        self.assertEqual(fake.url, 'https://syncup.example.com/app/v1/partner/notify')
        self.assertEqual(fake.payload['title'], 'ANAND')
        self.assertEqual(fake.payload['body'], 'are you there?')
        self.assertTrue(fake.payload['url'].endswith(reverse('j6_inbox')))
        self.assertEqual(fake.payload['token'], 'tok-123')

    def test_somebody_reading_right_now_is_not_buzzed(self):
        Account.objects.filter(pk=self.mani.pk).update(last_seen_at=timezone.now())
        fake, _ = self._send()
        self.assertIsNone(fake.url)

    def test_no_device_token_means_nothing_is_attempted(self):
        Account.objects.filter(pk=self.mani.pk).update(syncup_token='')
        fake, _ = self._send()
        self.assertIsNone(fake.url)

    def test_a_blocked_sender_cannot_buzz_them(self):
        service.set_blocked(self.mani, self.chat, True)
        fake, _ = self._send()
        self.assertIsNone(fake.url)

    def test_switching_push_off_switches_this_off_too(self):
        config = Settings.load()
        config.push_enabled = False
        config.save()
        fake, _ = self._send()
        self.assertIsNone(fake.url)

    def test_the_message_is_claimed_before_the_thread_runs(self):
        """Two quick messages must not race into two buzzes for the same seq."""
        _, message = self._send()
        theirs = Member.objects.get(chat=self.chat, account=self.mani)
        self.assertEqual(theirs.last_push_seq, message.seq)

    def test_the_tick_does_not_repeat_what_was_already_pushed(self):
        self._send()
        fake = FakeSyncUp()
        with patch('core.push.urllib.request.urlopen', fake):
            result = push(_ctx('tick'))
        self.assertIsNone(fake.url, 'the backstop buzzed again for the same message')
        self.assertEqual(result['sent'], 0)

    def test_the_tick_still_catches_what_the_send_missed(self):
        """A send that failed leaves the message unread, so the next tick tries again."""
        with patch('j6.push.threading.Thread', RunInline), \
             patch('core.push.urllib.request.urlopen',
                   FakeSyncUp(raises=TimeoutError('timed out'))):
            service.send(self.anand, self.chat, 'first try fails')
        Member.objects.filter(chat=self.chat, account=self.mani).update(last_push_seq=0)
        fake = FakeSyncUp()
        with patch('core.push.urllib.request.urlopen', fake):
            result = push(_ctx('tick'))
        self.assertIsNotNone(fake.url)
        self.assertEqual(result['sent'], 1)

    def test_the_sender_never_waits_for_syncup(self):
        """The decision is synchronous; only the delivery gets a thread."""
        with patch('j6.push.threading.Thread') as thread:
            service.send(self.anand, self.chat, 'off you go')
        self.assertTrue(thread.called)
        self.assertTrue(thread.call_args.kwargs['daemon'])
        thread.return_value.start.assert_called_once()

    def test_nothing_is_spawned_when_there_is_nothing_to_do(self):
        Account.objects.filter(pk=self.mani.pk).update(syncup_token='')
        with patch('j6.push.threading.Thread') as thread:
            service.send(self.anand, self.chat, 'nobody to tell')
        self.assertFalse(thread.called)

    def test_the_hook_says_when_no_account_can_be_pushed_to(self):
        Account.objects.all().update(syncup_token='')
        self.assertIn('device token', push(_ctx('tick'))['note'])


class LazyHistoryTests(TestCase):
    """A long conversation must not arrive all at once — nor fetch its media on open."""

    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        # Backdated: a hundred messages in the last minute is a flood, and the rate limit would
        # quite rightly refuse the next one. History is what this is about.
        old = timezone.now() - timedelta(days=3)
        Message.objects.bulk_create(
            [Message(chat=self.chat, seq=n, sender=self.anand if n % 2 else self.mani,
                     body=f'message {n}', created_at=old + timedelta(minutes=n))
             for n in range(1, 101)])
        Chat.objects.filter(pk=self.chat.pk).update(next_seq=101)

    def test_a_first_load_is_one_page_of_the_newest(self):
        thread = service.thread(self.anand, self.chat)
        self.assertEqual(len(thread['messages']), service.PAGE)
        self.assertTrue(thread['has_more'])
        self.assertEqual(thread['messages'][-1]['body'], 'message 100')     # newest last

    def test_earlier_walks_backwards_a_page_at_a_time(self):
        first = service.thread(self.anand, self.chat)
        second = service.earlier(self.anand, self.chat, first['from_seq'])
        self.assertEqual(len(second['messages']), service.PAGE)
        self.assertTrue(second['has_more'])
        self.assertLess(second['messages'][-1]['seq'], first['from_seq'])
        third = service.earlier(self.anand, self.chat, second['from_seq'])
        self.assertFalse(third['has_more'], 'that is the start of the conversation')

    def test_a_short_conversation_says_there_is_nothing_above(self):
        chat, _ = service.start_chat(self.anand, make('Riya').account_id)
        service.send(self.anand, chat, 'hello')
        self.assertFalse(service.thread(self.anand, chat)['has_more'])

    def test_a_poll_is_unaffected_because_it_is_already_incremental(self):
        thread = service.thread(self.anand, self.chat, since=98)
        self.assertEqual([m['seq'] for m in thread['messages']], [99, 100])

    def test_blocking_still_hides_their_half_of_the_history(self):
        service.set_blocked(self.anand, self.chat, True)
        thread = service.thread(self.anand, self.chat)
        self.assertTrue(all(m['mine'] for m in thread['messages']))
        earlier = service.earlier(self.anand, self.chat, thread['from_seq'])
        self.assertTrue(all(m['mine'] for m in earlier['messages']))

    def test_the_page_carries_a_page_not_a_history(self):
        self.client.post(reverse('j6_login'), {'self_id': self.anand.self_id})
        body = self.client.get(reverse('j6_chat', args=[self.chat.pk])).content.decode()
        self.assertIn('id="earlier"', body)
        self.assertNotIn('message 1<', body)                 # the oldest is not in the page
        self.assertIn('message 100', body)

    def test_the_endpoint_refuses_somebody_elses_chat(self):
        self.client.post(reverse('j6_login'), {'self_id': self.anand.self_id})
        theirs, _ = service.start_chat(make('Riya'), make('Karthik').account_id)
        r = self.client.get(reverse('j6_earlier') + f'?chat={theirs.pk}&before=10')
        self.assertEqual(r.status_code, 404)


class ConsoleAccountTests(TestCase):
    """What the console can see and change about an account."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.account = make('Mani')
        self.url = reverse('j6_account', args=[self.account.pk])

    def test_both_ids_are_on_the_page(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn(self.account.account_id, body)
        self.assertIn(self.account.self_id, body)

    def test_the_self_id_is_masked_until_asked_for(self):
        body = self.client.get(self.url).content.decode()
        field = body.split(f'id="self-{self.account.pk}"', 1)[1][:200]
        self.assertIn('type="password"', field)

    def test_the_page_says_what_the_self_id_is(self):
        self.assertContains(self.client.get(self.url), 'signs you in <b>as them</b>')

    def test_the_admin_can_rename_somebody(self):
        r = self.client.post(reverse('j6_account_rename', args=[self.account.pk]),
                             {'name': 'Mani K'}, follow=True)
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, 'MANI K')
        self.assertContains(r, 'Account ID and Self-ID are unchanged')

    def test_renaming_leaves_both_ids_alone(self):
        before = (self.account.account_id, self.account.self_id)
        self.client.post(reverse('j6_account_rename', args=[self.account.pk]), {'name': 'Zed'})
        self.account.refresh_from_db()
        self.assertEqual((self.account.account_id, self.account.self_id), before)

    def test_a_name_that_is_too_short_is_refused(self):
        r = self.client.post(reverse('j6_account_rename', args=[self.account.pk]),
                             {'name': 'M'}, follow=True)
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, 'MANI')
        self.assertContains(r, 'at least 2 characters')

    def test_renaming_is_post_only_and_admin_only(self):
        url = reverse('j6_account_rename', args=[self.account.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(url, {'name': 'Hacked'}).status_code, 302)
        self.account.refresh_from_db()
        self.assertEqual(self.account.name, 'MANI')


class ConsoleThreadTests(TestCase):
    """The console's own window on a conversation, and what it loads."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        old = timezone.now() - timedelta(days=3)
        Message.objects.bulk_create(
            [Message(chat=self.chat, seq=n, sender=self.anand if n % 2 else self.mani,
                     body=f'message {n}', created_at=old + timedelta(minutes=n))
             for n in range(1, 91)])
        Message.objects.create(chat=self.chat, seq=91, sender=self.mani,
                               body='https://media.example.com/party.gif')
        Chat.objects.filter(pk=self.chat.pk).update(next_seq=92)
        self.url = reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk])

    def test_it_shows_a_window_not_the_whole_conversation(self):
        from j6.views_admin import ADMIN_PAGE
        body = self.client.get(self.url).content.decode()
        self.assertEqual(body.count('class="j6-msg'), ADMIN_PAGE)
        self.assertIn('91 messages', body)
        self.assertIn('showing 60', body)

    def test_there_is_a_way_back_through_the_history(self):
        import re
        body = self.client.get(self.url).content.decode()
        self.assertIn('?before=', body)
        before = re.search(r'\?before=(\d+)', body).group(1)
        older = self.client.get(self.url + f'?before={before}').content.decode()
        self.assertIn('message 1<', older)
        self.assertIn('Jump to latest', older)

    def test_media_is_shown_when_the_setting_says_so(self):
        body = self.client.get(self.url).content.decode()
        self.assertIn('<img src="https://media.example.com/party.gif"', body)
        self.assertIn('loading="lazy"', body)
        self.assertIn('referrerpolicy="no-referrer"', body)

    def test_turning_it_off_puts_it_back_behind_a_button(self):
        config = Settings.load()
        config.admin_render_media = False
        config.save()
        body = self.client.get(self.url).content.decode()
        self.assertNotIn('<img src="https://media', body)
        self.assertIn('Show media', body)

    def test_something_that_is_not_media_stays_text(self):
        Message.objects.create(chat=self.chat, seq=92, sender=self.mani,
                               body='have a look at https://example.com/notes and tell me')
        body = self.client.get(self.url).content.decode()
        self.assertIn('have a look at', body)
        self.assertNotIn('<img src="https://example.com/notes"', body)


class UppercaseNameTests(TestCase):
    """Names are stored in capitals, whichever door they come in through."""

    def test_the_welcome_screen_stores_capitals(self):
        self.client.post(reverse('j6_welcome'), {'name': 'Anand'})
        self.assertEqual(Account.objects.get().name, 'ANAND')

    def test_the_client_api_does_too(self):
        r = self.client.post(reverse('j6_api_start'), data=json.dumps({'name': 'riya k'}),
                             content_type='application/json')
        self.assertEqual(r.json()['name'], 'RIYA K')
        self.assertEqual(Account.objects.get().name, 'RIYA K')

    def test_so_does_a_rename_by_its_owner(self):
        account = make('Mani')
        self.client.post(reverse('j6_login'), {'self_id': account.self_id})
        self.client.post(reverse('j6_profile'), {'what': 'name', 'name': 'mani k'})
        account.refresh_from_db()
        self.assertEqual(account.name, 'MANI K')

    def test_and_a_rename_by_the_admin(self):
        admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(admin)
        account = make('Mani')
        self.client.post(reverse('j6_account_rename', args=[account.pk]), {'name': 'karthik'})
        account.refresh_from_db()
        self.assertEqual(account.name, 'KARTHIK')

    def test_the_length_rule_still_counts_what_was_typed(self):
        with self.assertRaises(ValidationError):
            make('m')
        self.assertEqual(make('mi').name, 'MI')

    def test_initials_follow(self):
        self.assertEqual(make('anand kumar').initials, 'AK')
        self.assertEqual(make('mani').initials, 'MA')

    def test_a_notification_carries_the_name_as_stored(self):
        anand, mani = make('anand'), make('mani')
        chat, _ = service.start_chat(anand, mani.account_id)
        Account.objects.filter(pk=mani.pk).update(
            syncup_token='tok', last_seen_at=timezone.now() - timedelta(minutes=5))
        from core.models import HubSettings
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.save()
        fake = FakeSyncUp()
        with patch('j6.push.threading.Thread', RunInline), \
             patch('core.push.urllib.request.urlopen', fake):
            service.send(anand, chat, 'hello')
        self.assertEqual(fake.payload['title'], 'ANAND')

    def test_search_in_the_console_is_still_case_insensitive(self):
        admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(admin)
        make('anand')
        self.assertContains(self.client.get(reverse('j6_accounts') + '?q=anand'), 'ANAND')
        self.assertContains(self.client.get(reverse('j6_accounts') + '?q=ANAND'), 'ANAND')

    def test_the_command_brings_older_rows_into_line(self):
        from io import StringIO

        from django.core.management import call_command
        account = make('Mani')
        Account.objects.filter(pk=account.pk).update(name='Mani Kumar')   # as an old row would be
        out = StringIO()
        call_command('uppercase_names', stdout=out)
        account.refresh_from_db()
        self.assertEqual(account.name, 'MANI KUMAR')
        self.assertIn('1 name updated', out.getvalue())

    def test_the_command_is_safe_to_run_twice(self):
        from io import StringIO

        from django.core.management import call_command
        make('Riya')
        call_command('uppercase_names', stdout=StringIO())
        out = StringIO()
        call_command('uppercase_names', stdout=out)
        self.assertIn('already in capitals', out.getvalue())

    def test_a_dry_run_changes_nothing(self):
        from io import StringIO

        from django.core.management import call_command
        account = make('Mani')
        Account.objects.filter(pk=account.pk).update(name='Mani')
        call_command('uppercase_names', '--dry-run', stdout=StringIO())
        account.refresh_from_db()
        self.assertEqual(account.name, 'Mani')
