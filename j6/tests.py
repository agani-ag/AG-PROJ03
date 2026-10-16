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

from . import service
from .cron import push, retention
from .models import Account, Chat, Member, Message, Settings, new_account_id, new_self_id


def make(name='Anand'):
    return service.create_account(name)


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
        self.assertEqual(make('Mi').name, 'Mi')

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
        self.assertEqual(make('Mani').initials, 'Ma')

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
        self.assertTrue(Account.objects.filter(name='Riya').exists())

    def test_hello_says_who_you_are_and_what_you_have(self):
        body = self._post('j6_api_hello').json()
        self.assertEqual(body['name'], 'Anand')
        self.assertEqual(body['chats'][0]['name'], 'Mani')
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
        self.assertEqual(r.json()['name'], 'Riya')

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
        self.assertContains(r, 'Anand')
        self.assertContains(r, self.anand.account_id)

    def test_search_matches_a_name_or_an_id(self):
        self.assertContains(self.client.get(reverse('j6_accounts') + '?q=Mani'), 'Mani')
        r = self.client.get(reverse('j6_accounts') + f'?q={self.anand.account_id[:9]}')
        self.assertContains(r, 'Anand')

    def test_an_account_page_shows_their_chats_not_pair_titles(self):
        r = self.client.get(reverse('j6_account', args=[self.anand.pk]))
        self.assertContains(r, "Anand's chats")
        self.assertContains(r, 'Mani')
        self.assertNotContains(r, 'Anand ↔ Mani')

    def test_a_conversation_is_shown_from_that_account_side(self):
        r = self.client.get(reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk]))
        self.assertContains(r, 'Shown as <b>Anand</b> sees it')
        body = r.content.decode()
        self.assertIn('j6-msg right', body)                   # their own messages, right-aligned

    def test_media_is_not_loaded_for_the_admin(self):
        r = self.client.get(reverse('j6_admin_chat', args=[self.anand.pk, self.chat.pk]))
        body = r.content.decode()
        self.assertIn('Show media', body)
        # the URL appears as text and as a link to click — never inside an img or a video
        self.assertNotIn('<img src="https://media.example.com', body)
        self.assertNotIn('<video', body)

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
                 'idle_account_days', 'autoplay_seconds', 'max_media_px', 'site_base',
                 'syncup_base', 'syncup_notify_key']}
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
        config = Settings.load()
        config.syncup_base = 'https://syncup.example.com'
        config.site_base = 'https://synchub.example.com'
        config.save()
        Account.objects.filter(pk=self.mani.pk).update(
            syncup_token='tok', last_seen_at=timezone.now() - timedelta(minutes=10))
        service.send(self.anand, self.chat, 'one')
        service.send(self.anand, self.chat, 'two')

        sent = []

        class Fake:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return b'{"delivered": 1}'

        def fake_open(request, timeout=None):
            sent.append(json.loads(request.data))
            return Fake()

        with patch('j6.cron.urllib.request.urlopen', fake_open):
            first = push(_ctx('tick'))
            second = push(_ctx('tick'))

        self.assertEqual(len(sent), 1, 'a second tick must not buzz again')
        self.assertEqual(first['sent'], 1)
        self.assertEqual(second['items'], 0)
        self.assertIn('2 new messages', sent[0]['body'])
        self.assertTrue(sent[0]['url'].endswith(reverse('j6_inbox')))

    def test_push_skips_somebody_whose_page_is_open(self):
        config = Settings.load()
        config.syncup_base = 'https://syncup.example.com'
        config.save()
        Account.objects.filter(pk=self.mani.pk).update(syncup_token='tok',
                                                       last_seen_at=timezone.now())
        service.send(self.anand, self.chat, 'hello')
        with patch('j6.cron.urllib.request.urlopen', side_effect=AssertionError('sent anyway')):
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
