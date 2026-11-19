"""The rules of UNO. Pure: state in, state out, no Django, no database, no request.

This is the piece that can be quietly wrong, so it lives alone and is tested on its own — the
same arrangement as `hibiscus/cycle.py` and for the same reason.

THE STATE IS A PLAIN DICT AND MUST SURVIVE json.dumps/json.loads UNCHANGED. That is why every
per-seat thing is a **list indexed by position** rather than a dict keyed by a seat id: JSON
turns integer keys into strings and the bug that follows is the kind that only shows up in
production. `state['seats']` holds the database ids in play order; everything else — hands,
said_uno, misses — is indexed by the same position.

Times are ISO strings in the state and datetimes at the door. Nothing here reads a clock: `now`
is always handed in, because a rule that asks the time cannot be tested.

Every function that changes something returns `(state, error)`. `error` is None on success and a
sentence for a person on failure; the state is returned unchanged alongside it. Nothing raises
for an ordinary refusal — a player tapping the wrong card is not an exception.
"""
import random
from datetime import datetime, timedelta

from . import deck

#: Cards each player starts with.
DEAL = 7

#: How long you have to call UNO after playing your second-to-last card, and how long anyone
#: else has to catch you. One window, both purposes.
UNO_SECONDS = 5

#: A turn, before it is taken for you. Long enough to pick a phone up.
TURN_SECONDS = 90

#: Consecutive turns run out of time before the seat is played for them.
MISSES_BEFORE_TAKEOVER = 3

#: What a player draws when caught without having called it.
CAUGHT_PENALTY = 2

#: The arguments people actually have, and what the box says.
DEFAULTS = {
    'stacking': False,        # +2 on +2 and +4 on +4, the total falling on whoever stops
    # ON, and the one place this differs from the printed rules by default. The box says
    # take exactly one card and lose your turn if it does not fit. On a phone that reads as
    # a punishment: you tap the pile, something appears, and your turn is gone before you
    # have seen what you got. Drawing until something fits is how people actually play, and
    # anybody who wants the printed rule turns this off when they make the table.
    'draw_until': True,
    'seven_zero': False,      # 7 swaps hands with a chosen seat, 0 passes every hand round
    'free_wild_four': False,  # a wild draw four whenever you like, rather than only when stuck
}

_SYSTEM = random.SystemRandom()


def options(given=None):
    """The room's toggles, with anything missing filled in from the box."""
    out = dict(DEFAULTS)
    out.update({k: bool(v) for k, v in (given or {}).items() if k in DEFAULTS})
    return out


# --------------------------------------------------------------------------- #
# Time, kept at the edges
# --------------------------------------------------------------------------- #
def _at(when):
    return when.isoformat() if when else None


def _due(stamp, now):
    """True when `now` has reached a stored deadline. A missing deadline is never due."""
    return bool(stamp) and now >= datetime.fromisoformat(stamp)


# --------------------------------------------------------------------------- #
# Starting a round
# --------------------------------------------------------------------------- #
def new_round(seats, now, rng=None, opts=None):
    """Shuffle, deal seven each, and turn one card over — honouring what that card does.

    The opening flip is the part everybody forgets. A wild draw four goes back in and we try
    again; a wild means the first player names the colour before playing; a skip, reverse or
    draw two takes effect before anyone has had a turn.
    """
    rng = rng or _SYSTEM
    opts = options(opts)
    count = len(seats)
    cards = deck.fresh()
    rng.shuffle(cards)

    hands = [sorted(cards[i * DEAL:(i + 1) * DEAL]) for i in range(count)]
    pile = cards[count * DEAL:]

    # A wild draw four may not start the game: it goes back and another is turned over.
    while deck.value_of(pile[-1]) == 'draw4':
        card = pile.pop()
        pile.insert(rng.randrange(len(pile)), card)
    first = pile.pop()

    state = {
        'seats': list(seats),
        'turn': 0,
        'direction': 1,
        'colour': deck.colour_of(first),
        'hands': hands,
        'draw': pile,
        'discard': [first],
        'pending': {'n': 0, 'kind': None},
        'said_uno': [False] * count,
        'uno': None,
        'need_colour': False,
        'need_swap': False,
        'drew': None,
        'turn_until': None,
        'think_until': None,
        'misses': [0] * count,
        'log': [],
        'winner': None,
        'phase': 'playing',
    }

    value = deck.value_of(first)
    if value == 'wild':
        state['need_colour'] = True          # the first player names it, then plays
        _say(state, 'A wild was turned over')
    elif value == 'skip':
        _say(state, 'The first turn was skipped')
        _advance(state)
    elif value == 'reverse':
        # Flipping the direction first makes the two-player case fall out: next() then lands on
        # the dealer, which is the same seat "acts as a skip" would have chosen.
        state['direction'] = -1
        _say(state, 'Play starts the other way round')
        _advance(state)
    elif value == 'draw2':
        _give(state, state['turn'], 2, rng)
        _say(state, 'The first player drew two and was skipped')
        _advance(state)

    _start_turn(state, now)
    return state


