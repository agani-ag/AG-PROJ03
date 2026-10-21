"""Who is using the diary.

Identity — the password, the magic link, the session — belongs to `core.auth` now. The same
people keep a diary and a set of money books, and used to have two of everything.

What stays here is the diary's own *profile*, the `Person` row: the cycle hint, the audit trail
and the five foreign keys that point at it. Plus the audit log itself, which is the diary's and
nobody else's.

The console still says "set this person's password" and "make them a link", so those names are
kept as thin shims onto the member. The console does not need to know where identity moved, and
`Person` is still what every one of its pages is about.
"""
import secrets
from functools import wraps

from django.shortcuts import redirect
from django.urls import reverse

from core import auth as hub

from .models import Audit, Person

# No look-alike characters: somebody reads this off a screen and types it on a phone.
# Re-exported so the diary's own views and tests keep their names while the session itself
# belongs to the hub.
SESSION_KEY = hub.SESSION_KEY
GREET_KEY = hub.GREET_KEY

PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
PASSWORD_LENGTH = 8


def generate_password():
    return ''.join(secrets.choice(PASSWORD_ALPHABET) for _ in range(PASSWORD_LENGTH))


# --------------------------------------------------------------------------- #
# Shims, so the console keeps speaking about people
# --------------------------------------------------------------------------- #
def set_password(person, raw_password):
    if person.member:
        hub.set_password(person.member, raw_password)


def check_person_password(person, raw_password):
    return bool(person.member) and hub.check_member_password(person.member, raw_password)


def new_login_key(person):
    """The diary's link is made to be bookmarked: no expiry, and it keeps working.

    That was a deliberate decision here long before the hub existed — the admin hands somebody
    a link and they never type a password again. Replacing it is what pulls an old one.
    """
    return hub.new_login_key(person.member, hours=None, one_shot=False) if person.member else None


def clear_login_key(person):
    if person.member:
        person.member.login_key = None
        person.member.login_key_made_at = None
        person.member.save(update_fields=['login_key', 'login_key_made_at'])


def person_by_login_key(key):
    member = hub.member_by_login_key(key)
    return profile_for(member) if member else None


def find_person(sign_in_id):
    """Whoever signs in with this email or phone — looked up hub-wide, then narrowed to here."""
    member = hub.find_member(sign_in_id)
    return profile_for(member) if member else None


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def profile_for(member):
    """This person's diary, or None if they have never started one."""
    if not member:
        return None
    return Person.objects.filter(member=member, is_active=True, is_archived=False).first()


def current_person(request):
    return profile_for(hub.current(request))


def person_required(view):
    """Signed in to the hub, and keeps a diary.

    Two different "no"s, two different places: the hub's sign-in carrying where they were
    headed, or the page that offers to start a diary.
    """
    @wraps(view)
    def inner(request, *args, **kwargs):
        member = hub.current(request)
        if not member:
            return redirect(f'{reverse("member_login")}?next={request.get_full_path()}')
        person = profile_for(member)
        if not person:
            return redirect('hibiscus_join')
        request.member = member
        request.person = person
        return view(request, *args, **kwargs)
    return inner


def log(action, person=None, actor=None, detail=''):
    """One audit row. The admin sees everything here, so every look and change is recorded."""
    return Audit.objects.create(
        actor=actor if (actor and actor.is_authenticated) else None,
        person=person, action=action, detail=str(detail)[:200],
    )
