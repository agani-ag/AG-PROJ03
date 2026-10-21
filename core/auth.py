"""One sign-in for everybody who is not the admin.

Hibiscus and Pocket Money each had their own session key, their own password check and their own
magic links — the same two hundred lines, twice, for a set of people that overlaps by about
seventy-five. This is that code, once.

What a module still owns is its *profile*: `hibiscus.Person`, `pocket.Account`. A view asks for
the member, then for its own row. `member_required` does the first; `profile_required` in each
module does the second, because only the module knows what to do when somebody has not joined it
yet.

Admins are not members. The console is still a Django superuser with a PIN.
"""
import secrets
from datetime import timedelta
from functools import wraps

from django.contrib.auth.hashers import check_password, make_password
from django.db.models import F
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme

from .models import Member

SESSION_KEY = 'synchub_member'
#: Set when a session begins and popped by the first page that asks, so a greeting fires on
#: arrival rather than on every page.
GREET_KEY = 'synchub_greet'
#: How the session was established. An SSO session with no password of its own is the only kind
#: that ends when somebody signs out of SyncUp.
VIA_KEY = 'synchub_via'

PASSWORD_MIN = 6
LOGIN_KEY_BYTES = 32
LOGIN_KEY_HOURS = 1
SESSION_DAYS = 30


# --------------------------------------------------------------------------- #
# Passwords
# --------------------------------------------------------------------------- #
def set_password(member, raw_password):
    """Set the password and drop the spare — this settles which of two it was."""
    member.password_hash = make_password(raw_password)
    member.legacy_password_hash = ''
    member.save(update_fields=['password_hash', 'legacy_password_hash'])


def check_member_password(member, raw_password):
    """True if this is either of the passwords they might reasonably be typing.

    A person merged from two modules arrives with two hashes and no way to know which one they
    remember. Both work until one of them is used; `settle_password` then clears the other.
    """
    raw = raw_password or ''
    if member.password_hash and check_password(raw, member.password_hash):
        return True
    if member.legacy_password_hash and check_password(raw, member.legacy_password_hash):
        return True
    return False


def settle_password(member, raw_password):
    """After a successful sign-in, keep the hash they actually used and forget the other."""
    if not member.legacy_password_hash:
        return False
    if member.password_hash and check_password(raw_password or '', member.password_hash):
        member.legacy_password_hash = ''
        member.save(update_fields=['legacy_password_hash'])
        return True
    member.password_hash = member.legacy_password_hash
    member.legacy_password_hash = ''
    member.save(update_fields=['password_hash', 'legacy_password_hash'])
    return True


def normalise_phone(value):
    """Last ten digits — the part people type differently every time."""
    return ''.join(c for c in str(value or '') if c.isdigit())[-10:]


def split_sign_in_id(value):
    """`(email, phone)` from whatever was typed into the one field."""
    value = (value or '').strip()
    if not value:
        return '', ''
    if '@' in value:
        return value.lower(), ''
    return '', normalise_phone(value)


def find_member(sign_in_id):
    email, phone = split_sign_in_id(sign_in_id)
    if email:
        return Member.objects.filter(email__iexact=email).first()
    if phone and len(phone) >= 10:
        return Member.objects.filter(phone__endswith=phone).first()
    return None


# --------------------------------------------------------------------------- #
# Magic links
# --------------------------------------------------------------------------- #
def new_login_key(member, hours=LOGIN_KEY_HOURS, one_shot=True):
    """A fresh link. Making one kills the last, which is how a leaked link is pulled.

    Two kinds, and both are wanted. A link somebody asks for at the sign-in page lasts an hour
    and dies on first use. A link the admin hands somebody to bookmark — which is how the
    diary's people have always got in — lasts until it is replaced: pass `hours=None,
    one_shot=False`. The difference is the link's, not the module's, so one implementation
    serves both.
    """
    member.login_key = secrets.token_urlsafe(LOGIN_KEY_BYTES)
    member.login_key_made_at = timezone.now()
    member.login_key_used_at = None
    member.login_key_expires_at = (timezone.now() + timedelta(hours=hours)) if hours else None
    member.login_key_one_shot = bool(one_shot)
    member.save(update_fields=['login_key', 'login_key_made_at', 'login_key_used_at',
                               'login_key_expires_at', 'login_key_one_shot'])
    return member.login_key


def member_by_login_key(key):
    key = (key or '').strip()
    if not key:
        return None
    member = Member.objects.filter(login_key=key, is_active=True, is_archived=False).first()
    if not member or not member.login_key_made_at:
        return None
    if member.login_key_expires_at and timezone.now() > member.login_key_expires_at:
        return None
    return member


def spend_login_key(member):
    """Record the use, and burn the key if it was a one-shot."""
    fields = {'login_key_used_at': timezone.now(), 'login_key_uses': F('login_key_uses') + 1}
    if member.login_key_one_shot:
        fields['login_key'] = None
    Member.objects.filter(pk=member.pk).update(**fields)


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def sign_in(request, member, via='password'):
    """Start a session.

    `cycle_key()` first: a fresh session id on every sign-in, so a cookie somebody else already
    knows cannot be ridden into the new session. It has to happen *before* the key is written,
    or it wipes what was just set.
    """
    request.session.cycle_key()
    request.session[SESSION_KEY] = member.pk
    request.session[GREET_KEY] = True
    request.session[VIA_KEY] = via
    request.session.set_expiry(60 * 60 * 24 * SESSION_DAYS)
    Member.objects.filter(pk=member.pk).update(last_opened_at=timezone.now(),
                                               open_count=F('open_count') + 1)


def sign_out(request):
    for key in (SESSION_KEY, GREET_KEY, VIA_KEY):
        request.session.pop(key, None)


def signed_in_via(request):
    return request.session.get(VIA_KEY, '')


def current(request):
    """The signed-in member, or None. Re-read every request, so archiving bites at once."""
    pk = request.session.get(SESSION_KEY)
    if not pk:
        return None
    return Member.objects.filter(pk=pk, is_active=True, is_archived=False).first()


def safe_next(request, fallback='member_apps'):
    """Where to go after signing in, when the URL says. Never off-site."""
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return nxt
    return reverse(fallback)


def member_required(view):
    """Signed in, or off to the one sign-in page — carrying where they were going."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        member = current(request)
        if not member:
            sign_out(request)
            return redirect(f'{reverse("member_login")}?next={request.get_full_path()}')
        request.member = member
        return view(request, *args, **kwargs)
    return inner
