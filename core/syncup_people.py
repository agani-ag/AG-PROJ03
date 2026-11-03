"""SyncUp's people, copied into SyncHub.

SyncUp owns these accounts. Their name, email, phone and password live there; this copies
them here so the SyncUp password works on this website too, and so somebody who presses
**Use token** inside the SyncUp app is already known.

One way only. A SyncHub sign-up never creates a SyncUp account.

**The rule everything here serves: one person never becomes two SyncHub accounts.**
`Member.syncup_id` is unique and that is the enforcement, not the intention — if the cron and
a Use token press reach the same person at the same moment, the second finds the first. Where
a person is already here under an account of their own, nothing is created: the account is
marked *waiting to join*, and joining always needs that account's own SyncHub password, once.
On a shared phone that is what stops one family member taking over another's diary.

Shaped like `core/syncup_links.py`, which talks to the same server with the same key: POST,
key in the body, every call returns `(data, error)`, nothing raises.
"""
import json
import logging
import urllib.error
import urllib.request

from django.contrib.auth.hashers import identify_hasher
from django.db import IntegrityError, transaction
from django.utils import timezone

from . import auth
from .models import HubSettings, Member, SyncUpRefusal

logger = logging.getLogger(__name__)

LIST_TIMEOUT = 15         # the first call carries everybody
WHO_TIMEOUT = 8           # somebody is holding a phone waiting for this
PAGE_TIMEOUT = 20         # "Sync now", with an admin watching


# --------------------------------------------------------------------------- #
# Talking to SyncUp
# --------------------------------------------------------------------------- #
def problem(hub=None):
    """Why nothing can be fetched right now, or ''."""
    hub = hub or HubSettings.load()
    if not hub.syncup_base:
        return 'Set the SyncUp address first.'
    if not hub.syncup_links_key:
        return 'Paste the SyncUp links key first (SyncUp admin → General links).'
    return ''


def _post(path, body, timeout, hub=None):
    """POST to one of SyncUp's hub endpoints. Returns `(data, error)`."""
    hub = hub or HubSettings.load()
    why = problem(hub)
    if why:
        return None, why
    endpoint = hub.syncup_base.rstrip('/') + path
    data = json.dumps({**body, 'key': hub.syncup_links_key}).encode()
    request = urllib.request.Request(endpoint, data=data,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read() or b'{}'), ''
    except urllib.error.HTTPError as exc:
        try:
            said = json.loads(exc.read() or b'{}').get('error') or ''
        except (ValueError, AttributeError):
            said = ''
        return None, (f'SyncUp said {exc.code}: {said}' if said else f'SyncUp said {exc.code}')[:200]
    except (urllib.error.URLError, ValueError, TimeoutError, OSError) as exc:
        return None, f'{type(exc).__name__}: {exc}'[:200]


def fetch_people(version='', timeout=LIST_TIMEOUT, hub=None):
    """The whole list, or `unchanged` when nothing has moved since `version`."""
    return _post('/app/v1/hub/people', {'version': version} if version else {}, timeout, hub)


def who_is(token, timeout=WHO_TIMEOUT, hub=None):
    """Which SyncUp person this page token belongs to, if any."""
    return _post('/app/v1/hub/people/who', {'token': token or ''}, timeout, hub)


# --------------------------------------------------------------------------- #
# Reading one person off the wire
# --------------------------------------------------------------------------- #
def clean(person):
    """SyncUp's JSON, in this database's shapes.

    The empty strings matter more than they look. `Member.email` and `Member.phone` are
    unique and nullable, and an empty string is a *value* — two people with no email would
    collide on the index and the second create would raise, in the middle of a batch. NULL
    is exempt from uniqueness; '' is not.
    """
    return {
        'syncup_id': str(person.get('id') or '').strip()[:64],
        'name': (person.get('name') or '').strip()[:80],
        'email': ((person.get('email') or '').strip().lower() or None),
        'phone': (auth.normalise_phone(person.get('phone')) or None),
        'password_hash': (person.get('password') or '').strip(),
        'syncup_token': (person.get('token') or '').strip(),
    }


def hash_is_usable(password_hash):
    """Whether this site can actually check a password against the hash SyncUp sent.

    Both projects run Django's defaults today, so a PBKDF2 hash verifies here. If SyncUp ever
    gains a hasher this site does not have — installing argon2-cffi is enough to change
    Django's default — `check_password` returns False for everybody, with no error anywhere
    to say why. Better to keep the person and flag the hash than to store a credential
    nothing can check.
    """
    if not password_hash:
        return True                      # nothing to check is not the same as unusable
    try:
        identify_hasher(password_hash)
    except Exception:                    # noqa: BLE001 — any failure means "not ours"
        return False
    return True


