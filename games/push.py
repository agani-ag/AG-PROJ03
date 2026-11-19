"""A nudge when it is your turn and you are not looking.

The rule is J6's and it is the right one: somebody whose table is polling can see the game, so
buzzing their phone about a screen they are holding is spam. Only a player who has gone quiet
gets one, and only once per turn — `Seat.pushed_seq` is what keeps a retry and the fifteen-
minute backstop from buzzing twice for the same turn.

Sent in a background thread, so the person who just played never waits on somebody else's phone.
"""
import logging
import threading

from django.urls import reverse
from django.utils import timezone

from core import push as core_push
from core.models import HubSettings

from .models import Seat

logger = logging.getLogger(__name__)

#: Polling more recently than this means the table is open in front of them.
QUIET_AFTER_SECONDS = 90


def is_away(player):
    if not player or not player.last_seen_at:
        return True
    return (timezone.now() - player.last_seen_at).total_seconds() > QUIET_AFTER_SECONDS


def nudge(game):
    """Buzz whoever the table is waiting on, if they have wandered off. Returns why not."""
    state = game.state or {}
    if state.get('phase') != 'playing':
        return 'not playing'
    seats = list(game.room.seats.order_by('position'))
    turn = state.get('turn')
    if turn is None or turn >= len(seats):
        return 'no seat'
    seat = seats[turn]
    if seat.is_held:
        return 'nobody waiting'           # a chair this server plays needs no telling
    if seat.pushed_seq >= game.seq:
        return 'already pushed'
    player = seat.player
    if not is_away(player):
        return 'watching'
    token = player.push_token
    if not token:
        return 'no token'

    # Claimed before the thread starts, so a retry and the backstop cannot both send.
    Seat.objects.filter(pk=seat.pk).update(pushed_seq=game.seq, pushed_at=timezone.now())
    threading.Thread(target=_deliver, args=(token, game.room.code), daemon=True).start()
    return 'pushing'


def _deliver(token, code):
    try:
        url = HubSettings.load().url_for(reverse('games_room', args=[code]))
        core_push.send(token, 'Your turn', f'Table {code} is waiting on you.', url)
    except Exception as problem:              # noqa: BLE001 — a push must never break a move
        logger.info('games push: %s', problem)
