"""The one way out of SyncHub to somebody's phone.

A module decides *what* to say and *when*; this decides nothing. It posts to SyncUp's partner
endpoint, which turns the message into an FCM notification on whichever device handed us the
token — `window.SyncUp.token`, which a page only ever sees inside the SyncUp app.

This lived twice, once in Hibiscus and once in J6, until a third module wanted it. The two
settings it needs — where SyncUp is, and the key it expects — already belong to the hub, so the
transport belongs here with them.

Nothing here raises: a notification that fails to go out is a nuisance, never a reason for the
thing that triggered it to fall over.
"""
import json
import logging
import urllib.error
import urllib.request

from .models import HubSettings

logger = logging.getLogger(__name__)

TITLE_MAX = 100
BODY_MAX = 200
SEND_TIMEOUT = 5          # short for cron work, longer when somebody is watching a page


def token_of(profile):
    """The device token to reach this person on — one per person, not one per module.

    It lives on the `core.Member`, so a phone registered by opening the diary inside the SyncUp
    app also reaches Pocket Money and J6. The profile's own column is a fallback with exactly
    one real user: an unlinked J6 chat account, which has no Member to put it on.
    """
    member = profile.member if profile.member_id else None
    return (member.syncup_token if member else '') or profile.syncup_token


def remember_token(profile, token):
    """Store `window.SyncUp.token` where every module will look for it. True if it changed.

    Writes to the Member whenever there is one, which is what makes the token shared. Updated
    with a queryset `update()` rather than `save()`: these arrive from a background fetch while
    the page is doing something else, and must not write back a whole stale row.
    """
    token = (token or '').strip()[:4000]
    if not token:
        return False
    owner = profile.member if profile.member_id else profile
    if token == owner.syncup_token:
        return False
    type(owner).objects.filter(pk=owner.pk).update(syncup_token=token)
    owner.syncup_token = token
    return True


def send(token, title, body, url, timeout=SEND_TIMEOUT):
    """Post one notification. Returns `(delivered, error)`.

    `delivered` is what SyncUp says it reached — 0 when nothing went out, which is also what a
    missing token or an unreachable host gives. The error string is for the console and the cron
    log; nobody in a module has to interpret it.
    """
    hub = HubSettings.load()
    if not hub.syncup_base:
        return 0, 'syncup not configured'
    if not token:
        return 0, 'no syncup token'

    payload = json.dumps({'token': token, 'title': (title or '')[:TITLE_MAX],
                          'body': (body or '')[:BODY_MAX], 'url': url,
                          'key': hub.syncup_notify_key}).encode()
    endpoint = hub.syncup_base.rstrip('/') + '/app/v1/partner/notify'
    request = urllib.request.Request(endpoint, data=payload,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read() or b'{}')
        return int(data.get('delivered') or 0), ''
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        return 0, f'{type(exc).__name__}: {exc}'[:200]
