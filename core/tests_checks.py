"""The deploy checks, which nothing tested until the static files moved under them.

`core/checks.py` exists for mistakes that are silent: a server makes them, stays up, serves
pages, and you find out weeks later. That makes the checks themselves the one piece of this
project where "it looked right" is worth nothing — they have to be watched failing.

Run: python manage.py test core.tests_checks
"""
import tempfile
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase, override_settings

from core.checks import DEV_SECRET, check_deploy

LIVE = {'DEBUG': False, 'SECRET_KEY': 'a-real-one-for-tests', 'CRON_KEY': 'a-cron-key'}


def ids(problems):
    return sorted(p.id for p in problems)


class _Hosted(SimpleTestCase):
    """A throwaway BASE_DIR, so a test says what is on disk instead of asking the real repo."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name)

    def assets(self, *names):
        for name in names:
            path = self.base / 'static' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('/* css */', encoding='utf-8')

    def everything(self):
        self.assets('css/app.css', 'icons.svg', 'hibiscus/user.css', 'j6/user.css')

    def run_checks(self, **extra):
        with override_settings(BASE_DIR=self.base, **{**LIVE, **extra}):
            return check_deploy(None)


class StaticCheckTests(_Hosted):

    def test_a_complete_checkout_is_quiet(self):
        self.everything()
        self.assertEqual(ids(self.run_checks()), [])

    def test_a_missing_asset_is_named_rather_than_counted(self):
        self.assets('css/app.css', 'icons.svg')
        problems = self.run_checks()
        self.assertEqual(ids(problems), ['synchub.W003'])
        self.assertIn('hibiscus/user.css', problems[0].msg)
        self.assertIn('j6/user.css', problems[0].msg)
        self.assertNotIn('css/app.css', problems[0].msg)

    def test_a_leftover_staticfiles_folder_is_flagged(self):
        """The one hazard of serving static/ directly, and it fails silently.

        A web server still pointed at staticfiles/ keeps serving whatever was collected there.
        Nothing breaks, the site just stops picking up anything deployed after that. Django
        cannot see the mapping; it can see the folder that lets a wrong one keep working.
        """
        self.everything()
        (self.base / 'staticfiles' / 'css').mkdir(parents=True)
        problems = self.run_checks()
        self.assertEqual(ids(problems), ['synchub.W005'])
        self.assertIn('staticfiles', problems[0].hint)
        self.assertIn(str(self.base / 'static'), problems[0].hint)


class OtherDeployCheckTests(_Hosted):

    def test_nothing_is_checked_in_development(self):
        """All of these are about a hosted server, and would be noise on a laptop."""
        self.assertEqual(self.run_checks(DEBUG=True, SECRET_KEY=DEV_SECRET, CRON_KEY=''), [])

    def test_the_development_secret_key_is_an_error_not_a_warning(self):
        self.everything()
        problems = self.run_checks(SECRET_KEY=DEV_SECRET)
        self.assertEqual(ids(problems), ['synchub.E001'])
        self.assertEqual(problems[0].level, 40)          # ERROR, so `check` exits non-zero

    def test_an_empty_secret_key_never_gets_as_far_as_this_check(self):
        """Django refuses it on the way out of settings, which is why the '' in the tuple
        in checks.py is belt-and-braces rather than a branch anything can reach."""
        self.everything()
        with self.assertRaises(ImproperlyConfigured):
            self.run_checks(SECRET_KEY='')

    def test_no_cron_key_is_a_warning(self):
        self.everything()
        self.assertEqual(ids(self.run_checks(CRON_KEY='')), ['synchub.W004'])
