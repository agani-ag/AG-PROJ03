"""The pages people actually see, and the two files that make them work.

Template mistakes are invisible to the model tests — a page renders, it just renders wrongly —
so these walk the real HTML: the inbox's two panes, the media rules reaching the script, the
block button, and the small promises the copy makes (that a Self-ID is re-readable, that nothing
opens another tab).
"""
from pathlib import Path

from django.conf import settings as django_settings
from django.test import TestCase
from django.urls import reverse

from . import service
from core import auth as hub
from core.models import Member

from .models import Account, Settings

CSS = Path(django_settings.BASE_DIR) / 'static' / 'j6' / 'user.css'
JS = Path(django_settings.BASE_DIR) / 'static' / 'j6' / 'user.js'


def make(name='Anand'):
    return service.create_account(name)


class PageTests(TestCase):
    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        service.send(self.mani, self.chat, 'morning')
        self.client.post(reverse('j6_login'), {'self_id': self.anand.self_id})

    def test_the_list_renders_names_never_ids(self):
        body = self.client.get(reverse('j6_inbox')).content.decode()
        visible = body.split('id="rows"', 1)[1].split('<footer', 1)[0]
        self.assertIn('MANI', visible)
        # the ID travels in the boot payload, but no row is ever *headed* by one
        self.assertNotIn(self.mani.account_id, visible)

    def test_opening_a_chat_renders_both_panes_and_marks_it_open(self):
        body = self.client.get(reverse('j6_chat', args=[self.chat.pk])).content.decode()
        self.assertIn('class="inbox open"', body)         # CSS shows one pane on a phone
        self.assertIn('id="msgs"', body)
        self.assertIn('id="composer"', body)

    def test_the_thread_is_handed_to_the_page_so_there_is_no_empty_flash(self):
        body = self.client.get(reverse('j6_chat', args=[self.chat.pk])).content.decode()
        self.assertIn('id="boot"', body)
        self.assertIn('morning', body)

    def test_opening_a_chat_clears_its_unread_count(self):
        self.assertEqual(service.chat_rows(self.anand)[0]['unread'], 1)
        self.client.get(reverse('j6_chat', args=[self.chat.pk]))
        self.assertEqual(service.chat_rows(self.anand)[0]['unread'], 0)

    def test_somebody_elses_chat_is_a_404(self):
        theirs, _ = service.start_chat(make('Riya'), make('Karthik').account_id)
        self.assertEqual(self.client.get(reverse('j6_chat', args=[theirs.pk])).status_code, 404)

    def test_every_page_refuses_to_be_cached(self):
        for name in ['j6_inbox', 'j6_profile', 'j6_new', 'j6_ids']:
            r = self.client.get(reverse(name))
            self.assertIn('no-store', r['Cache-Control'], name)
            self.assertEqual(r['Referrer-Policy'], 'same-origin', name)

    def test_block_and_unblock_from_the_thread(self):
        self.client.post(reverse('j6_block', args=[self.chat.pk]))
        self.assertTrue(service.thread(self.anand, self.chat)['blocked'])
        self.client.post(reverse('j6_block', args=[self.chat.pk]))
        self.assertFalse(service.thread(self.anand, self.chat)['blocked'])

    def test_the_profile_can_rename_without_touching_the_ids(self):
        before = (self.anand.account_id, self.anand.self_id)
        self.client.post(reverse('j6_profile'), {'what': 'name', 'name': 'Anand K'})
        self.anand.refresh_from_db()
        self.assertEqual(self.anand.name, 'ANAND K')
        self.assertEqual((self.anand.account_id, self.anand.self_id), before)

    def test_the_profile_carries_the_sign_in_link(self):
        body = self.client.get(reverse('j6_profile')).content.decode()
        self.assertIn(reverse('j6_magic', args=[self.anand.self_id]), body)

    def test_signing_out_needs_the_self_id_to_come_back(self):
        self.assertContains(self.client.get(reverse('j6_profile')), 'no other way back')
        self.client.get(reverse('j6_logout'))
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 302)

    def test_nothing_opens_another_tab(self):
        for name in ['j6_inbox', 'j6_profile', 'j6_new']:
            self.assertNotIn('target="_blank"',
                             self.client.get(reverse(name)).content.decode(), name)

    def test_no_template_tag_leaks_into_a_page(self):
        """Django comments are single-line only; a multi-line {# #} prints itself."""
        for name in ['j6_welcome', 'j6_login']:
            self.client.get(reverse('j6_logout'))
            body = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('{#', body)
            self.assertNotIn('{%', body)


