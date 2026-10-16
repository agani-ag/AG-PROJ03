"""Tests for the Windows client that need neither Windows nor a server.

    cd desktop && python -m unittest tests_desktop -v

The GUI cannot be tested here — it needs a desktop session and WebView2 — so what is covered is
everything underneath it: where the config goes, that it survives a crash mid-save, and that the
API client builds the right requests and turns failures into something a person can read.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

from j6desk.core import config                                    # noqa: E402
from j6desk.core.api import Api, ApiError                         # noqa: E402


class ConfigTests(unittest.TestCase):
    """The account follows the person, not the executable."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.appdata = os.environ.get('APPDATA')
        os.environ['APPDATA'] = self.tmp.name
        config.force_portable(False)

    def tearDown(self):
        config.force_portable(False)
        if self.appdata is None:
            os.environ.pop('APPDATA', None)
        else:
            os.environ['APPDATA'] = self.appdata
        self.tmp.cleanup()

    def test_it_lives_in_the_user_folder_not_beside_the_exe(self):
        where = config.path()
        self.assertEqual(where.name, 'config.json')
        self.assertEqual(where.parent.name, 'J6')
        self.assertTrue(str(where).startswith(self.tmp.name))

    def test_the_portable_switch_puts_it_beside_the_exe(self):
        config.force_portable(True)
        self.assertEqual(config.path().parent, config.exe_dir())
        self.assertEqual(config.path().name, 'j6.config')

    def test_a_missing_file_gives_the_defaults(self):
        data = config.load()
        self.assertEqual(data['self_id'], '')
        self.assertTrue(data['notify'])

    def test_a_broken_file_is_not_fatal(self):
        config.path().parent.mkdir(parents=True, exist_ok=True)
        config.path().write_text('{not json at all', encoding='utf-8')
        self.assertEqual(config.load()['self_id'], '')

    def test_saving_and_reading_back(self):
        config.update(self_id='j6s-abcd', name='Anand', server='https://x.test')
        again = config.load()
        self.assertEqual(again['self_id'], 'j6s-abcd')
        self.assertEqual(again['name'], 'Anand')

    def test_the_write_is_atomic(self):
        """A crash mid-save must leave the old file, not half of a new one."""
        config.update(self_id='j6s-old')
        with patch('j6desk.core.config.os.replace', side_effect=OSError('power cut')):
            with self.assertRaises(OSError):
                config.update(self_id='j6s-new')
        self.assertEqual(config.load()['self_id'], 'j6s-old')
        leftovers = list(config.path().parent.glob('*.tmp'))
        self.assertEqual(len(leftovers), 1, 'the temp file is what survives, not a broken config')

    def test_signing_out_removes_the_file(self):
        config.update(self_id='j6s-abcd')
        self.assertTrue(config.forget())
        self.assertFalse(config.path().exists())
        self.assertEqual(config.load()['self_id'], '')      # and reading still works

    def test_nothing_about_the_chat_is_kept(self):
        """The client holds a key, not a copy of anybody's conversation."""
        for key in config.DEFAULTS:
            self.assertNotIn(key, ('messages', 'chats', 'cache'))