# --------------------------------------------------------------------------- #
# Matching, which is where duplicates would come from
# --------------------------------------------------------------------------- #
def _refused(member, syncup_id):
    return SyncUpRefusal.objects.filter(member=member, syncup_id=syncup_id).exists()


def older_account_for(fields):
    """A SyncHub-only account that looks like this SyncUp person — or None.

    Three ways somebody can already be here under their own account: the same email, the
    same phone, or signed in on the same phone (their page token reached us while they were
    using their own account). Returns `(member, why)`.

    Only accounts with no `syncup_id` of their own are candidates, and a pair that has been
    refused is never offered again.
    """
    mine = Member.objects.filter(syncup_id__isnull=True)
    for why, lookup in (
            (Member.Waiting.EMAIL, {'email__iexact': fields['email']} if fields['email'] else None),
            (Member.Waiting.PHONE, {'phone': fields['phone']} if fields['phone'] else None),
            (Member.Waiting.TOKEN,
             {'syncup_token': fields['syncup_token']} if fields['syncup_token'] else None)):
        if lookup is None:
            continue
        for member in mine.filter(**lookup):
            if not _refused(member, fields['syncup_id']):
                return member, why
    return None, ''


def _free_of_clashes(fields, exclude_pk=None):
    """`(email, phone, complaints)` — with anything another account already holds dropped.

    Both columns are unique here, so a value somebody else has cannot be stored whatever we
    do. Dropping the one field keeps the person; forcing it would raise, and in the create
    path that meant losing them from the sync altogether.
    """
    out, complaints = {}, []
    for field in ('email', 'phone'):
        value = fields[field]
        taken = (value is not None and Member.objects.filter(**{field: value}).exclude(
            pk=exclude_pk).exists())
        if taken:
            complaints.append(f'{field} {value} is already another account here')
            out[field] = None
        else:
            out[field] = value
    return out['email'], out['phone'], complaints


def _apply(member, fields):
    """Copy SyncUp's version of a person onto a member already linked to them.

    A changed email or phone that another account here already holds is skipped rather than
    forced — the unique index would refuse it anyway, and losing the rest of the update over
    one field would be worse. The clash is written down for the People page.
    """
    changed = []
    email, phone, clashes = _free_of_clashes(fields, exclude_pk=member.pk)
    for field, value in (('email', email), ('phone', phone)):
        # A dropped field keeps whatever is there rather than being blanked: losing their
        # address because somebody else took it would be worse than the change not landing.
        if value is None and fields[field] is not None:
            continue
        if value != getattr(member, field):
            setattr(member, field, value)
            changed.append(field)

    for field in ('name', 'syncup_token'):
        if fields[field] and fields[field] != getattr(member, field):
            setattr(member, field, fields[field])
            changed.append(field)

    if fields['password_hash'] and fields['password_hash'] != member.password_hash:
        if hash_is_usable(fields['password_hash']):
            member.password_hash = fields['password_hash']
            # SyncUp owns the password now, so the spare from the old module merge goes.
            member.legacy_password_hash = ''
            changed += ['password_hash', 'legacy_password_hash']
        else:
            clashes.append('their SyncUp password is hashed in a way this site cannot check')

    if not member.is_active:
        member.is_active = True          # back on in SyncUp means back on here
        changed.append('is_active')

    note = '; '.join(clashes)[:200]
    if note != member.syncup_clash:
        member.syncup_clash = note
        changed.append('syncup_clash')
    if member.waiting_for or member.waiting_why:
        member.waiting_for, member.waiting_why = '', ''
        changed += ['waiting_for', 'waiting_why']

    if changed:
        member.save(update_fields=sorted(set(changed)))
    return bool(changed)