# --------------------------------------------------------------------------- #
# Small movements
# --------------------------------------------------------------------------- #
def _count(state):
    return len(state['seats'])


def _next(state, frm=None, steps=1):
    frm = state['turn'] if frm is None else frm
    return (frm + state['direction'] * steps) % _count(state)


def _advance(state, steps=1):
    state['turn'] = _next(state, steps=steps)


def _start_turn(state, now):
    """Whose turn it is from now, and until when. Clears what belonged to the last turn."""
    state['drew'] = None
    state['turn_until'] = _at(now + timedelta(seconds=TURN_SECONDS))


def _say(state, text):
    """The log the table reads. Capped, because a long round is still one row."""
    state['log'] = (state['log'] + [text])[-30:]


def _refill(state, rng):
    """The draw pile is empty: everything under the top card is shuffled back into it.

    A wild in there goes back to having no colour of its own — the colour it was given was a
    fact about one turn, not about the card.
    """
    if len(state['discard']) <= 1:
        return False
    top = state['discard'][-1]
    rest = state['discard'][:-1]
    rng.shuffle(rest)
    state['draw'] = rest
    state['discard'] = [top]
    _say(state, 'The pile was shuffled back in')
    return True


def _give(state, seat, many, rng):
    """Hand `many` cards to a seat, refilling if the pile runs out. Returns what was drawn.

    If the pile runs out and there is nothing to shuffle back, the draw simply stops short —
    an impossible position in a real deck, but a loop that cannot end is worse than a short
    hand.
    """
    drawn = []
    for _ in range(many):
        if not state['draw'] and not _refill(state, rng):
            break
        drawn.append(state['draw'].pop())
    if drawn:
        state['hands'][seat] = sorted(state['hands'][seat] + drawn)
        state['said_uno'][seat] = False       # back above one card: the call is spent
    return drawn


# --------------------------------------------------------------------------- #
# What may be played
# --------------------------------------------------------------------------- #
def playable(state, card, hand, opts):
    """Whether this one card may go on the pile right now."""
    pending = state['pending']
    if pending['n']:
        # A stack is live, so the only answer is the same kind of card or taking the lot.
        return deck.value_of(card) == pending['kind']
    if state['need_colour'] or state['need_swap']:
        return False
    value = deck.value_of(card)
    if value == 'draw4':
        # The printed rule: only when you hold nothing of the colour in play. The server knows
        # the hand, so it simply refuses — which is why there is no challenge to make.
        return bool(opts['free_wild_four']) or not any(
            deck.colour_of(c) == state['colour'] for c in hand)
    if value == 'wild':
        return True
    if deck.colour_of(card) == state['colour']:
        return True
    top = state['discard'][-1]
    return not deck.is_wild(top) and value == deck.value_of(top)


def legal(state, seat, opts):
    """Every card in that seat's hand that may be played, as a sorted list."""
    if state['phase'] != 'playing' or state['turn'] != seat:
        return []
    hand = state['hands'][seat]
    return sorted({c for c in hand if playable(state, c, hand, opts)})


# --------------------------------------------------------------------------- #
# The moves
# --------------------------------------------------------------------------- #
def _mine(state, seat):
    if state['phase'] != 'playing':
        return 'That round is over.'
    if state['turn'] != seat:
        return 'It is not your turn.'
    return None


