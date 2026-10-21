"""SyncHub's tools as links in the SyncUp app — for everyone, under "For everyone".

SyncUp keeps one General link per module, made and updated through its hub/links endpoint with
the links key its admin hands out. SyncHub only ever adds, updates or hides its own links (a
`synchub:<module>` ref each). SyncUp's admin can switch one off, and that wins: whatever SyncHub
sends afterwards changes the details but never turns it back on.

Nothing goes on a schedule. A link in everybody's app is not something to change behind the
admin's back, so the System page's buttons do the sending.

Shaped like core/push.py: the settings come from HubSettings, every call returns `(ok, error)`,
and nothing raises.
"""
import hashlib
import json
import logging
import urllib.error
import urllib.request

from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from .models import HubSettings, SyncUpLink
from .modules import MODULES

logger = logging.getLogger(__name__)

REF_PREFIX = 'synchub:'
SEND_TIMEOUT = 5
PAGE_TIMEOUT = 10         # somebody is watching the page, so give SyncUp longer


def ref(module):
    return REF_PREFIX + module.key


def is_built(module):
    """True once the module's own pages exist — a link to a page that isn't there helps nobody."""
    if not module.url_name:
        return False
    try:
        reverse(module.url_name)
    except NoReverseMatch:
        return False
    return True


def details(module, hub=None):
    """What SyncUp is told about a module's link — without the key or the shown/hidden switch."""
    hub = hub or HubSettings.load()
    return {
        'ref': ref(module),
        'title': module.title,
        'url': hub.url_for(module.syncup_path),
        'description': module.syncup_description,
        'icon': module.syncup_icon,
        'color': module.syncup_color,
        'give_token': module.syncup_token,
        'sso': module.syncup_sso,
    }


def _hash(body):
    return hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()


def problem(hub=None, need_site=True):
    """Why nothing can be sent right now, or ''."""
    hub = hub or HubSettings.load()
    if not hub.syncup_base:
        return 'Set the SyncUp address first.'
    if not hub.syncup_links_key:
        return 'Paste the SyncUp links key first (SyncUp admin → General links).'
    if need_site and not hub.site_base:
        return "Set this site's address first — the links open pages on it."
    return ''


def _post(hub, body, timeout):
    """POST to SyncUp's hub/links. Returns `(reply, error)`; reply is None when it didn't work."""
    endpoint = hub.syncup_base.rstrip('/') + '/app/v1/hub/links'
    data = json.dumps({**body, 'key': hub.syncup_links_key}).encode()
    request = urllib.request.Request(endpoint, data=data, headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b'{}'), ''
    except urllib.error.HTTPError as exc:
        # SyncUp says what was wrong in its JSON; anything else in front of it (a proxy's error
        # page) says nothing useful, so only the status is kept then.
        try:
            said = json.loads(exc.read() or b'{}').get('error') or ''
        except (ValueError, AttributeError):
            said = ''
        return None, (f'SyncUp said {exc.code}: {said}' if said else f'SyncUp said {exc.code}')[:200]
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        return None, f'{type(exc).__name__}: {exc}'[:200]


def _record(module, reply, error, sent_hash=None, hidden=None):
    row, _ = SyncUpLink.objects.get_or_create(module=module.key)
    now = timezone.now()
    if reply is None:
        row.last_error, row.error_at = error, now
        row.save()
        logger.warning('syncup link %s: %s', module.key, error)
        return False, error
    link = reply.get('link') or {}
    if sent_hash is not None:
        row.sent_hash = sent_hash
    if hidden is not None:
        row.hidden = hidden
    row.sent_at = now
    row.admin_off = bool(link.get('admin_off'))
    row.syncup_id = str(link.get('id') or row.syncup_id)[:40]
    row.last_error, row.error_at = '', None
    row.save()
    return True, ''


def send(module, active=None, timeout=SEND_TIMEOUT):
    """Add a module's link to SyncUp, or update it. Returns `(ok, error)`.

    `active` None keeps it shown or hidden as SyncHub last asked; True shows it, False hides it.
    """
    hub = HubSettings.load()
    why = problem(hub)
    if why:
        return False, why
    if not module.has_syncup_link:
        return False, 'this module has no SyncUp link'
    if not is_built(module):
        return False, "this module's pages aren't built yet"
    if active is None:
        row = SyncUpLink.objects.filter(module=module.key).first()
        active = not (row and row.hidden)
    body = details(module, hub)
    reply, error = _post(hub, {**body, 'active': active}, timeout)
    return _record(module, reply, error, sent_hash=_hash(body), hidden=not active)


def hide(module, timeout=SEND_TIMEOUT):
    """Hide a link SyncHub sent before. Only the ref and the switch go — nothing else changes."""
    hub = HubSettings.load()
    why = problem(hub, need_site=False)
    if why:
        return False, why
    row = SyncUpLink.objects.filter(module=module.key).first()
    if row is None or row.sent_at is None:
        return False, "it isn't in SyncUp yet"
    reply, error = _post(hub, {'ref': ref(module), 'active': False}, timeout)
    return _record(module, reply, error, hidden=True)


def send_all(timeout=SEND_TIMEOUT):
    """Send every module that has a SyncUp link and whose pages exist. Returns [(module, ok, error)]."""
    return [(m, *send(m, timeout=timeout)) for m in MODULES if m.has_syncup_link and is_built(m)]


def rows():
    """The System page's list: each module with a SyncUp link, or one SyncHub sent before."""
    hub = HubSettings.load()
    saved = {r.module: r for r in SyncUpLink.objects.all()}
    can_send = not problem(hub)
    out = []
    for m in MODULES:
        row = saved.get(m.key)
        if not m.has_syncup_link and row is None:
            continue
        built = is_built(m)
        sent = row is not None and row.sent_at is not None
        url = hub.url_for(m.syncup_path) if m.has_syncup_link else ''
        if not m.has_syncup_link:
            label, badge = 'No longer offered', 'badge-warning'
        elif not built:
            label, badge = 'Pages not built yet', ''
        elif not sent:
            label, badge = 'Not in SyncUp yet', ''
        elif row.admin_off:
            label, badge = 'Switched off in SyncUp', 'badge-warning'
        elif row.hidden:
            label, badge = 'Hidden', ''
        elif row.sent_hash != _hash(details(m, hub)):
            label, badge = 'Changed — send again', 'badge-warning'
        else:
            label, badge = 'In SyncUp', 'badge-success'
        out.append({
            'module': m,
            'url': url,
            'label': label,
            'badge': badge,
            'sent_at': row.sent_at if row else None,
            'error': row.last_error if row else '',
            'admin_off': bool(sent and row.admin_off),
            'can_send': can_send and m.has_syncup_link and built,
            'can_hide': sent and not row.hidden and not problem(hub, need_site=False),
            'can_show': sent and row.hidden and can_send and m.has_syncup_link and built,
            'sent': sent,
        })
    return {'rows': out, 'problem': problem(hub), 'any_ready': any(r['can_send'] for r in out)}
