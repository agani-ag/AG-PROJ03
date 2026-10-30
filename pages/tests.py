"""Pages: a file in the repository, a row in the console, and one address.

The thing worth testing hardest is that nothing touches the file on its way out. A standalone
page that gets templated, rewritten or wrapped has stopped being the thing that was committed,
and it will have stopped in a way nobody notices until the page misbehaves in front of
somebody.

Run: python manage.py test pages
"""
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core import syncup_links
from core.models import HubSettings, SyncUpLink

from . import models as page_models
from .models import Page

RAW = """<!doctype html><html><head><title>Raw</title></head>
<body>{{ not_a_variable }} {% not_a_tag %} <script>console.log(1)</script></body></html>"""


class _WithFiles(TestCase):
    """A throwaway `pages/html/`, so a test says what is on disk instead of touching the repo."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.dir = Path(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        patcher = mock.patch.object(page_models, 'HTML_DIR', self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, slug, body=RAW):
        (self.dir / f'{slug}.html').write_text(body, encoding='utf-8')

    def page(self, slug='raw', **fields):
        self.write(slug)
        return Page.objects.create(slug=slug, title=fields.pop('title', 'Raw'), **fields)


class ServingTests(_WithFiles):

    def test_the_file_is_served_exactly_as_committed(self):
        """No template engine anywhere near it: the braces come out as braces."""
        self.page()
        r = self.client.get('/p/raw')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode(), RAW)
        self.assertIn('{{ not_a_variable }}', r.content.decode())
        self.assertIn('{% not_a_tag %}', r.content.decode())
        self.assertTrue(r['Content-Type'].startswith('text/html'))

    def test_switching_it_off_closes_the_address(self):
        page = self.page(is_enabled=False)
        self.assertEqual(self.client.get('/p/raw').status_code, 404)
        Page.objects.filter(pk=page.pk).update(is_enabled=True)
        self.assertEqual(self.client.get('/p/raw').status_code, 200)

    def test_a_row_whose_file_went_away_is_a_404_not_a_500(self):
        """A deploy can take the file and leave the row. That must not be an error page."""
        self.page()
        (self.dir / 'raw.html').unlink()
        self.assertEqual(self.client.get('/p/raw').status_code, 404)

    def test_an_address_nobody_set_up_is_a_404(self):
        self.write('orphan')
        self.assertEqual(self.client.get('/p/orphan').status_code, 404)

    def test_opening_one_is_counted(self):
        page = self.page()
        self.client.get('/p/raw')
        self.client.get('/p/raw')
        page.refresh_from_db()
        self.assertEqual(page.open_count, 2)
        self.assertIsNotNone(page.last_opened_at)

    def test_nobody_has_to_sign_in(self):
        self.page()
        self.assertEqual(self.client.get('/p/raw').status_code, 200)


class SandboxTests(_WithFiles):
    """These pages are public and run on this site's address, so this header is the fence."""

    def test_a_sandboxed_page_is_given_an_origin_of_its_own(self):
        self.page(sandboxed=True)
        policy = self.client.get('/p/raw')['Content-Security-Policy']
        self.assertIn('sandbox', policy)
        self.assertIn('allow-scripts', policy)
        # The missing one is the mechanism: with it, the fence is not there at all.
        self.assertNotIn('allow-same-origin', policy)

    def test_an_unsandboxed_page_is_sent_without_the_header(self):
        self.page(sandboxed=False)
        self.assertNotIn('Content-Security-Policy', self.client.get('/p/raw'))

    def test_it_is_off_by_default(self):
        """These files are committed and reviewed, so the default matches how they arrive."""
        self.assertFalse(Page._meta.get_field('sandboxed').default)


class FileLookupTests(_WithFiles):

    def test_a_slug_cannot_climb_out_of_the_folder(self):
        """The validator forbids it and the form refuses it; this is the floor under both."""
        outside = Path(self._tmp).parent / 'secret.html'
        outside.write_text('nope', encoding='utf-8')
        self.addCleanup(outside.unlink, True)
        page = Page(slug='../secret', title='Climb')
        self.assertIsNone(page.file)
        self.assertEqual(page.html, '')

    def test_files_with_no_page_are_found(self):
        self.write('spare-one')
        self.write('spare-two')
        self.page('taken')
        self.assertEqual(sorted(page_models.files_on_disk()),
                         ['spare-one', 'spare-two', 'taken'])

    def test_size_is_reported_and_survives_a_missing_file(self):
        page = self.page()
        self.assertGreater(page.size_kb, 0)
        (self.dir / 'raw.html').unlink()
        self.assertEqual(page.size_kb, 0)
        self.assertFalse(page.is_ready)


