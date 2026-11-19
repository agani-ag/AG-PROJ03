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
from .models import Game, Player, Room, Seat, Settings

logger = logging.getLogger(__name__)

#: A table nobody has touched for this long is not being played any more.
STALE_HOURS = 6

#: How long a table nobody has touched is still considered in play.


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


@cronhub.cleanup('games.guests', budget=8)
def guests(ctx):
    """A guest nobody has seen for a while, and everything that was theirs.

    HARD DELETE, on purpose. A guest is a name in somebody's session and nothing else — there
    is no account to suspend, nothing to email and nobody to ask, so leaving the rows behind
    would only be hoarding. Tables they made go with them, and so do their chairs at anybody
    else's. Members are never touched by this, however long they have been away.

    How long is `Settings.keep_guest_days`, set on the module's settings page.
    """
    now = timezone.now()
    days = Settings.load().keep_guest_days
    if not days:
        return {'items': 0, 'note': 'keeping guests for ever (set to 0)'}

    stale = list(Player.objects.filter(member__isnull=True,
                                       last_seen_at__lt=now - timedelta(days=days))
                 .values_list('pk', flat=True)[:200])
    # A guest who has never come back at all still ages, from when they started.
    stale += list(Player.objects.filter(member__isnull=True, last_seen_at__isnull=True,
                                        created_at__lt=now - timedelta(days=days))
                  .values_list('pk', flat=True)[:200])
    if not stale:
        return {'items': 0}

    hosted = Room.objects.filter(host__in=stale)
    rooms_gone = hosted.count()
    hosted.delete()                       # cascades to its seats, rounds and moves

    # Their chairs at other people's tables. Any round still open in one of those is closed
    # first, because a seat vanishing out from under a live game is worse than a dead table.
    rooms_left = set(Seat.objects.filter(player__in=stale).values_list('room_id', flat=True))
    Game.objects.filter(room_id__in=rooms_left, finished_at__isnull=True).update(finished_at=now)
    Room.objects.filter(pk__in=rooms_left).update(phase=Room.Phase.CLOSED)
    seats_gone = Seat.objects.filter(player__in=stale).delete()[0]
    people_gone = Player.objects.filter(pk__in=stale).delete()[0]

    return {'items': people_gone,
            'note': f'{people_gone} guests, {rooms_gone} tables, {seats_gone} seats'}


@cronhub.cleanup('games.tidy', budget=6)
def tidy(ctx):
    """Clear out what has aged out. Rooms cascade to their seats, rounds and moves.

    A FINISHED TABLE IS KEPT FOR A DAY, not a month. Nothing reads one: the score lives on the
    table and the table is over, the code has been read out and will never be read out again,
    and a console list of hundreds of finished tables is a list nobody scrolls. Both numbers
    are on the module's settings page.
    """
    now = timezone.now()
    config = Settings.load()

    gone_closed = 0
    if config.keep_finished_days is not None:
        done = Room.objects.filter(
            phase=Room.Phase.CLOSED,
            last_seen_at__lt=now - timedelta(days=config.keep_finished_days))
        gone_closed = done.count()
        done.delete()

    gone_empty = 0
    if config.keep_lobby_days:
        empty = Room.objects.filter(
            phase=Room.Phase.LOBBY,
            last_seen_at__lt=now - timedelta(days=config.keep_lobby_days))
        gone_empty = empty.count()
        empty.delete()

    return {'items': gone_closed + gone_empty,
            'note': f'{gone_closed} finished, {gone_empty} never started'}
