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
from .models import Settings

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
        self.assertIn('Mani', visible)
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
        self.assertEqual(self.anand.name, 'Anand K')
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