class ConsoleTests(_WithFiles):

    def setUp(self):
        super().setUp()
        User.objects.create_superuser('admin', 'a@example.com', 'admin-pw')
        self.client.login(username='admin', password='admin-pw')

    def test_the_list_offers_a_file_that_has_no_page_yet(self):
        self.write('photo-stamp')
        r = self.client.get(reverse('pages'))
        self.assertContains(r, 'photo-stamp.html')
        self.assertContains(r, 'no page set up')

    def test_setting_one_up(self):
        self.write('photo-stamp')
        r = self.client.post(reverse('page_new'), {
            'slug': 'photo-stamp', 'title': 'Photo Stamp', 'description': 'Stamp a date on it',
            'icon': 'camera', 'colour': 'teal', 'is_enabled': 'on', 'sort_order': 0})
        self.assertRedirects(r, reverse('pages'))
        page = Page.objects.get(slug='photo-stamp')
        self.assertEqual(page.created_by.username, 'admin')
        self.assertEqual(self.client.get('/p/photo-stamp').status_code, 200)

    def test_a_page_with_no_file_behind_it_is_refused(self):
        r = self.client.post(reverse('page_new'), {
            'slug': 'not-here', 'title': 'Missing', 'icon': 'link', 'colour': 'slate',
            'sort_order': 0})
        self.assertContains(r, 'no pages/html/not-here.html')
        self.assertEqual(Page.objects.count(), 0)

    def test_the_file_name_cannot_be_changed_afterwards(self):
        """It names the file as well as the address, so editing it would orphan the row."""
        page = self.page('photo-stamp', title='Photo Stamp')
        self.client.post(reverse('page_edit', args=[page.pk]), {
            'slug': 'something-else', 'title': 'Photo Stamp', 'icon': 'link',
            'colour': 'slate', 'is_enabled': 'on', 'sort_order': 0})
        page.refresh_from_db()
        self.assertEqual(page.slug, 'photo-stamp')

    def test_the_switches(self):
        page = self.page()
        for what, field in [('enabled', 'is_enabled'), ('syncup', 'in_syncup'),
                            ('sandbox', 'sandboxed')]:
            before = getattr(Page.objects.get(pk=page.pk), field)
            self.client.post(reverse('page_toggle', args=[page.pk, what]))
            self.assertNotEqual(getattr(Page.objects.get(pk=page.pk), field), before, what)

    def test_forgetting_a_page_leaves_the_file_alone(self):
        page = self.page('photo-stamp', title='Photo Stamp')
        self.client.post(reverse('page_delete', args=[page.pk]))
        self.assertEqual(Page.objects.count(), 0)
        self.assertTrue((self.dir / 'photo-stamp.html').is_file())
        # And it is offered again as something to set up.
        self.assertContains(self.client.get(reverse('pages')), 'photo-stamp.html')

    def test_the_console_needs_an_admin(self):
        page = self.page()
        self.client.logout()
        for url in (reverse('pages'), reverse('page_new'),
                    reverse('page_edit', args=[page.pk])):
            self.assertEqual(self.client.get(url).status_code, 302, url)


class SyncUpLinkTests(_WithFiles):
    """A page is a link subject exactly as a module is — that is the whole integration."""

    def setUp(self):
        super().setUp()
        config = HubSettings.load()
        config.syncup_base = 'https://syncup.example.com'
        config.syncup_links_key = 'a-key'
        config.site_base = 'https://hub.example.com'
        config.save()

    def test_a_page_is_namespaced_away_from_the_modules(self):
        page = self.page('j6')                      # same name as a module, on purpose
        self.assertEqual(page.link_key, 'page-j6')
        self.assertNotIn(page.link_key, {'hibiscus', 'j6', 'pocket'})

    def test_only_a_page_that_asks_for_a_link_offers_one(self):
        page = self.page(in_syncup=False)
        self.assertFalse(page.link_subject.has_syncup_link)
        Page.objects.filter(pk=page.pk).update(in_syncup=True)
        self.assertTrue(Page.objects.get(pk=page.pk).link_subject.has_syncup_link)

    def test_a_page_that_is_switched_off_is_not_ready_to_send(self):
        page = self.page(in_syncup=True, is_enabled=False)
        self.assertFalse(syncup_links.is_built(page.link_subject))

    def test_it_appears_on_the_system_page_beside_the_modules(self):
        self.page('photo-stamp', title='Photo Stamp', in_syncup=True)
        keys = [r['module'].key for r in syncup_links.rows()['rows']]
        self.assertIn('page-photo-stamp', keys)
        self.assertIn('hibiscus', keys)

    def test_what_syncup_is_told_about_a_page(self):
        self.page('photo-stamp', title='Photo Stamp', description='Stamp a date on it',
                  icon='camera', colour='teal', in_syncup=True)
        body = syncup_links.details(Page.objects.get(slug='photo-stamp').link_subject)
        self.assertEqual(body['ref'], 'synchub:page-photo-stamp')
        self.assertEqual(body['url'], 'https://hub.example.com/p/photo-stamp')
        self.assertEqual((body['title'], body['icon'], body['color']),
                         ('Photo Stamp', 'camera', 'teal'))
        self.assertFalse(body['give_token'])        # a standalone page has nothing to push

    def test_a_deleted_page_leaves_a_link_that_can_still_be_hidden(self):
        """Otherwise the link stays in everybody's app with nothing on this side admitting it."""
        page = self.page('photo-stamp', title='Photo Stamp', in_syncup=True)
        SyncUpLink.objects.create(module=page.link_key, sent_at='2026-10-30T10:00:00Z')
        page.delete()
        row = next(r for r in syncup_links.rows()['rows']
                   if r['module'].key == 'page-photo-stamp')
        self.assertEqual(row['label'], 'No longer offered')
        self.assertTrue(row['can_hide'])
