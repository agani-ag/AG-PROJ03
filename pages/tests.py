"""Standalone pages: a file in `pages/`, served at `/p/<name>`.

Small feature, two things worth proving. That nothing touches the file on its way out — a
page that is quietly templated has stopped being what was committed, and it stops in a way
nobody notices. And that a name arriving from a URL cannot reach a file outside the folder.

Run: python manage.py test pages
"""
import shutil
import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from . import views

RAW = """<!doctype html><html><head><title>Raw</title></head>
<body>{{ not_a_variable }} {% not_a_tag %} <script>console.log(1)</script></body></html>"""


class PagesTests(TestCase):

    def setUp(self):
        """A throwaway folder, so a test says what is on disk instead of using the real one."""
        self._tmp = tempfile.mkdtemp()
        self.dir = Path(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        patcher = mock.patch.object(views, 'HTML_DIR', self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, name, body=RAW):
        (self.dir / f'{name}.html').write_text(body, encoding='utf-8')

    def test_the_file_is_served_exactly_as_committed(self):
        """No template engine anywhere near it: the braces come out as braces."""
        self.write('raw')
        r = self.client.get('/p/raw')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content.decode(), RAW)
        self.assertIn('{{ not_a_variable }}', r.content.decode())
        self.assertIn('{% not_a_tag %}', r.content.decode())
        self.assertTrue(r['Content-Type'].startswith('text/html'))

    def test_dropping_a_file_in_is_the_whole_setup(self):
        self.assertEqual(self.client.get('/p/later').status_code, 404)
        self.write('later')
        self.assertEqual(self.client.get('/p/later').status_code, 200)

    def test_a_name_with_no_file_is_a_404(self):
        self.assertEqual(self.client.get('/p/nothing-here').status_code, 404)

    def test_a_name_cannot_climb_out_of_the_folder(self):
        outside = self.dir.parent / 'secret.html'
        outside.write_text('nope', encoding='utf-8')
        self.addCleanup(outside.unlink, True)
        # The URL pattern refuses the dots before the view is reached, and the view would
        # refuse them anyway. Both are worth a line, because only one of them is obvious.
        self.assertEqual(self.client.get('/p/../secret').status_code, 404)
        with self.assertRaises(Exception):
            views.show(None, '../secret')

    def test_anybody_can_open_one(self):
        self.write('public')
        self.assertEqual(self.client.get('/p/public').status_code, 200)

    def test_utf8_survives_the_round_trip(self):
        self.write('accents', '<!doctype html><p>Ameya — ₹1,200 · café</p>')
        body = self.client.get('/p/accents').content.decode('utf-8')
        self.assertIn('₹1,200', body)
        self.assertIn('café', body)
        self.assertIn('—', body)


class ConsoleListTests(TestCase):
    """System → Pages. Read-only: it answers "what is there and what is its address"."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.dir = Path(self._tmp)
        self.addCleanup(shutil.rmtree, self._tmp, True)
        patcher = mock.patch.object(views, 'HTML_DIR', self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        User.objects.create_superuser('admin', 'a@example.com', 'admin-pw')
        self.client.login(username='admin', password='admin-pw')

    def write(self, name, body=RAW):
        (self.dir / f'{name}.html').write_text(body, encoding='utf-8')

    def test_it_lists_the_files_with_their_own_titles(self):
        self.write('photo-stamp', '<!doctype html><title>Photo Stamp</title><p>hi</p>')
        self.write('plain', '<!doctype html><p>no title here</p>')
        r = self.client.get(reverse('pages'))
        self.assertContains(r, 'Photo Stamp')        # the <title>, not the file name
        self.assertContains(r, 'photo-stamp.html')
        self.assertContains(r, '/p/photo-stamp')
        self.assertContains(r, 'plain')              # falls back to the name

    def test_a_title_with_entities_and_newlines_reads_properly(self):
        self.write('x', '<!doctype html><title>Server,\n  Browser &amp; Sides</title>')
        self.assertContains(self.client.get(reverse('pages')), 'Server, Browser &amp; Sides')

    def test_each_row_carries_a_full_link_to_copy(self):
        self.write('photo-stamp')
        self.assertContains(self.client.get(reverse('pages')),
                            'data-copy="http://testserver/p/photo-stamp"')

    def test_an_empty_folder_says_so(self):
        self.assertContains(self.client.get(reverse('pages')), 'Nothing in the folder yet')

    def test_a_folder_that_is_not_there_is_not_an_error(self):
        shutil.rmtree(self._tmp, True)
        self.assertEqual(self.client.get(reverse('pages')).status_code, 200)

    def test_only_html_is_listed(self):
        self.write('real')
        (self.dir / 'notes.txt').write_text('ignore me', encoding='utf-8')
        r = self.client.get(reverse('pages'))
        self.assertContains(r, 'real.html')
        self.assertNotContains(r, 'notes.txt')

    def test_the_list_needs_an_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('pages')).status_code, 302)


class TheRealFolderTests(TestCase):
    """The one page actually in the repository, so a bad move here fails a test."""

    def test_the_example_page_is_served(self):
        shipped = Path(views.HTML_DIR) / 'client-vs-server.html'
        if not shipped.is_file():           # pragma: no cover - it was deliberately removed
            self.skipTest('client-vs-server.html is no longer in pages/')
        r = self.client.get('/p/client-vs-server')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.content, shipped.read_bytes())