def play(state, seat, card, now, rng=None, opts=None):
    """Put one card on the pile."""
    rng, opts = rng or _SYSTEM, options(opts)
    wrong = _mine(state, seat)
    if wrong:
        return state, wrong
    if state['need_colour']:
        return state, 'Choose a colour first.'
    if state['need_swap']:
        return state, 'Choose whose hand to take first.'
    hand = state['hands'][seat]
    if card not in hand:
        return state, 'That card is not in your hand.'
    if not playable(state, card, hand, opts):
        if deck.value_of(card) == 'draw4':
            return state, 'A wild draw four is only allowed when you have nothing of that colour.'
        return state, 'That card does not go there.'

    hand.remove(card)
    state['discard'].append(card)
    _say(state, f'Played {deck.label(card)}')

    if deck.is_wild(card) and hand:
        # The colour comes as its own move: the pile has changed, the turn has not. A wild as
        # somebody's *last* card skips this — there is nothing left to play on the colour and
        # asking for one would be a picker over a finished round.
        state['need_colour'] = True
        return state, None

    state['colour'] = deck.colour_of(card)
    _settle(state, seat, card, now, rng, opts)
    return state, None


def choose_colour(state, seat, colour, now, rng=None, opts=None):
    """Name the colour, after a wild — or after one was turned over to start the round."""
    rng, opts = rng or _SYSTEM, options(opts)
    wrong = _mine(state, seat)
    if wrong:
        return state, wrong
    if not state['need_colour']:
        return state, 'There is no colour to choose.'
    if colour not in deck.COLOURS:
        return state, 'That is not one of the four colours.'

    state['colour'] = colour
    state['need_colour'] = False
    _say(state, f'Colour is now {colour}')

    # A wild turned over to start the round belongs to nobody: naming its colour is the whole
    # move and the same player then plays on it. A wild played out of a hand carries on into
    # its effect — a draw four still has four cards to hand out.
    if len(state['discard']) == 1:
        return state, None
    _settle(state, seat, state['discard'][-1], now, rng, opts)
    return state, None


def choose_swap(state, seat, other, now, rng=None, opts=None):
    """Whose hand to take, after playing a 7 under the seven-zero rule."""
    rng, opts = rng or _SYSTEM, options(opts)
    wrong = _mine(state, seat)
    if wrong:
        return state, wrong
    if not state['need_swap']:
        return state, 'There is nothing to swap.'
    if not isinstance(other, int) or not 0 <= other < _count(state) or other == seat:
        return state, 'Pick somebody else at the table.'

    _swap(state, seat, other)
    state['need_swap'] = False
    _finish_turn(state, now)
    return state, None


def _swap(state, one, other):
    """Trade two hands. Both calls are spent — you are holding somebody else's cards now, and
    whether *they* called it says nothing about you."""
    hands = state['hands']
    hands[one], hands[other] = hands[other], hands[one]
    state['said_uno'][one] = False
    state['said_uno'][other] = False
    _say(state, 'Hands were swapped')


def draw(state, seat, now, rng=None, opts=None):
    """Take from the pile — one card, or the whole stack somebody has built up."""
    rng, opts = rng or _SYSTEM, options(opts)
    wrong = _mine(state, seat)
    if wrong:
        return state, wrong
    if state['need_colour'] or state['need_swap']:
        return state, 'Finish the card you played first.'
    if state['drew'] is not None:
        return state, 'You have already drawn this turn.'

    pending = state['pending']
    if pending['n']:
        _give(state, seat, pending['n'], rng)
        _say(state, f'Took {pending["n"]} cards')
        state['pending'] = {'n': 0, 'kind': None}
        _advance(state)
        _start_turn(state, now)
        return state, None

    # The hand is read back each time rather than held: _give rebinds state['hands'][seat],
    # so a reference taken before the first draw is a different list by the second. It
    # matters — whether a drawn wild draw four may be played depends on what else is in
    # there, and in draw-until mode something of that colour may have arrived on the way.
    taken = _give(state, seat, 1, rng)
    while (opts['draw_until'] and taken
           and not playable(state, taken[-1], state['hands'][seat], opts)):
        more = _give(state, seat, 1, rng)
        if not more:
            break
        taken += more
    _say(state, 'Drew a card' if len(taken) == 1 else f'Drew {len(taken)} cards')

    # Drawn something you can use? The turn stays with you, to play it or pass. Otherwise it is
    # over — there is nothing to decide and making somebody tap "pass" is not a decision.
    if taken and playable(state, taken[-1], state['hands'][seat], opts):
        state['drew'] = taken[-1]
        return state, None
    _advance(state)
    _start_turn(state, now)
    return state, None


