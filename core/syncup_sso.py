"""Signing in with SyncUp, from SyncHub's side.

The page never gets to say who somebody is. It gets a one-time code from the app, posts it here,
and *this* redeems it server-side against SyncUp — because `window.SyncUp` is a JavaScript object
and anybody can type one into a browser console.

    page: SyncUp.signIn() → {code}
    here: POST {syncup_base}/app/v1/hub/sso/check  {key, code, host}
          → {"success": true, "user": {"id": "su_…", "name": …, "email": …, "phone": …}}

The id is SyncUp's opaque per-site handle: stable for ever for one SyncUp account on this hub,
the same value for every module, and gone only if the account is deleted. That is what makes it
safe as `Member.syncup_id`. The page token is *not* identity — it can be reset.

The name, email and phone that come back are **unverified for ever**. SyncUp does not verify
email addresses, so arriving with somebody's address is not proof of being them. That single
fact is why this module never joins an SSO sign-in to an existing account by itself: see
`resolve`, which hands that case back as `prove`.

Everything here is inert until an SSO key is set at System → Settings. No key, no button.
"""
import json
import logging
import urllib.error
import urllib.request

from .auth import normalise_phone
from .models import HubSettings, Member

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 8
ENDPOINT = '/app/v1/hub/sso/check'

#: What SyncUp says went wrong, in words a person can act on.
REASONS = {
    'off': 'SyncUp sign-in is not switched on for this site.',
    'bad_key': 'This site and SyncUp disagree about the sign-in key.',
    'bad_code': 'That sign-in was already used. Try again.',
    'expired': 'That took too long. Try again.',
    'wrong_host': 'That sign-in was for a different site.',
    'account_gone': 'That SyncUp account is no longer there.',
    'disconnected': 'That sign-in was withdrawn. Try again.',
    'slow_down': 'Too many tries. Give it a minute.',
}


def is_configured():
    hub = HubSettings.load()
    return bool(hub.syncup_base and hub.syncup_sso_key)


def host_for(hub=None):
    """The host SyncUp bound the code to — whatever `site_base` points at."""
    hub = hub or HubSettings.load()
    base = (hub.site_base or '').strip()
    for prefix in ('https://', 'http://'):
        if base.startswith(prefix):
            base = base[len(prefix):]
    return base.strip('/').split('/')[0]


def check(code, timeout=SEND_TIMEOUT):
    """Redeem a one-time code. Returns `(user, error)` and never raises.

    A code is single use and SyncUp consumes it whatever the answer, so this is called once.
    """
    hub = HubSettings.load()
    if not hub.syncup_base:
        return None, 'SyncUp address not set at System → Settings.'
    if not hub.syncup_sso_key:
        return None, 'No SyncUp sign-in key set at System → Settings.'
    if not (code or '').strip():
        return None, 'Nothing to check.'

    payload = json.dumps({'key': hub.syncup_sso_key, 'code': code,
                          'host': host_for(hub)}).encode()
    request = urllib.request.Request(hub.syncup_base.rstrip('/') + ENDPOINT, data=payload,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read() or b'{}')
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read() or b'{}')
        except ValueError:
            body = {}
        reason = body.get('reason', '')
        return None, REASONS.get(reason) or body.get('error') or f'SyncUp said {exc.code}.'
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        return None, f'Could not reach SyncUp — {getattr(exc, "reason", exc)}'

    if not data.get('success'):
        reason = data.get('reason', '')
        return None, REASONS.get(reason) or data.get('error') or 'SyncUp refused that sign-in.'

    user = data.get('user') or {}
    if not user.get('id'):
        return None, 'SyncUp did not say who that was.'
    return {
        'id': str(user['id'])[:64],
        'name': (user.get('name') or '').strip()[:80],
        'email': (user.get('email') or '').strip().lower(),
        'phone': normalise_phone(user.get('phone')),
    }, ''


# --------------------------------------------------------------------------- #
# Turning a SyncUp person into a Member
# --------------------------------------------------------------------------- #
def resolve(user):
    """Work out who this is. Returns `(outcome, member)`.

    * `linked`  — we have seen this SyncUp account before; sign them in.
    * `prove`   — somebody here already has that email or phone. It is **probably** them and we
                  still will not take SyncUp's word for it: they type their password first.
                  SyncUp does not verify email addresses, so an unverified address must never be
                  enough to walk into an existing diary or set of money books.
    * `new`     — nobody matches; a Member can be created.
    """
    member = Member.objects.filter(syncup_id=user['id']).first()
    if member:
        return 'linked', member

    claimant = None
    if user['email']:
        claimant = Member.objects.filter(email__iexact=user['email']).first()
    if not claimant and len(user['phone']) >= 10:
        claimant = Member.objects.filter(phone__endswith=user['phone']).first()
    if claimant:
        return 'prove', claimant
    return 'new', None


def attach(member, user):
    """Link an existing Member to this SyncUp account, once they have proved it is theirs."""
    member.syncup_id = user['id']
    if not member.name and user['name']:
        member.name = user['name']
    member.save(update_fields=['syncup_id', 'name'])
    return member


def create(user, *, email=None, phone=None, password=''):
    """A new Member for this SyncUp account.

    Called two ways. Normally the email and phone come straight from SyncUp and there is no
    password, because the app is the way in. On the "it is not my account" branch the address
    belongs to somebody else here, so the caller passes an identifier the person typed instead
    — and a password with it.

    Both, not either: `/in/` finds people by email or phone and *then* checks a password, so an
    account with neither is unreachable from a browser no matter what password it has. A
    password on its own would look like a way back in without being one.
    """
    from .auth import set_password
    email = user['email'] if email is None else email
    phone = user['phone'] if phone is None else phone
    member = Member.objects.create(
        name=user['name'] or 'Someone',
        email=(email or '').lower() or None,
        phone=phone or None,
        syncup_id=user['id'],
    )
    if password:
        set_password(member, password)
    return member


def identifier_taken(email, phone, exclude=None):
    """Is this email or phone already somebody else's?"""
    rows = Member.objects.all()
    if exclude:
        rows = rows.exclude(pk=exclude)
    if email and rows.filter(email__iexact=email).exists():
        return 'Somebody here already uses that email address.'
    if phone and len(phone) >= 10 and rows.filter(phone__endswith=phone).exists():
        return 'Somebody here already uses that number.'
    return ''
