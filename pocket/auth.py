"""Passwords, sessions and magic links for the people who keep money here.

They are not Django users — they never reach the console — so Pocket Money keeps its own hash
and its own session key, the way Hibiscus does. Hashing goes through Django's hashers, so these
passwords are stored exactly as well as the admin's.

Why a password at all, when J6 gets by with a pasted ID: a chat you can lose and start again,
eight months of money you cannot. The email or phone is the way back in.
"""
import secrets
from functools import wraps

from django.contrib.auth.hashers import check_password, make_password
from django.db.models import F
from django.shortcuts import redirect
from django.utils import timezone

from .models import Account

SESSION_KEY = 'pocket_account_id'
GREET_KEY = 'pocket_greet'

PASSWORD_MIN = 6
LOGIN_KEY_BYTES = 32            # 43 url-safe characters: the whole link is the credential
LOGIN_KEY_HOURS = 1             # a money account gets an hour, not for ever


def set_password(account, raw_password):
    account.password_hash = make_password(raw_password)
    account.save(update_fields=['password_hash'])


def check_account_password(account, raw_password):
    return bool(account.password_hash) and check_password(raw_password or '',
                                                          account.password_hash)


def normalise_phone(value):
    """Last ten digits, which is what people actually type differently every time."""
    return ''.join(c for c in str(value or '') if c.isdigit())[-10:]


def split_sign_in_id(value):
    """`(email, phone)` from whatever somebody typed in the one field."""
    value = (value or '').strip()
    if not value:
        return '', ''
    if '@' in value:
        return value.lower(), ''
    return '', normalise_phone(value)


def find_account(sign_in_id):
    """Whoever signs in with this email address or phone number."""
    email, phone = split_sign_in_id(sign_in_id)
    if email:
        return Account.objects.filter(email__iexact=email).first()
    if phone:
        return Account.objects.filter(phone__endswith=phone).first()
    return None


# --------------------------------------------------------------------------- #
# Magic links
# --------------------------------------------------------------------------- #
def new_login_key(account):
    """A fresh one-hour link. Making one kills the last, which is how a leaked link is pulled."""
    account.login_key = secrets.token_urlsafe(LOGIN_KEY_BYTES)
    account.login_key_made_at = timezone.now()
    account.login_key_used_at = None
    account.save(update_fields=['login_key', 'login_key_made_at', 'login_key_used_at'])
    return account.login_key


def account_by_login_key(key):
    """Whoever this key belongs to, if it is real, unexpired and the account is open."""
    key = (key or '').strip()
    if not key:
        return None
    account = Account.objects.filter(login_key=key, is_active=True, is_archived=False).first()
    if not account or not account.login_key_made_at:
        return None
    age = timezone.now() - account.login_key_made_at
    if age.total_seconds() > LOGIN_KEY_HOURS * 3600:
        return None
    return account


def spend_login_key(account):
    """One hour *and* one use: the link is dead the moment it works."""
    Account.objects.filter(pk=account.pk).update(login_key=None, login_key_used_at=timezone.now())


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def sign_in(request, account):
    request.session[SESSION_KEY] = account.pk
    request.session[GREET_KEY] = True
    request.session.set_expiry(60 * 60 * 24 * 30)
    Account.objects.filter(pk=account.pk).update(last_opened_at=timezone.now(),
                                                 open_count=F('open_count') + 1)


def sign_out(request):
    request.session.pop(SESSION_KEY, None)
    request.session.pop(GREET_KEY, None)


def current(request):
    """The signed-in account, or None. Re-read every request, so archiving takes effect at once."""
    pk = request.session.get(SESSION_KEY)
    if not pk:
        return None
    return Account.objects.filter(pk=pk, is_active=True, is_archived=False).first()


def account_required(view):
    """Signed in, or go to the sign-in page."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        account = current(request)
        if not account:
            sign_out(request)
            return redirect('pocket_login')
        request.account = account
        return view(request, *args, **kwargs)
    return inner