class ApiTests(unittest.TestCase):
    """What goes on the wire, and what comes back when it goes wrong."""

    def setUp(self):
        self.api = Api('https://synchub.example.com/', 'j6s-key')
        self.sent = {}

    def _answer(self, payload, status=200):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *exc): return False
            def read(self): return json.dumps(payload).encode()

        def fake(request, timeout=None):
            self.sent = {
                'url': request.full_url,
                'method': request.get_method(),
                'headers': dict(request.header_items()),
                'body': json.loads(request.data) if request.data else None,
            }
            return Response()
        return fake

    def test_the_self_id_travels_in_a_header_not_a_cookie(self):
        with patch('urllib.request.urlopen', self._answer({'name': 'Anand', 'chats': []})):
            self.api.hello()
        self.assertEqual(self.sent['headers'].get('X-j6-self'), 'j6s-key')
        self.assertNotIn('Cookie', self.sent['headers'])

    def test_the_server_address_is_normalised(self):
        with patch('urllib.request.urlopen', self._answer({})):
            self.api.hello()
        self.assertEqual(self.sent['url'], 'https://synchub.example.com/j6/api/hello')

    def test_polling_carries_the_chat_and_the_last_seq(self):
        with patch('urllib.request.urlopen', self._answer({'chats': []})):
            self.api.poll(chat=7, since=42)
        self.assertIn('chat=7', self.sent['url'])
        self.assertIn('since=42', self.sent['url'])

    def test_creating_an_account_needs_no_credential_and_keeps_the_one_it_gets(self):
        answer = {'account_id': 'j6-aaaa', 'self_id': 'j6s-bbbb', 'name': 'Riya'}
        fresh = Api('https://synchub.example.com')
        with patch('urllib.request.urlopen', self._answer(answer)):
            made = fresh.start('Riya')
        self.assertNotIn('X-j6-self', self.sent['headers'])
        self.assertEqual(made['self_id'], 'j6s-bbbb')
        self.assertEqual(fresh.self_id, 'j6s-bbbb')         # ready for the next call

    def test_sending_posts_json(self):
        with patch('urllib.request.urlopen', self._answer({'seq': 1})):
            self.api.send(3, 'hello')
        self.assertEqual(self.sent['method'], 'POST')
        self.assertEqual(self.sent['body'], {'chat': 3, 'body': 'hello'})

    def test_a_refusal_becomes_a_sentence(self):
        import urllib.error

        def refuse(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 400, 'Bad Request', {},
                                         _Body(json.dumps({'error': 'That is too long.'})))
        with patch('urllib.request.urlopen', refuse):
            with self.assertRaises(ApiError) as caught:
                self.api.send(1, 'x' * 5000)
        self.assertEqual(str(caught.exception), 'That is too long.')

    def test_a_bad_self_id_says_which_problem_it_is(self):
        import urllib.error

        def refuse(request, timeout=None):
            raise urllib.error.HTTPError(request.full_url, 401, 'Unauthorized', {}, _Body('{}'))
        with patch('urllib.request.urlopen', refuse):
            with self.assertRaises(ApiError) as caught:
                self.api.hello()
        self.assertIn('not recognised', str(caught.exception))

    def test_an_unreachable_server_names_itself(self):
        import urllib.error

        def refuse(request, timeout=None):
            raise urllib.error.URLError('connection refused')
        with patch('urllib.request.urlopen', refuse):
            with self.assertRaises(ApiError) as caught:
                self.api.hello()
        self.assertIn('synchub.example.com', str(caught.exception))


class _Body:
    """The file-like object an HTTPError carries."""

    def __init__(self, text):
        self.text = text.encode()

    def read(self):
        return self.text

    def close(self):
        """HTTPError treats its body like a temp file and closes it on the way out."""


class EntryPointTests(unittest.TestCase):
    """One executable, two faces — the argument parsing that decides which."""

    def test_version_prints_and_exits_cleanly(self):
        from j6desk import __main__ as entry
        from io import StringIO
        out = StringIO()
        with patch('sys.stdout', out):
            code = entry.main(['--version'])
        self.assertEqual(code, 0)
        self.assertIn('J6', out.getvalue())

    def test_portable_is_honoured_for_one_run_only(self):
        from j6desk import __main__ as entry
        with patch('j6desk.ui_web.run', return_value=0) as window:
            entry.main(['--portable'])
        self.assertTrue(window.called)
        self.assertTrue(config.is_portable())
        config.force_portable(False)

    def test_cli_goes_to_the_terminal_client(self):
        from j6desk import __main__ as entry
        with patch('j6desk.__main__.attach_console', return_value=True), \
             patch('j6desk.ui_term.run', return_value=0) as term:
            code = entry.main(['--cli', 'whoami'])
        self.assertEqual(code, 0)
        self.assertEqual(term.call_args[0][0], ['whoami'])    # the rest is passed through


if __name__ == '__main__':
    unittest.main()


class TrayTests(unittest.TestCase):
    """The tray is optional, and everything about it has to survive its absence."""

    def test_it_reports_whether_this_build_has_one(self):
        from j6desk.core import tray
        self.assertIsInstance(tray.available(), bool)

    def test_a_build_without_pystray_still_gives_a_working_object(self):
        from j6desk.core import tray
        with patch('j6desk.core.tray.pystray', None):
            icon = tray.Tray(lambda: None, lambda: None, lambda: None, lambda: None)
            self.assertFalse(icon.start())        # says so rather than raising
            icon.set_unread(3)                    # and stays harmless afterwards
            icon.stop()
            self.assertEqual(icon.title, '')


class VersionTests(unittest.TestCase):
    def test_the_client_and_the_server_agree_on_a_version_string(self):
        from j6desk import __version__
        server = (Path(__file__).resolve().parents[1] / 'j6' / 'views_api.py').read_text(
            encoding='utf-8')
        self.assertIn(f"VERSION = '{__version__}'", server,
                      'desktop/j6desk/__init__.py and j6/views_api.py have drifted apart')