class MediaRuleTests(TestCase):
    """The renderer is told what it may do by the server, not by itself."""

    def setUp(self):
        self.anand = make('Anand')
        self.client.post(reverse('j6_login'), {'self_id': self.anand.self_id})

    def test_the_rules_reach_the_page(self):
        body = self.client.get(reverse('j6_inbox')).content.decode()
        self.assertIn('data-media="1"', body)
        self.assertIn('data-autoplay="15"', body)
        self.assertIn('data-maxpx="240"', body)

    def test_the_module_switch_turns_rendering_off_for_everyone(self):
        config = Settings.load()
        config.render_media = False
        config.save()
        self.assertIn('data-media="0"',
                      self.client.get(reverse('j6_inbox')).content.decode())

    def test_a_person_can_turn_it_off_for_themselves(self):
        self.client.post(reverse('j6_profile'), {'what': 'media', 'load_media': 'off'})
        self.anand.refresh_from_db()
        self.assertFalse(self.anand.load_media)
        self.assertIn('data-media="0"',
                      self.client.get(reverse('j6_inbox')).content.decode())

    def test_the_script_probes_rather_than_guessing_by_extension(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('function probe(', js)
        for kind in ['"img"', '"video"', '"audio"']:
            self.assertIn(kind, js)
        self.assertIn('no-referrer', js)              # the far end learns nothing about us
        self.assertIn('loading = "lazy"', js.replace('loading="lazy"', 'loading = "lazy"'))

    def test_a_failed_probe_leaves_a_link(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('var asLink', js)
        self.assertIn('PROBE_MS', js)                 # and it gives up rather than hanging

    def test_media_opens_in_the_page_not_another_tab(self):
        js = JS.read_text(encoding='utf-8')
        self.assertIn('function lightbox(', js)
        self.assertNotIn('_blank', js)
        self.assertIn('.lb', CSS.read_text(encoding='utf-8'))


class StylesheetTests(TestCase):
    """One grid, two layouts — the thing that makes the desk and the phone one codebase."""

    def test_the_inbox_is_two_panes_then_one(self):
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('grid-template-columns: 320px minmax(0, 1fr)', css)
        self.assertIn('@media (max-width: 760px)', css)
        self.assertIn('.inbox.open .list { display: none; }', css)

    def test_a_short_conversation_sits_at_the_bottom(self):
        self.assertIn('.msgs .pad { margin-top: auto; }', CSS.read_text(encoding='utf-8'))

    def test_the_composer_clears_the_home_bar(self):
        self.assertIn('env(safe-area-inset-bottom', CSS.read_text(encoding='utf-8'))

    def test_dark_mode_is_handled_both_ways(self):
        css = CSS.read_text(encoding='utf-8')
        self.assertIn('@media (prefers-color-scheme: dark)', css)
        self.assertIn(':root[data-theme="dark"]', css)

    def test_nothing_is_loaded_from_outside(self):
        css = CSS.read_text(encoding='utf-8')
        for forbidden in ['@import', 'https://', 'fonts.googleapis', 'cdn.']:
            self.assertNotIn(forbidden, css)


class RouteTests(TestCase):
    """The short address belongs to the people using it; the console takes the long one."""

    def setUp(self):
        self.admin_user = None
        self.anand = make('Anand')

    def test_the_chat_is_at_the_module_root(self):
        self.assertEqual(reverse('j6_inbox'), '/j6/')
        self.assertEqual(reverse('j6_welcome'), '/j6/welcome')

    def test_the_console_is_under_admin(self):
        self.assertEqual(reverse('j6_accounts'), '/j6/admin/')
        self.assertEqual(reverse('j6_settings'), '/j6/admin/settings/')

    def test_the_console_is_not_public(self):
        self.assertEqual(self.client.get('/j6/admin/').status_code, 302)

    def test_old_addresses_still_land(self):
        """Sign-in links were handed out with /me/ in them; they have to keep working."""
        r = self.client.get('/j6/me/profile')
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r['Location'], '/j6/profile')

    def test_a_query_string_survives_the_redirect(self):
        r = self.client.get('/j6/me/poll?chat=1&since=0')
        self.assertEqual(r['Location'], '/j6/poll?chat=1&since=0')

    def test_the_sign_in_link_is_unchanged(self):
        self.assertEqual(reverse('j6_magic', args=[self.anand.self_id]),
                         f'/j6/s/{self.anand.self_id}')

    def test_the_client_api_is_unchanged(self):
        self.assertEqual(reverse('j6_api_hello'), '/j6/api/hello')


class ThreadLayoutTests(TestCase):
    """Two layout bugs that only show on a real screen, pinned here instead.

    The stylesheet had the rules; the markup never gave them anything to work with — which is
    why the earlier tests passed while the page looked wrong.
    """

    def setUp(self):
        self.anand, self.mani = make('Anand'), make('Mani')
        self.chat, _ = service.start_chat(self.anand, self.mani.account_id)
        service.send(self.mani, self.chat, 'morning')
        self.client.post(reverse('j6_login'), {'self_id': self.anand.self_id})

    def test_a_short_conversation_rests_at_the_bottom(self):
        """`.msgs .pad { margin-top: auto }` needs an element to push. There wasn't one."""
        body = self.client.get(reverse('j6_chat', args=[self.chat.pk])).content.decode()
        msgs = body.split('id="msgs"', 1)[1]
        self.assertIn('<div class="pad"></div>', msgs)
        self.assertIn('.msgs .pad { margin-top: auto; }', CSS.read_text(encoding='utf-8'))

    def test_the_jump_pill_anchors_to_the_thread_not_the_window(self):
        css = CSS.read_text(encoding='utf-8')
        thread = css.split('.thread {', 1)[1].split('}', 1)[0]
        self.assertIn('position: relative', thread)
        jump = css.split('.jump {', 1)[1].split('}', 1)[0]
        self.assertIn('position: absolute', jump)


class NotificationScriptTests(TestCase):
    """What the page does about telling somebody a message arrived."""

    JS = JS.read_text(encoding='utf-8')

    def test_the_first_message_of_a_new_chat_is_announced(self):
        """It used to reload the page instead, throwing the notification away."""
        block = self.JS.split('if (!node) {', 1)[1].split('}', 1)[0]
        self.assertIn('notify(', block)
        self.assertLess(block.index('notify('), block.index('location.reload'))

    def test_http_is_explained_rather_than_called_unsupported(self):
        """Browsers withhold notifications on a plain-http origin; that is not the browser's fault."""
        self.assertIn('isSecureContext === false', self.JS)
        self.assertIn('https', self.JS.split('isSecureContext === false', 1)[1][:300])

    def test_a_hidden_tab_still_checks_often_enough_to_feel_like_a_chat(self):
        cadence = self.JS.split('function cadence', 1)[1][:400]
        self.assertIn('10000', cadence)
        self.assertNotIn('20000', cadence)

    def test_one_notification_per_sender_rather_than_per_message(self):
        self.assertIn('tag: "j6-" + title', self.JS)

    def test_nothing_is_raised_while_the_tab_is_in_front(self):
        notify = self.JS.split('function notify(', 1)[1][:400]
        self.assertIn('visibilityState === "visible") return', notify)


class LoadEarlierScriptTests(TestCase):
    """Walking back through a conversation without losing your place."""

    JS = JS.read_text(encoding='utf-8')

    def test_the_button_asks_for_the_page_above(self):
        self.assertIn('data-earlier', self.JS)
        self.assertIn('"?chat=" + CHAT + "&before=" + state.oldest', self.JS)

    def test_older_messages_are_prepended_not_appended(self):
        self.assertIn('function prepend(', self.JS)
        self.assertIn('msgs.insertBefore(frag', self.JS)

    def test_the_scroll_position_is_kept(self):
        """Without this the thread jumps to the top and you lose what you were reading."""
        block = self.JS.split('function prepend(', 1)[1].split('\n  }', 1)[0]
        self.assertIn('msgs.scrollHeight', block)
        self.assertIn('msgs.scrollTop = atTop + (msgs.scrollHeight - before)', block)

    def test_it_cannot_fire_twice_at_once(self):
        block = self.JS.split('earlierBtn.addEventListener', 1)[1][:400]
        self.assertIn('if (state.loading', block)

    def test_the_button_hides_at_the_start_of_the_conversation(self):
        block = self.JS.split('function paintEarlier(', 1)[1][:300]
        self.assertIn('hidden = !state.more', block)


class HubDoorTests(TestCase):
    """J6's two optional doors onto a hub account. Anonymity is the thing being protected."""

    def setUp(self):
        self.member = Member.objects.create(name='Ganesh Saravanan', email='g@example.com')
        hub.set_password(self.member, 'pw-for-tests')

    def _hub_in(self):
        self.client.post(reverse('member_login'),
                         {'sign_in_id': 'g@example.com', 'password': 'pw-for-tests'})

    def _j6_in(self, account):
        # Out first: J6's sign-in redirects somebody already in rather than swapping accounts.
        self.client.get(reverse('j6_logout'))
        self.client.post(reverse('j6_login'), {'self_id': account.self_id})

    # ---- door one: claim an account you already have
    def test_claiming_links_it_and_says_what_changed(self):
        account = make('Anand')
        self._hub_in()
        self._j6_in(account)
        r = self.client.post(reverse('j6_claim'))
        account.refresh_from_db()
        self.assertEqual(account.member, self.member)
        self.assertContains(r, 'Linked to Ganesh Saravanan')
        self.assertContains(r, 'can now see that this account is yours')

    def test_cutting_it_leaves_the_account_working(self):
        account = make('Anand')
        self._hub_in()
        self._j6_in(account)
        self.client.post(reverse('j6_claim'))
        self.client.post(reverse('j6_unclaim'))
        account.refresh_from_db()
        self.assertIsNone(account.member)
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)

    def test_only_one_chat_account_can_be_linked(self):
        first, second = make('Anand'), make('Mani')
        self._hub_in()
        self._j6_in(first)
        self.client.post(reverse('j6_claim'))
        self._j6_in(second)
        r = self.client.post(reverse('j6_claim'))
        second.refresh_from_db()
        self.assertIsNone(second.member)
        self.assertContains(r, 'One chat account each', status_code=409)

    # ---- door two: start a chat account from a hub account
    def test_the_real_name_is_only_suggested(self):
        self._hub_in()
        r = self.client.get(reverse('j6_start_hub'))
        self.assertContains(r, 'Ganesh Saravanan')           # in the field
        self.assertContains(r, 'not</b> your\n            SyncHub name')
        self.assertFalse(Account.objects.exists(), 'nothing is made until they choose a name')

    def test_a_different_chat_name_is_what_gets_stored(self):
        self._hub_in()
        self.client.post(reverse('j6_start_hub'), {'name': 'Bluejay'})
        account = Account.objects.get()
        self.assertEqual(account.name, 'BLUEJAY')
        self.assertEqual(account.member, self.member)

    def test_no_email_or_phone_ever_lands_on_a_chat_account(self):
        self._hub_in()
        self.client.post(reverse('j6_start_hub'), {'name': 'Bluejay'})
        account = Account.objects.get()
        self.assertFalse(hasattr(account, 'email'))
        self.assertFalse(hasattr(account, 'phone'))

    def test_the_self_id_is_still_the_credential(self):
        self._hub_in()
        self.client.post(reverse('j6_start_hub'), {'name': 'Bluejay'})
        account = Account.objects.get()
        self.client.post(reverse('member_logout'))
        self.client.get(reverse('j6_logout'))
        self.client.post(reverse('j6_login'), {'self_id': account.self_id})
        self.assertEqual(self.client.get(reverse('j6_inbox')).status_code, 200)

    def test_coming_back_to_the_second_door_opens_the_account_you_have(self):
        self._hub_in()
        self.client.post(reverse('j6_start_hub'), {'name': 'Bluejay'})
        self.client.get(reverse('j6_logout'))
        r = self.client.get(reverse('j6_start_hub'))
        self.assertRedirects(r, reverse('j6_inbox'))
        self.assertEqual(Account.objects.count(), 1)

    def test_a_stranger_is_sent_to_the_hub_first(self):
        r = self.client.get(reverse('j6_start_hub'))
        self.assertIn(reverse('member_login'), r['Location'])

    def test_the_welcome_screen_describes_both_doors(self):
        r = self.client.get(reverse('j6_welcome'))
        self.assertContains(r, 'no email, no number, no password')
        self.assertContains(r, 'Use my SyncHub account instead')
        self.assertContains(r, 'no way back')


class IdsPageWordingTests(TestCase):
    """What the IDs page promises has to match what is actually true of that account."""

    def setUp(self):
        self.member = Member.objects.create(name='Ganesh', email='g@example.com')
        hub.set_password(self.member, 'pw-for-tests')

    def test_an_unlinked_account_is_told_the_hard_truth(self):
        account = make('Anand')
        self.client.post(reverse('j6_login'), {'self_id': account.self_id})
        r = self.client.get(reverse('j6_ids'))
        self.assertContains(r, 'account is gone')
        self.assertContains(r, 'Remember it')

    def test_a_linked_account_is_not(self):
        account = make('Anand')
        Account.objects.filter(pk=account.pk).update(member=self.member)
        self.client.post(reverse('j6_login'), {'self_id': account.self_id})
        r = self.client.get(reverse('j6_ids'))
        self.assertNotContains(r, 'the account is gone')
        self.assertContains(r, 'losing the Self-ID\n        is survivable')
        self.assertContains(r, 'Ganesh')