def pass_turn(state, seat, now):
    """Keep the card you drew and give up the turn."""
    wrong = _mine(state, seat)
    if wrong:
        return state, wrong
    if state['drew'] is None:
        return state, 'Draw a card first.'
    _say(state, 'Passed')
    _finish_turn(state, now)
    return state, None


def say_uno(state, seat, now):
    """The call. Allowed while holding two — before the card lands — or inside the window."""
    if state['phase'] != 'playing':
        return state, 'That round is over.'
    if len(state['hands'][seat]) > 2:
        return state, 'Not yet.'
    state['said_uno'][seat] = True
    window = state['uno']
    if window and window['seat'] == seat:
        state['uno'] = None
        _say(state, 'Called it in time')
    return state, None


def catch(state, by, now):
    """Somebody else did not call it. Two cards, and only while the window is open."""
    window = state['uno']
    if not window or _due(window['until'], now):
        return state, 'There is nobody to catch.'
    if window['seat'] == by:
        return state, 'You cannot catch yourself.'
    caught = window['seat']
    _give(state, caught, CAUGHT_PENALTY, _SYSTEM)
    state['uno'] = None
    _say(state, 'Caught without calling it — two cards')
    return state, None


# --------------------------------------------------------------------------- #
# What a played card does
# --------------------------------------------------------------------------- #
def _settle(state, seat, card, now, rng, opts):
    """Going out, the UNO window, then the card's effect and the next turn."""
    hand = state['hands'][seat]
    value = deck.value_of(card)
    if not hand:
        # The printed rule, and easy to miss: a draw two or draw four played as somebody's last
        # card still has to be taken, and those cards count towards the winner's score.
        if value in ('draw2', 'draw4'):
            many = 2 if value == 'draw2' else 4
            _give(state, _next(state), many, rng)
            _say(state, f'Next player drew {many}')
        state['phase'] = 'over'
        state['winner'] = seat
        state['uno'] = None
        state['turn_until'] = None
        _say(state, 'Out — round over')
        return
    if len(hand) == 1 and not state['said_uno'][seat]:
        state['uno'] = {'seat': seat, 'until': _at(now + timedelta(seconds=UNO_SECONDS))}

    count = _count(state)

    if value == 'skip':
        _advance(state, 2)
        _start_turn(state, now)
        return
    if value == 'reverse':
        state['direction'] = -state['direction']
        # Two at the table and a reverse is a skip: flipping first, then stepping twice, lands
        # back on the player who played it, which is what "the other one misses a turn" means.
        _advance(state, 2 if count == 2 else 1)
        _start_turn(state, now)
        return
    if value in ('draw2', 'draw4'):
        many = 2 if value == 'draw2' else 4
        if opts['stacking']:
            state['pending'] = {'n': state['pending']['n'] + many, 'kind': value}
            _advance(state)
            _start_turn(state, now)
            return
        victim = _next(state)
        _give(state, victim, many, rng)
        _say(state, f'Next player drew {many}')
        _advance(state, 2)
        _start_turn(state, now)
        return
    if opts['seven_zero'] and value == '7':
        if count == 2:
            _swap(state, seat, _next(state))   # only one person it could be
        else:
            state['need_swap'] = True          # the turn waits until they pick somebody
            return
    elif opts['seven_zero'] and value == '0' and count > 1:
        # Every hand goes to the next seat in the direction of play, so the seat you are
        # holding for is the one behind you.
        hands, back = state['hands'], state['direction']
        state['hands'] = [hands[(i - back) % count] for i in range(count)]
        state['said_uno'] = [False] * count
        _say(state, 'Every hand moved round')

    _finish_turn(state, now)


def _finish_turn(state, now):
    _advance(state)
    _start_turn(state, now)


