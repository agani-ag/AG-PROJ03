"""Passwords and sessions for the people who keep a diary here.

They are not Django users — they never reach the admin console — so Hibiscus keeps its own
password hash and its own session key. Hashing still goes through Django's hashers, so these
passwords are stored exactly as well as the admin's.

The user-side sign-in views arrive in step 4; what is here is what the admin side needs.
"""
import secrets
from functools import wraps

from django.contrib.auth.hashers import check_password, make_password
from django.db.models import F
from django.shortcuts import redirect
from django.utils import timezone

from .models import Audit, Person

SESSION_KEY = 'hibiscus_person_id'

# No look-alike characters: someone reads this off a screen and types it on a phone.
PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
PASSWORD_LENGTH = 8


def generate_password():
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


def set_password(person, raw_password):
    person.password_hash = make_password(raw_password)
    person.save(update_fields=['password_hash'])


def check_person_password(person, raw_password):
    return bool(person.password_hash) and check_password(raw_password or '', person.password_hash)


# 32 random bytes, url-safe: 43 characters, nothing to guess. The whole link is the credential,
# so it is made long rather than readable — nobody types this one, they tap it.
LOGIN_KEY_BYTES = 32


def new_login_key(person):
    """Give this person a fresh magic link and return the key.

    Any previous key is overwritten, so handing out a new link kills the old one at once — the
    only way to take a leaked link out of circulation, since the links never expire.
    """
    person.login_key = secrets.token_urlsafe(LOGIN_KEY_BYTES)
    person.login_key_made_at = timezone.now()
    person.login_key_used_at = None
    person.login_key_uses = 0
    person.save(update_fields=['login_key', 'login_key_made_at', 'login_key_used_at',
                               'login_key_uses'])
    return person.login_key


def clear_login_key(person):
    """Switch the magic link off. The link stops working the moment this runs."""
    person.login_key = None
    person.login_key_made_at = None
    person.login_key_used_at = None
    person.login_key_uses = 0
    person.save(update_fields=['login_key', 'login_key_made_at', 'login_key_used_at',
                               'login_key_uses'])


def person_by_login_key(key):
    """Whoever this key belongs to, if the key is real and the account is open.

    A suspended or archived person is not returned, so switching someone off closes the magic
    link too — the same rule `current_person()` applies on every request.
    """
    key = (key or '').strip()
    if not key:
        return None
    return Person.objects.filter(login_key=key, is_active=True, is_archived=False).first()


def find_person(sign_in_id):
    """Look someone up by whatever they typed — an email address or a phone number."""
    value = (sign_in_id or '').strip()
    if not value:
        return None
    if '@' in value:
        return Person.objects.filter(email__iexact=value).first()
    digits = ''.join(c for c in value if c.isdigit())[-10:]
    return Person.objects.filter(phone__endswith=digits).first() if digits else None


def sign_in(request, person):
    """Start a session for this person. Their own session key, separate from the admin's login."""
    request.session[SESSION_KEY] = person.pk
    request.session.set_expiry(60 * 60 * 24 * 30)      # a diary is not a bank: 30 days
    Person.objects.filter(pk=person.pk).update(
        last_opened_at=timezone.now(), open_count=F('open_count') + 1)


def sign_out(request):
    request.session.pop(SESSION_KEY, None)


def current_person(request):
    """The signed-in person, or None. Re-checked on every request, because a suspended account
    must stop working at once rather than at the end of their session."""
    pk = request.session.get(SESSION_KEY)
    if not pk:
        return None
    return Person.objects.filter(pk=pk, is_active=True, is_archived=False).first()


def person_required(view):
    """Signed-in diary owner only. Anyone else goes to the sign-in page."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        person = current_person(request)
        if not person:
            sign_out(request)
            return redirect('hibiscus_me_login')
        request.person = person
        return view(request, *args, **kwargs)
    return inner


def log(action, person=None, actor=None, detail=''):
    """Write one audit row. The admin sees everything, so every look and change is recorded."""
    return Audit.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        person=person, action=action, detail=str(detail)[:200],
    )
