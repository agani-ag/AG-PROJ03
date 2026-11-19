"""Everything between a tap and the rules: rooms, seats, the lazy clock, and who may see what.

Two things here are load-bearing.

**Every write is a compare-and-swap.** SQLite has no row locks, so the only safe way to change
a game is one UPDATE whose WHERE clause carries the `seq` the change was worked out from. If
somebody else moved first the update touches nothing, and the caller is told to look again
rather than writing over them. A double tap, a retried request and two players racing are all
the same case.

**Nothing runs on a schedule.** `catch_up` is called by whoever looks at the table next, and it
is what closes an expired UNO window, takes a turn that ran out, and plays the seats this
server holds. There is no worker on this host and none is wanted.
"""
import random
from datetime import timedelta

from django.db.models import F
from django.utils import timezone

from .models import Game, Move, Player, Room, Seat, new_code
from .uno import deck, house, rules

MAX_SEATS = 6
MIN_SEATS = 2

#: Names for the seats this server holds. Ordinary, unthemed, and with no pattern to spot —
#: a table of Ace, Bishop and Castle would announce itself. Checked against the hub's real
#: members before use, because a second "Meena" at the table is worse than no illusion at all:
#: somebody would think the real one was there and ignoring them.
NAMES = (
    'Anitha', 'Vikram', 'Sruthi', 'Rahul', 'Deepa', 'Karthik', 'Nisha', 'Arun',
    'Lakshmi', 'Sanjay', 'Preethi', 'Mahesh', 'Divya', 'Rohit', 'Kavya', 'Naveen',
    'Shalini', 'Ganesh', 'Aarthi', 'Suresh', 'Megha', 'Vinod', 'Pooja', 'Hari',
    'Sneha', 'Ajith', 'Ramya', 'Kiran', 'Tara', 'Manoj',
)

_SYSTEM = random.SystemRandom()


# --------------------------------------------------------------------------- #
# Rooms and seats
# --------------------------------------------------------------------------- #
def make_room(player, options=None, target=0, rng=None):
    """A new table, with the person who made it already sitting at it."""
    rng = rng or _SYSTEM
    for _ in range(12):
        code = new_code()
        if not Room.objects.filter(code=code).exists():
            break
    else:                                        # pragma: no cover - 28^5 codes, twelve tries
        code = new_code()
    room = Room.objects.create(code=code, host=player, target=target,
                               options=rules.options(options))
    Seat.objects.create(room=room, position=0, player=player, name=player.name)
    return room


def taken_names(room):
    """Every name that must not be handed to a seat this server holds.

    The hub's real members as well as the table's — if there is an Anitha on SyncHub, no table
    gets a second one, because the two would be indistinguishable to anybody who knows her.
    """
    from core.models import Member
    used = {s.name.strip().lower() for s in room.seats.all()}
    used |= {(name or '').strip().lower()
             for name in Member.objects.values_list('name', flat=True) if name}
    used |= {(name or '').strip().lower()
             for name in Player.objects.values_list('name', flat=True) if name}
    return used


def fill(room, up_to, rng=None):
    """Sit somebody in each empty chair, up to `up_to` in all."""
    rng = rng or _SYSTEM
    used = taken_names(room)
    free = [n for n in NAMES if n.strip().lower() not in used]
    rng.shuffle(free)
    position = room.seats.count()
    added = []
    while position < min(up_to, MAX_SEATS) and free:
        added.append(Seat.objects.create(room=room, position=position, name=free.pop()))
        position += 1
    return added


def join(room, player):
    """Take a chair. Returns `(seat, error)`."""
    seat = room.seats.filter(player=player).first()
    if seat:
        return seat, None
    if not room.is_open:
        return None, 'That game has already started.'
    if room.seats.count() >= MAX_SEATS:
        return None, 'That table is full.'
    position = room.seats.count()
    return Seat.objects.create(room=room, position=position, player=player,
                               name=player.name), None


def leave(room, player):
    """Give a chair up, before the first round is dealt.

    Only before: chairs are numbered, a finished round refers to them by number, and
    renumbering them afterwards would misalign the scores from the game they were won in.
    Somebody who wants out later simply stops answering, and the chair is played for
    them — which is the behaviour that already exists."""
    if not room.is_open or room.games.exists():
        return False
    seat = room.seats.filter(player=player).first()
    if not seat or seat.player_id == room.host_id:
        return False
    seat.delete()
    for position, row in enumerate(room.seats.order_by('position')):
        if row.position != position:
            Seat.objects.filter(pk=row.pk).update(position=position)
    return True


#: A poll is only written down when the last one has gone cold. The table asks every two
#: seconds and a write per poll per player is a write this host does not need to do.
SEEN_EVERY_SECONDS = 40


def seen(player):
    """Note that somebody is looking, which is what stops them being buzzed about it."""
    if not player:
        return
    now = timezone.now()
    if player.last_seen_at and (now - player.last_seen_at).total_seconds() < SEEN_EVERY_SECONDS:
        return
    Player.objects.filter(pk=player.pk).update(last_seen_at=now)
    player.last_seen_at = now