# --------------------------------------------------------------------------- #
# The lazy clock
# --------------------------------------------------------------------------- #
def tick(state, now, rng=None, opts=None):
    """Bring a game up to date with the time, without anything having happened.

    Nothing here runs on a schedule — there is no scheduler. Whoever looks at the table next is
    the one who closes an expired UNO window and takes a turn that ran out. Returns the state
    and whether anything moved.
    """
    rng, opts = rng or _SYSTEM, options(opts)
    moved = False
    if state['phase'] != 'playing':
        return state, False

    window = state['uno']
    if window and _due(window['until'], now):
        state['uno'] = None
        state['said_uno'][window['seat']] = True     # got away with it
        _say(state, 'Got away with it')
        moved = True

    if _due(state['turn_until'], now):
        seat = state['turn']
        state['misses'][seat] = state['misses'][seat] + 1
        # A turn can run out halfway through a card. Leaving either of these set would hand the
        # next player a question that was never theirs — and a wild with no colour on the pile
        # is a game nobody can play on.
        if state['need_colour']:
            state['colour'] = best_colour(state['hands'][seat])
            state['need_colour'] = False
            _say(state, f'Colour is now {state["colour"]}')
        if state['need_swap']:
            state['need_swap'] = False
        elif state['drew'] is None:
            _give(state, seat, 1, rng)
        _say(state, 'Ran out of time')
        _advance(state)
        _start_turn(state, now)
        moved = True

    return state, moved


def best_colour(hand):
    """The colour somebody holds most of — what to name when a wild has to be answered for.

    Shared rather than duplicated: a turn that runs out needs an answer, and so does every
    wild played by a seat the server is holding.
    """
    counts = {colour: 0 for colour in deck.COLOURS}
    for card in hand:
        colour = deck.colour_of(card)
        if colour:
            counts[colour] += 1
    return max(deck.COLOURS, key=lambda c: (counts[c], -deck.COLOURS.index(c)))


def stuck_seat(state):
    """The seat that has run out of time once too often, or None.

    The caller decides what to do about it — these rules do not know that some seats are played
    by the server, and they are better off not knowing.
    """
    if state['phase'] != 'playing':
        return None
    for seat, misses in enumerate(state['misses']):
        if misses >= MISSES_BEFORE_TAKEOVER:
            return seat
    return None


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #
def round_points(state):
    """What the winner takes: everything left in everybody else's hands."""
    if state['winner'] is None:
        return 0
    return sum(deck.points(card)
               for seat, hand in enumerate(state['hands'])
               if seat != state['winner']
               for card in hand)


# --------------------------------------------------------------------------- #
# What one player is allowed to see
# --------------------------------------------------------------------------- #
def view(state, seat, opts=None):
    """The table as one seat sees it.

    ========================= READ THIS BEFORE EDITING =========================
    This is the only thing that reaches a browser, and it is the whole of the game's secrecy.
    It carries one hand — theirs — and counts for everybody else. It never carries the draw
    pile's order, another player's cards, or anything at all about who is playing a seat.

    Sending the lot and hiding it in the page is not the same thing and never was: anybody can
    open devtools. If something needs to be known by the table, put it here. If it needs to be
    known by one player, put it behind their seat. There is no third option.
    ============================================================================
    """
    opts = options(opts)
    window = state['uno']
    return {
        'phase': state['phase'],
        'turn': state['turn'],
        'direction': state['direction'],
        'colour': state['colour'],
        'top': state['discard'][-1] if state['discard'] else None,
        'hand': list(state['hands'][seat]) if seat is not None else [],
        'playable': legal(state, seat, opts) if seat is not None else [],
        'counts': [len(h) for h in state['hands']],
        'draw_count': len(state['draw']),
        'pending': state['pending']['n'],
        'said_uno': list(state['said_uno']),
        'uno_open': bool(window),
        'uno_seat': window['seat'] if window else None,
        'need_colour': state['need_colour'],
        'need_swap': state['need_swap'],
        'drew': state['drew'] if state['turn'] == seat else None,
        'turn_until': state['turn_until'],
        'winner': state['winner'],
        # The log is NOT here. The table's one sentence is worked out from the state
        # above, and eight lines of history in a payload that goes out every two
        # seconds is weight nobody reads. The console still has the whole of it.
    }
