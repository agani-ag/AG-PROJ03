"""Games' share of SyncHub's two cron jobs.

No endpoint of its own — `apps.py` imports this at startup, which registers the hooks with the
hub. Neither of these is the main path: a turn is nudged the moment the table moves on to it,
and a game is played forward by whoever looks at it next. These exist for what that missed —
a push that failed, a table everybody walked away from, a process that died mid-move.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core import cronhub

from . import push as pusher
from . import service
from .models import Game, Room

logger = logging.getLogger(__name__)

#: A table nobody has touched for this long is not being played any more.
STALE_HOURS = 6

#: What is kept. A finished table is worth looking back at for a while; an empty lobby is not.
KEEP_CLOSED_DAYS = 30
KEEP_LOBBY_DAYS = 7


@cronhub.tick('games.turns', budget=6)
def turns(ctx):
    """Nudge anybody whose turn came up while they were away, and close dead tables.

    The backstop, not the main path. A table in play is moved on by its own players' polls;
    this is for the ones where everybody has put their phone down.
    """
    now = timezone.now()
    nudged = closed = 0
    stale = now - timedelta(hours=STALE_HOURS)

    for game in Game.objects.filter(finished_at__isnull=True).select_related('room')[:40]:
        if ctx.out_of_time():
            break
        if game.room.last_seen_at and game.room.last_seen_at < stale:
            # Nobody has looked in six hours. Finishing it is kinder than leaving a table
            # that says "your turn" for ever.
            Game.objects.filter(pk=game.pk).update(finished_at=now)
            Room.objects.filter(pk=game.room_id).update(phase=Room.Phase.CLOSED)
            closed += 1
            continue
        game = service.catch_up(game, now)
        if pusher.nudge(game) == 'pushing':
            nudged += 1

    return {'items': nudged + closed, 'nudged': nudged, 'closed': closed}


@cronhub.cleanup('games.tidy', budget=6)
def tidy(ctx):
    """Clear out what has aged out. Rooms cascade to their seats, games and moves."""
    now = timezone.now()
    old_closed = Room.objects.filter(phase=Room.Phase.CLOSED,
                                     last_seen_at__lt=now - timedelta(days=KEEP_CLOSED_DAYS))
    gone_closed = old_closed.count()
    old_closed.delete()

    empty = Room.objects.filter(phase=Room.Phase.LOBBY,
                                last_seen_at__lt=now - timedelta(days=KEEP_LOBBY_DAYS))
    gone_empty = empty.count()
    empty.delete()

    return {'items': gone_closed + gone_empty,
            'note': f'{gone_closed} finished, {gone_empty} never started'}
