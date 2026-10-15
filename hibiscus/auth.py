"""Passwords and sessions for the people who keep a diary here.

They are not Django users — they never reach the admin console — so Hibiscus keeps its own
password hash and its own session key. Hashing still goes through Django's hashers, so these
passwords are stored exactly as well as the admin's.

The user-side sign-in views arrive in step 4; what is here is what the admin side needs.
"""
import secrets

from django.contrib.auth.hashers import check_password, make_password

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


def find_person(sign_in_id):
    """Look someone up by whatever they typed — an email address or a phone number."""
    value = (sign_in_id or '').strip()
    if not value:
        return None
    if '@' in value:
        return Person.objects.filter(email__iexact=value).first()
    digits = ''.join(c for c in value if c.isdigit())[-10:]
    return Person.objects.filter(phone__endswith=digits).first() if digits else None


def log(action, person=None, actor=None, detail=''):
    """Write one audit row. The admin sees everything, so every look and change is recorded."""
    return Audit.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        person=person, action=action, detail=str(detail)[:200],
    )
