"""Who is playing.

Identity is the hub's — `core.auth` owns the password, the session and the magic link. What
this module owns is a `Player` row: a name to show at a table and a tally. So a view asks two
questions in order, and they have two different answers: sign in, or start playing.
"""
from functools import wraps

from django.shortcuts import redirect
from django.urls import reverse

from core import auth as hub

from .models import Player


def profile_for(member):
    """Their player row, or None if they have never opened Games."""
    if not member:
        return None
    return Player.objects.filter(member=member).first()


def start(member):
    """Make one, named after the member. The name is theirs to change afterwards."""
    name = (member.name or '').strip() or 'Player'
    return Player.objects.create(member=member, name=name[:40])


def current(request):
    return profile_for(hub.current(request))


def player_required(view):
    """Signed in to the hub, and has started Games."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        member = hub.current(request)
        if not member:
            return redirect(f'{reverse("member_login")}?next={request.get_full_path()}')
        player = profile_for(member)
        if not player:
            return redirect(f'{reverse("games_join")}?next={request.get_full_path()}')
        request.member = member
        request.player = player
        return view(request, *args, **kwargs)
    return inner
