"""System → Tools in SyncUp: SyncHub's modules as General links in the SyncUp app.

urlopen is mocked throughout — nothing here reaches a real SyncUp.
Run: python manage.py test core.tests_syncup_links
"""
import io
import json
import urllib.error
from unittest import mock

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from core import syncup_links
from core.models import HubSettings, SyncUpLink
from core.modules import MODULES_BY_KEY, Module

HIBISCUS = MODULES_BY_KEY['hibiscus']
URLOPEN = 'core.syncup_links.urllib.request.urlopen'


class _Reply:
    def __init__(self, body):
        self.body = json.dumps(body).encode()

    def read(self):
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def reply(admin_off=False, link_id=7):
    return _Reply({'success': True, 'created': True, 'link': {'id': link_id, 'admin_off': admin_off}})


def sent(urlopen):
    request = urlopen.call_args[0][0]
    return request.full_url, json.loads(request.data)


def set_up_hub():
    hub = HubSettings.load()
    hub.site_base = 'https://synchub.example.com'
    hub.syncup_base = 'https://syncup.example.com'
    hub.syncup_links_key = 'links-key'
    hub.save()
    return hub


class SendTests(TestCase):
    def setUp(self):
        set_up_hub()

    @mock.patch(URLOPEN, return_value=reply())
    def test_send_posts_the_modules_link_with_the_key(self, urlopen):
        self.assertEqual(syncup_links.send(HIBISCUS), (True, ''))
        url, body = sent(urlopen)
        self.assertEqual(url, 'https://syncup.example.com/app/v1/hub/links')
        self.assertEqual(body['key'], 'links-key')
        self.assertEqual(body['ref'], 'synchub:hibiscus')
        self.assertEqual(body['url'], 'https://synchub.example.com/hibiscus/')
        self.assertEqual((body['icon'], body['color'], body['give_token'], body['active']),
                         ('flower', 'rose', True, True))
        row = SyncUpLink.objects.get(module='hibiscus')
        self.assertEqual(row.syncup_id, '7')
        self.assertTrue(row.sent_at)

    @mock.patch(URLOPEN)
    def test_nothing_goes_without_the_key(self, urlopen):
        hub = HubSettings.load()
        hub.syncup_links_key = ''
        hub.save()
        ok, error = syncup_links.send(HIBISCUS)
        self.assertFalse(ok)
        self.assertIn('links key', error)
        urlopen.assert_not_called()

    @mock.patch(URLOPEN, return_value=reply())
    def test_hide_sends_only_the_switch_and_send_all_keeps_it_hidden(self, urlopen):
        syncup_links.send(HIBISCUS)
        self.assertEqual(syncup_links.hide(HIBISCUS), (True, ''))
        self.assertEqual(set(sent(urlopen)[1]), {'key', 'ref', 'active'})
        self.assertTrue(SyncUpLink.objects.get(module='hibiscus').hidden)
        syncup_links.send(HIBISCUS)
        self.assertFalse(sent(urlopen)[1]['active'], 'Send keeps a hidden link hidden')
        syncup_links.send(HIBISCUS, active=True)
        self.assertFalse(SyncUpLink.objects.get(module='hibiscus').hidden)

    @mock.patch(URLOPEN)
    def test_hide_needs_it_sent_first(self, urlopen):
        ok, _ = syncup_links.hide(HIBISCUS)
        self.assertFalse(ok)
        urlopen.assert_not_called()

    @mock.patch(URLOPEN, return_value=reply(admin_off=True))
    def test_syncups_switch_off_is_remembered(self, urlopen):
        syncup_links.send(HIBISCUS)
        row = next(r for r in syncup_links.rows()['rows'] if r['module'] is HIBISCUS)
        self.assertEqual(row['label'], 'Switched off in SyncUp')
        self.assertTrue(row['admin_off'])

    @mock.patch(URLOPEN)
    def test_a_refusal_is_kept_and_shown(self, urlopen):
        urlopen.side_effect = urllib.error.HTTPError(
            'https://syncup.example.com/app/v1/hub/links', 403, 'Forbidden', {},
            io.BytesIO(b'{"success": false, "error": "Wrong links key."}'))
        ok, error = syncup_links.send(HIBISCUS)
        self.assertFalse(ok)
        self.assertIn('403', error)
        self.assertIn('Wrong links key', error)
        self.assertIn('Wrong links key', SyncUpLink.objects.get(module='hibiscus').last_error)

    @mock.patch(URLOPEN, side_effect=urllib.error.URLError('no route'))
    def test_an_unreachable_syncup_never_raises(self, urlopen):
        ok, error = syncup_links.send(HIBISCUS)
        self.assertFalse(ok)
        self.assertIn('URLError', error)

    @mock.patch(URLOPEN, return_value=reply())
    def test_a_change_here_shows_as_send_again(self, urlopen):
        syncup_links.send(HIBISCUS)
        label = lambda: next(r['label'] for r in syncup_links.rows()['rows'] if r['module'] is HIBISCUS)
        self.assertEqual(label(), 'In SyncUp')
        hub = HubSettings.load()
        hub.site_base = 'https://hub.example.org'
        hub.save()
        self.assertEqual(label(), 'Changed — send again')

    @mock.patch(URLOPEN)
    def test_a_module_without_pages_is_not_sent(self, urlopen):
        later = Module(key='later', title='Later', icon='package', group='Modules', description='',
                       url_name='no_such_page', syncup_path='/later/')
        ok, error = syncup_links.send(later)
        self.assertFalse(ok)
        self.assertIn('built', error)
        urlopen.assert_not_called()


class PageTests(TestCase):
    def setUp(self):
        set_up_hub()
        self.client.force_login(User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234'))

    def test_buttons_need_the_admin(self):
        self.client.logout()
        r = self.client.post(reverse('syncup_link_send', args=['hibiscus']))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(SyncUpLink.objects.exists())

    @mock.patch(URLOPEN, return_value=reply())
    def test_send_hide_and_send_all_buttons(self, urlopen):
        r = self.client.post(reverse('syncup_link_send', args=['hibiscus']))
        self.assertRedirects(r, reverse('system_settings') + '#syncup-links', fetch_redirect_response=False)
        self.assertTrue(SyncUpLink.objects.get(module='hibiscus').sent_at)
        self.client.post(reverse('syncup_link_visibility', args=['hibiscus']), {'show': '0'})
        self.assertTrue(SyncUpLink.objects.get(module='hibiscus').hidden)
        self.client.post(reverse('syncup_links_send_all'))
        self.assertTrue(SyncUpLink.objects.filter(module='j6').exists())
        self.assertTrue(SyncUpLink.objects.get(module='hibiscus').hidden)

    def test_unknown_module(self):
        self.assertEqual(self.client.post(reverse('syncup_link_send', args=['nope'])).status_code, 404)

    def test_the_key_is_saved_with_the_other_values(self):
        self.client.post(reverse('system_settings'), {
            'site_base': 'https://synchub.example.com',
            'syncup_base': 'https://syncup.example.com',
            'syncup_notify_key': 'notify',
            'syncup_links_key': 'new-links-key',
        })
        self.assertEqual(HubSettings.load().syncup_links_key, 'new-links-key')

    def test_the_card_lists_the_tools(self):
        body = self.client.get(reverse('system_settings')).content.decode()
        self.assertIn('Tools in SyncUp', body)
        self.assertIn('https://synchub.example.com/hibiscus/', body)
        self.assertIn('Add to SyncUp', body)