def current_game(room):
    """The round to show: the one in play, or the one that has just finished.

    Filtering on finished_at alone is what hid the result screen — the moment somebody went
    out there was no game to hand over, so the table fell back to the lobby and the winner
    was never announced."""
    return (room.games.filter(finished_at__isnull=True).first()
            or room.games.first())


def seat_of(room, player):
    return room.seats.filter(player=player).first() if player else None


def back(room, player):
    """Somebody who wandered off has opened the table again: give them their chair back.

    Nothing at the table marks either the leaving or the returning. From the other chairs
    one player went quiet for a while and then did not."""
    seat = seat_of(room, player)
    if not seat or not seat.away:
        return False
    Seat.objects.filter(pk=seat.pk).update(away=False)
    seat.away = False
    game = room.games.filter(finished_at__isnull=True).first()
    if game and game.state.get('misses'):
        state = dict(game.state)
        if seat.position < len(state['misses']):
            state['misses'][seat.position] = 0
            state['think_until'] = None
            _commit(game, state, game.seq, timezone.now())
    return True


# --------------------------------------------------------------------------- #
# Starting and finishing a round
# --------------------------------------------------------------------------- #
def start(room, now=None, rng=None):
    """Deal. Returns `(game, error)`."""
    rng, now = rng or _SYSTEM, now or timezone.now()
    if room.phase == Room.Phase.CLOSED:
        return None, 'That table has finished.'
    seats = list(room.seats.order_by('position'))
    if len(seats) < MIN_SEATS:
        return None, 'There has to be somebody to play against.'
    live = room.games.filter(finished_at__isnull=True).first()
    if live:
        return live, None

    state = rules.new_round([s.pk for s in seats], now, rng, room.options)
    _set_think(state, seats, now, rng)
    game = Game.objects.create(room=room, number=room.games.count() + 1, state=state)
    Room.objects.filter(pk=room.pk).update(phase=Room.Phase.PLAYING, last_seen_at=now)
    room.refresh_from_db()
    return game, None


def _finish(game, now):
    """Score a finished round, once. The first caller to claim it is the one that counts."""
    claimed = Game.objects.filter(pk=game.pk, finished_at__isnull=True).update(finished_at=now)
    if not claimed:
        return
    state = game.state
    winner = state.get('winner')
    points = rules.round_points(state)
    seats = list(game.room.seats.order_by('position'))
    if winner is not None and winner < len(seats):
        won = seats[winner]
        Seat.objects.filter(pk=won.pk).update(score=won.score + points)
        if won.player_id:
            Player.objects.filter(pk=won.player_id).update(won=F('won') + 1)
    for row in seats:
        if row.player_id:
            Player.objects.filter(pk=row.player_id).update(played=F('played') + 1)

    # A table closes when it has been won, and not otherwise. Without a target there is
    # nothing to win, so it simply goes back to waiting: the score carries, and "play
    # again" is a button rather than a new table with a new code to read out.
    room = game.room
    won = room.target and any(s.score >= room.target
                              for s in room.seats.order_by('position'))
    Room.objects.filter(pk=room.pk).update(
        phase=Room.Phase.CLOSED if won else Room.Phase.LOBBY, last_seen_at=now)


# --------------------------------------------------------------------------- #
# The lazy clock
# --------------------------------------------------------------------------- #
def _house_seats(room):
    """Positions this server is playing. Never leaves this module in anything readable."""
    return {s.position for s in room.seats.order_by('position') if s.is_held}


def _set_think(state, seats, now, rng):
    """When the seat whose turn it is will answer, if this server is holding it."""
    if state['phase'] != 'playing':
        state['think_until'] = None
        return
    turn = state['turn']
    if turn >= len(seats) or not seats[turn].is_held:
        state['think_until'] = None
        return
    if state.get('think_until'):
        return
    seconds = house.think_seconds(rng)
    if house.wanders_off(rng):
        # Never answering is a tell of its own, so now and again this turn is simply missed
        # and the clock takes it, exactly as it would for somebody who put their phone down.
        seconds = rules.TURN_SECONDS + 5
    state['think_until'] = rules._at(now + timedelta(seconds=seconds))


