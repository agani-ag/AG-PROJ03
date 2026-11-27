"""Standalone pages: a file in `pages/templates/pages/html/`, served at `/p/<name>`.

Small feature, two things worth proving. That nothing touches the file on its way out — a
page that is quietly templated has stopped being what was committed, and it stops in a way
nobody notices. And that a name arriving from a URL cannot reach a file outside the folder.

Run: python manage.py test pages
"""
import re
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
        # newline='' so the bytes on disk are exactly these bytes. The default rewrites line
        # endings on Windows, and then a byte-for-byte test is testing the platform instead
        # of the view.
        (self.dir / f'{name}.html').write_text(body, encoding='utf-8', newline='')

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
        # newline='' so the bytes on disk are exactly these bytes. The default rewrites line
        # endings on Windows, and then a byte-for-byte test is testing the platform instead
        # of the view.
        (self.dir / f'{name}.html').write_text(body, encoding='utf-8', newline='')

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
    """The pages actually in the repository, so moving the folder fails a test.

    Deliberately not a list of names: whatever is in there should be served, and a test that
    has to be edited every time a page is added is a test people start editing without
    reading.
    """

    def test_every_page_in_the_folder_is_served_byte_for_byte(self):
        shipped = sorted(Path(views.HTML_DIR).glob('*.html'))
        self.assertTrue(shipped, f'nothing in {views.HTML_DIR} — has the folder moved?')
        for path in shipped:
            with self.subTest(page=path.name):
                r = self.client.get(f'/p/{path.stem}')
                self.assertEqual(r.status_code, 200)
                self.assertEqual(r.content, path.read_bytes())

    def test_the_console_lists_them(self):
        shipped = sorted(Path(views.HTML_DIR).glob('*.html'))
        User.objects.create_superuser('admin', 'a@example.com', 'admin-pw')
        self.client.login(username='admin', password='admin-pw')
        body = self.client.get(reverse('pages')).content.decode()
        for path in shipped:
            self.assertIn(path.name, body)

class TheCharacterPageTests(TestCase):
    """The one page with a character on it, and the few things about her that rot silently.

    She answers in your own voice played back higher, not in a synthesised one. That is the
    whole reason she works in Tamil: a browser cannot speak Tamil without a Tamil voice
    installed, and almost none have one, so the earlier version was mute for the people this
    page is written for. Nothing here can prove she looks right. These prove the parts that
    would fail quietly — a reference to an element that no longer exists, a return of the
    speech API, a network dependency on a page that has to paint on a bad connection.
    """

    page = Path(views.HTML_DIR) / 'melvins-image-station.html'

    def setUp(self):
        self.assertTrue(self.page.exists(), f'{self.page.name} has moved or gone')
        self.html = self.page.read_text(encoding='utf-8')
        self.script = re.search(r'<script>(.*?)</script>', self.html, re.S).group(1)

    def test_every_element_the_script_reaches_for_is_in_the_page(self):
        """The bug this catches: deleting markup and leaving the code that reads it.

        It happened here. The voice picker went, and two handlers kept calling a function
        that had gone with it — which throws on the first tap and takes every later
        reaction down with it, while the page still looks perfectly fine.
        """
        wanted = set(re.findall(r"""\$\('#([A-Za-z0-9_-]+)'\)""", self.script))
        wanted |= set(re.findall(r"""getElementById\('([A-Za-z0-9_-]+)'\)""", self.script))
        self.assertTrue(wanted, 'found no element lookups at all — has the script changed shape?')
        present = set(re.findall(r'\bid="([A-Za-z0-9_-]+)"', self.html))
        self.assertEqual(wanted - present, set())

    def test_she_plays_your_voice_back_rather_than_speaking(self):
        for gone in ('speechSynthesis', 'SpeechRecognition', 'SpeechSynthesisUtterance'):
            self.assertNotIn(gone, self.html, f'{gone} is back; Tamil will be silent again')
        for needed in ('getUserMedia', 'MediaRecorder', 'decodeAudioData', 'playbackRate'):
            self.assertIn(needed, self.script, f'{needed} has gone; she cannot answer')

    def test_the_pitch_is_the_speed_and_is_actually_higher(self):
        """A rate of 1 would hand back your own voice and still look like it worked."""
        pitch = float(re.search(r'var PITCH = ([\d.]+);', self.script).group(1))
        self.assertGreater(pitch, 1.1)
        self.assertLess(pitch, 1.6)
        self.assertIn('source.playbackRate.value = PITCH;', self.script)

    def test_she_says_everything_in_both_languages(self):
        """Tamil and English have to carry the same keys, or one of them shows `undefined`."""
        blocks = re.findall(r'^    (ta|en): \{(.*?)^    \}', self.script, re.S | re.M)
        self.assertEqual(len(blocks), 2, 'expected a Tamil block and an English one')
        keys = [set(re.findall(r'^      (\w+):', body, re.M)) for _, body in blocks]
        self.assertEqual(keys[0], keys[1])
        self.assertTrue({'hold', 'busy', 'ask', 'brief', 'spoilt', 'none'} <= keys[0])

    def test_it_needs_nothing_off_the_network(self):
        """It replaced a CDN stylesheet. On a phone on a bad line that was the whole page."""
        outside = re.findall(r'(?:src|href)="(https?:)?//[^"]+', self.html)
        self.assertEqual(outside, [])
