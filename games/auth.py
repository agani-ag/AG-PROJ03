"""Who is playing — a hub member, or a guest who gave a name and nothing else.

Identity for a member is the hub's: `core.auth` owns the password, the session and the magic
link. What this module owns is a `Player` row, and the one thing it owns outright is the guest.

A GUEST IS A PLAYER WITH NO MEMBER. The key that brings them back lives in their session and
nowhere else — there is no password and nothing to recover with, which is the same trade J6
makes for its accounts. A game is a poor place to meet a sign-up form, and a table of two is
not a game, so the cheapest possible way in is the point.
"""
import random
from functools import wraps

from django.shortcuts import redirect
from django.urls import reverse

from core import auth as hub

from .models import Player, new_guest_key

#: Where a guest's key is kept. One per browser, so two tabs are one player and a different
#: phone is a different person.
GUEST_KEY = 'games_guest'

_SYSTEM = random.SystemRandom()


def profile_for(member):
    """A member's player row, or None if they have never opened Games."""
    if not member:
        return None
    return Player.objects.filter(member=member).first()


def guest_for(request):
    """The guest this browser is, or None."""
    key = request.session.get(GUEST_KEY)
    if not key:
        return None
    return Player.objects.filter(guest_key=key, member__isnull=True).first()


def current(request):
    """Whoever is playing on this request — member first, then guest."""
    return profile_for(hub.current(request)) or guest_for(request)


def start(member):
    """A player row for a hub member. The name is theirs to change afterwards."""
    name = (member.name or '').strip() or 'Player'
    return Player.objects.create(member=member, name=name[:40])


def start_guest(request, name):
    """A player row for somebody who gave a name and nothing else.

    The key goes in their session at once: a guest who loses it has lost their name, their
    score and the table they were sitting at, and there is no way to give any of it back.
    """
    player = Player.objects.create(member=None, guest_key=new_guest_key(),
                                   name=(name or '').strip()[:40] or suggest_name())
    request.session[GUEST_KEY] = player.guest_key
    return player


def suggest_name(rng=None):
    """A name to put in the box for a guest, which they are free to type over.

    From the same ordinary names a chair this server plays would be given, and checked the same
    way. Two pools would be a tell: anybody who learned which names came from where could read
    a table, and the whole point is that a table is unreadable.
    """
    from .service import NAMES, taken_names
    used = taken_names(None)
    free = [n for n in NAMES if n.strip().lower() not in used]
    return (rng or _SYSTEM).choice(free or list(NAMES))


def adopt(request, member, player):
    """A guest who has just signed in becomes that member, keeping everything.

    It is the same row: the name, the score, the tables they made and the chairs they are
    sitting in all come along because none of it moves. That is the whole reason a guest is a
    Player rather than a thing of its own — converting is one UPDATE, not a migration.

    Only when the member has no player of their own. Two rows for one person could be sitting
    at the same table, and merging them there would seat somebody twice; so the older account
    wins, the guest row is left where it is, and it ages out like any other.
    """
    if Player.objects.filter(member=member).exists():
        request.session.pop(GUEST_KEY, None)        # they are the member from here on
        return None
    Player.objects.filter(pk=player.pk).update(member=member, guest_key=None)
    request.session.pop(GUEST_KEY, None)
    player.refresh_from_db()
    return player


def player_required(view):
    """Playing — signed in to the hub, or a guest. Either is enough for every table.

    A guest who signs in is converted here, the first time they come back with both: it is the
    one moment we can be sure they want it, and it costs them nothing to find.
    """
    @wraps(view)
    def inner(request, *args, **kwargs):
        member = hub.current(request)
        player = profile_for(member) if member else None
        if member and request.session.get(GUEST_KEY):
            # Signed in with a guest key still in the session. Either way they are that member
            # from here on, so the key is spent — the only question is whether the guest row
            # becomes them or stays where it is.
            waiting = guest_for(request)
            if waiting and not player:
                player = adopt(request, member, waiting)
            request.session.pop(GUEST_KEY, None)
        if not member:
            player = guest_for(request)
        if not player:
            return redirect(f'{reverse("games_join")}?next={request.get_full_path()}')
        request.member = member
        request.player = player
        return view(request, *args, **kwargs)
    return inner


def next_value(request):
    """The raw `?next=`, as typed in the URL or carried in the form."""
    return request.POST.get('next') or request.GET.get('next') or ''


def safe_next(request, fallback='games_home'):
    """Where to go after starting. Checked, never obeyed — the hub's own rule."""
    return hub.safe_url(request, next_value(request), fallback)