def catch_up(game, now=None, rng=None, rounds=24):
    """Bring a game up to the present: expired windows, turns that ran out, and the seats this
    server holds. Returns the game, re-read if anything changed.

    Bounded on purpose. A loop whose end depends on the rules being right is a loop that can
    hang a web worker, and this one runs inside somebody's poll.
    """
    rng, now = rng or _SYSTEM, now or timezone.now()
    room = game.room
    seats = list(room.seats.order_by('position'))
    held = _house_seats(room)

    for _ in range(rounds):
        state = dict(game.state)
        claimed = game.seq
        if state.get('phase') != 'playing':
            break

        state, moved = rules.tick(state, now, rng, room.options)

        # Somebody who has stopped answering: the chair is played for them, keeping its name,
        # its hand and its score. Nothing at the table says so.
        stuck = rules.stuck_seat(state)
        if stuck is not None and stuck < len(seats) and not seats[stuck].is_held:
            # The chair keeps its player, its name, its hand and its score. Only the
            # answering changes hands, and they take it back by opening the table again.
            Seat.objects.filter(pk=seats[stuck].pk).update(away=True)
            seats[stuck].away = True
            held.add(stuck)
            state['misses'][stuck] = 0
            moved = True

        turn = state['turn']
        if turn in held and rules._due(state.get('think_until'), now):
            move = house.choose(state, turn, rng, room.options)
            state, error = house.apply(state, turn, move, now, rng, room.options)
            if error:
                # The rules refused a move these rules chose, which is a bug rather than a
                # situation. Draw instead so the table is never stuck on it.
                state, _ = rules.draw(state, turn, now, rng, room.options)
            state['think_until'] = None
            moved = True

        _set_think(state, seats, now, rng)
        if state.get('think_until') != game.state.get('think_until'):
            moved = True
        if not moved:
            break
        if not _commit(game, state, claimed, now):
            game = Game.objects.filter(pk=game.pk).first()
            if not game:
                break
            continue
        game.state, game.seq = state, claimed + 1
        if state['phase'] != 'playing':
            _finish(game, now)
            break
    return game


def _commit(game, state, claimed, now):
    """One UPDATE, guarded by the seq it was worked out from. False means somebody moved first."""
    rows = Game.objects.filter(pk=game.pk, seq=claimed).update(state=state, seq=claimed + 1)
    if rows:
        Room.objects.filter(pk=game.room_id).update(last_seen_at=now)
    return bool(rows)


# --------------------------------------------------------------------------- #
# A move
# --------------------------------------------------------------------------- #
ACTIONS = {
    'play': lambda state, seat, data, now, rng, opts: rules.play(
        state, seat, data.get('card'), now, rng, opts),
    'draw': lambda state, seat, data, now, rng, opts: rules.draw(state, seat, now, rng, opts),
    'pass': lambda state, seat, data, now, rng, opts: rules.pass_turn(state, seat, now),
    'colour': lambda state, seat, data, now, rng, opts: rules.choose_colour(
        state, seat, data.get('colour'), now, rng, opts),
    'swap': lambda state, seat, data, now, rng, opts: rules.choose_swap(
        state, seat, data.get('seat'), now, rng, opts),
    'uno': lambda state, seat, data, now, rng, opts: rules.say_uno(state, seat, now),
    'catch': lambda state, seat, data, now, rng, opts: rules.catch(state, seat, now),
}


def act(game, position, what, data, now=None, rng=None):
    """One player's move. Returns `(game, error)` — the game re-read when it landed."""
    rng, now = rng or _SYSTEM, now or timezone.now()
    doing = ACTIONS.get(what)
    if not doing:
        return game, 'No such move.'

    game = catch_up(game, now, rng)
    state = dict(game.state)
    claimed = game.seq

    state, error = doing(state, position, data, now, rng, game.room.options)
    if error:
        return game, error

    state['think_until'] = None
    seats = list(game.room.seats.order_by('position'))
    _set_think(state, seats, now, rng)

    if not _commit(game, state, claimed, now):
        # Somebody got there first, so this move was worked out from a table that no longer
        # exists. Nothing is written and the page is told to look again.
        return Game.objects.get(pk=game.pk), 'The table moved on — have another look.'

    game.state, game.seq = state, claimed + 1
    _log_move(game, position, what, data)
    if state['phase'] != 'playing':
        _finish(game, now)
    else:
        game = catch_up(game, now, rng)
        # Whoever the table has moved on to, if they have wandered off. Costs the player
        # who just moved nothing: the sending happens on a thread of its own.
        from . import push as pusher
        pusher.nudge(game)
    return game, None


def _log_move(game, position, what, data):
    detail = ''
    if what == 'play' and isinstance(data.get('card'), int):
        detail = deck.label(data['card'])
    elif what == 'colour':
        detail = str(data.get('colour') or '')
    Move.objects.create(game=game, seq=game.seq, position=position, kind=what,
                        detail=detail[:120])


# --------------------------------------------------------------------------- #
# What one browser is handed
# --------------------------------------------------------------------------- #
def table(room, game, seat):
    """The table as one chair sees it.

    ========================= READ THIS BEFORE EDITING =========================
    This and `rules.view` are the only two things that reach a browser. Between them they
    decide what one player can know, and there is nothing in either about *who* is playing a
    seat — not a flag set false, not a field left empty. Absent.

    Seats are names and scores here. Whether a chair has a `player_id` behind it is a column
    in the database and stays there.
    ============================================================================
    """
    seats = list(room.seats.order_by('position'))
    payload = {
        'code': room.code,
        'phase': room.phase,
        'target': room.target,
        'options': rules.options(room.options),
        'seats': [{'position': s.position, 'name': s.name, 'score': s.score} for s in seats],
        'you': seat.position if seat else None,
        'host': room.host_id == (seat.player_id if seat else None),
        'seq': game.seq if game else 0,
        'round': game.number if game else 0,
    }
    if game:
        payload['game'] = rules.view(game.state, seat.position if seat else None, room.options)
    return payload