def take(fields, created_by=None):
    """Bring one SyncUp person in. Returns one of created / updated / waiting / refused-new.

    The three branches are the whole no-duplicates rule:

    1. already linked by `syncup_id` → update them;
    2. an older SyncHub account that looks like them → mark it waiting, **make nothing**;
    3. otherwise → a new account, made ahead and not used yet.
    """
    linked = Member.objects.filter(syncup_id=fields['syncup_id']).first()
    if linked:
        return 'updated' if _apply(linked, fields) else 'unchanged'

    older, why = older_account_for(fields)
    if older:
        if older.waiting_for != fields['syncup_id'] or older.waiting_why != why:
            older.waiting_for, older.waiting_why = fields['syncup_id'], why
            older.save(update_fields=['waiting_for', 'waiting_why'])
        return 'waiting'

    email, phone, clashes = _free_of_clashes(fields)
    if not hash_is_usable(fields['password_hash']):
        clashes.append('their SyncUp password is hashed in a way this site cannot check')
    try:
        with transaction.atomic():
            Member.objects.create(
                syncup_id=fields['syncup_id'], syncup_unused=True,
                name=fields['name'] or 'Someone', email=email, phone=phone,
                password_hash=(fields['password_hash']
                               if hash_is_usable(fields['password_hash']) else ''),
                syncup_token=fields['syncup_token'], created_by=created_by,
                syncup_clash='; '.join(clashes)[:200])
    except IntegrityError:
        # Something took this syncup_id between the lookup above and here — a Use token press
        # landing at the same moment. The unique index is the no-duplicates rule doing its
        # job; the right answer is to leave the row that won alone.
        logger.info('syncup people: %s was created by something else mid-run',
                    fields['syncup_id'])
        return 'unchanged'
    return 'created'


# --------------------------------------------------------------------------- #
# One person, now — the Use token path
# --------------------------------------------------------------------------- #
#: Said to somebody holding a phone, so it says what to do next rather than what went wrong.
NO_ONE = 'No SyncUp sign-in on this phone. Sign in or sign up.'


def person_for_token(token):
    """Whose SyncUp account this page token is. Returns `(person, why_not)`.

    A phone that nobody is signed in on has a token of its own, and it never signs anybody
    in — that is SyncUp's rule and this is where it is honoured. A brand-new SyncUp person
    the cron has not copied yet comes back here in full, so they do not have to wait for the
    next run.
    """
    token = (token or '').strip()
    if not token:
        return None, NO_ONE
    data, error = who_is(token)
    if error:
        logger.info('syncup who: %s', error)
        return None, NO_ONE if error.startswith('SyncUp said 404') else (
            'SyncUp could not be reached just now. Sign in with your password instead.')
    person = (data or {}).get('person')
    if not person or not str(person.get('id') or '').strip():
        return None, NO_ONE
    return person, ''


def refuse(member, syncup_id):
    """"These are different people." Remembered, and never asked again."""
    SyncUpRefusal.objects.get_or_create(member=member, syncup_id=syncup_id)
    if member.waiting_for == syncup_id:
        member.waiting_for, member.waiting_why = '', ''
        member.save(update_fields=['waiting_for', 'waiting_why'])


def join(member, fields):
    """Tie this SyncHub account to that SyncUp person, for good.

    From here SyncUp owns the name, email, phone and password — the SyncHub password the
    person just typed stops working, which the screen tells them before they press it.

    An account the sync made ahead for the same person is in the way, and what to do about
    it depends on whether anybody has used it: an unused one is empty and is deleted, a used
    one holds somebody's diary and is left for an admin to merge by hand.
    """
    made_ahead = Member.objects.filter(syncup_id=fields['syncup_id']).exclude(
        pk=member.pk).first()
    if made_ahead is not None:
        if made_ahead.syncup_unused:
            made_ahead.delete()
        else:
            return False, ('That SyncUp account already has a SyncHub account here with '
                           'things in it. The admin joins these two on the People page.')

    member.syncup_id = fields['syncup_id']
    member.syncup_unused = False
    member.waiting_for, member.waiting_why = '', ''
    member.save(update_fields=['syncup_id', 'syncup_unused', 'waiting_for', 'waiting_why'])
    _apply(member, fields)              # name, email, phone, password — SyncUp's, now
    return True, ''


# --------------------------------------------------------------------------- #
# Whose phone is this now?
# --------------------------------------------------------------------------- #
def token_changed(request, token):
    """Called when a page sees a SyncUp token this session has not seen before.

    Two rules, different on purpose:

    * A session that came from **Use token** belongs to that token. When the token is no
      longer that person's — they signed out of SyncUp, or somebody else signed in — the
      session ends.
    * A session started with a **password** is the person's own. It ends only if a *different*
      linked SyncUp person is now on the phone, which on a shared phone is the case that
      matters.

    Returns True when the session was ended. Never raises: a SyncUp that cannot be reached
    must not sign anybody out.
    """
    from . import auth

    token = (token or '').strip()
    member = auth.current(request)
    if not member or not token:
        return False
    if request.session.get(auth.TOKEN_SEEN_KEY) == token:
        return False
    request.session[auth.TOKEN_SEEN_KEY] = token

    person, _ = person_for_token(token)
    now_id = str((person or {}).get('id') or '').strip()
    came_from_token = auth.signed_in_via(request) == 'token'

    if came_from_token:
        ends = now_id != (member.syncup_id or '')
    else:
        # Somebody else's *linked* account being on the phone is the only thing that unseats
        # a password session. An unknown token, or a phone nobody is signed in on, is not.
        ends = bool(now_id) and now_id != (member.syncup_id or '') and Member.objects.filter(
            syncup_id=now_id).exists()

    if not ends:
        return False

    _close_chat(request, member)
    auth.sign_out(request)
    return True


def _close_chat(request, member):
    """J6 keeps a session of its own and follows the hub, so it has to go too.

    The same reasoning as signing out by hand: a chat account *linked* to the person leaving
    is that person, and leaving it open on a phone that has changed hands is the leak this
    whole check exists to prevent.
    """
    try:
        from core.views_member import _close_linked_chat
    except Exception:                   # pragma: no cover - J6 is optional
        return
    _close_linked_chat(request, member)


# --------------------------------------------------------------------------- #
# A whole run
# --------------------------------------------------------------------------- #
def switch_off_the_missing(seen_ids):
    """Anybody linked to SyncUp who was not in the list has left it.

    Turned off, deleted, or made a partner user — all of them mean they cannot sign in here
    any more. Nothing of theirs is removed: the diary, the ledger and the chats stay exactly
    where they are, and turning them back on in SyncUp brings them back.
    """
    gone = Member.objects.filter(syncup_id__isnull=False, is_active=True).exclude(
        syncup_id__in=seen_ids)
    return gone.update(is_active=False)


def run(ctx=None, limit=None, hub=None):
    """One pass. Returns a summary dict; never raises.

    `ctx` is the cron context when there is one — the budget is shared with every other hook,
    so a first import of thousands stops when the time is up and carries on next tick. The
    list is the same list next time, so stopping early costs nothing but a delay.
    """
    hub = hub or HubSettings.load()
    data, error = fetch_people(hub.syncup_people_version, hub=hub)
    if error:
        hub.syncup_people_note = error[:200]
        hub.save(update_fields=['syncup_people_note'])
        return {'items': 0, 'error': error}

    if data.get('unchanged'):
        return {'items': 0, 'unchanged': True}

    people = data.get('people') or []
    out = {'items': 0, 'created': 0, 'updated': 0, 'waiting': 0, 'unchanged': 0,
           'switched_off': 0, 'host': data.get('host') or '', 'token_on': bool(data.get('token_on'))}

    # The guard AG-PROJ04 asked for, and it is worth more than it looks: an empty list from a
    # misconfigured key would otherwise switch off every synced person on the site at once.
    if not people and Member.objects.filter(syncup_id__isnull=False).exists():
        note = 'SyncUp returned no people while this site has some. Nobody was switched off.'
        logger.warning('syncup people: %s', note)
        hub.syncup_people_note = note
        hub.save(update_fields=['syncup_people_note'])
        return {**out, 'refused_empty': True}

    seen, done = [], 0
    for person in people:
        fields = clean(person)
        if not fields['syncup_id']:
            continue
        seen.append(fields['syncup_id'])
        out[take(fields)] += 1
        out['items'] += 1
        done += 1
        if limit and done >= limit:
            break
        if ctx is not None and done % 25 == 0 and ctx.out_of_time():
            # Stop here and keep the version unset, so the next tick asks for the whole list
            # again and carries on. Half a list stored as "done" is how people go missing.
            out['more_to_do'] = True
            hub.syncup_people_note = (f'Copied {done} of {len(people)}; the rest on the next '
                                      f'run.')[:200]
            hub.save(update_fields=['syncup_people_note'])
            return out

    out['switched_off'] = switch_off_the_missing(seen)

    hub.syncup_people_version = str(data.get('version') or '')[:80]
    hub.syncup_people_at = timezone.now()
    hub.syncup_people_note = (
        f"{out['items']} from SyncUp · {out['created']} new, {out['updated']} updated, "
        f"{out['waiting']} waiting to join, {out['switched_off']} switched off")[:200]
    hub.save(update_fields=['syncup_people_version', 'syncup_people_at', 'syncup_people_note'])
    return out
