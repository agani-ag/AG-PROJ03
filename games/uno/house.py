"""How a seat the server is holding decides what to do.

Pure, like `rules.py`: a state and a seat in, a move out. It writes nothing and reads no clock,
so a test can put it in a position and assert exactly what it does.

PLAYING WELL IS NOT THE POINT. A seat that answers in the same two seconds every time, never
misjudges and is awake at four in the morning is spotted inside a few rounds, and the whole
reason these seats exist is that a table of two is not a game. So three things are deliberate:

  * think time comes from a spread, not a constant — usually a few seconds, sometimes long
    enough that somebody wonders, occasionally instant on an obvious play;
  * a small share of turns take the second-best option, which is what a person does;
  * a turn is allowed to run out now and again, exactly as a person's does.

The randomness all arrives through `rng`, so a test hands in one that does not surprise it.
"""
import random

from . import deck, rules

#: How long a turn takes, in seconds. Most are ordinary; one in six is a long think; one in
#: eight is the instant answer of somebody who had already decided.
QUICK = (0.4, 1.2)
USUAL = (2.0, 7.0)
SLOW = (11.0, 26.0)

#: How often an ordinary mistake is made, and how often a turn is simply missed.
MISTAKE = 0.12
VANISH = 0.03

_SYSTEM = random.SystemRandom()


def think_seconds(rng=None):
    """How long before this seat answers. Never a sleep — the caller stores a deadline."""
    rng = rng or _SYSTEM
    roll = rng.random()
    if roll < 0.125:
        low, high = QUICK
    elif roll < 0.29:
        low, high = SLOW
    else:
        low, high = USUAL
    return round(rng.uniform(low, high), 2)


def wanders_off(rng=None):
    """Whether this one turn is simply not answered. Being reliable is a tell."""
    return (rng or _SYSTEM).random() < VANISH


def _rank(state, card, hand, opts):
    """How much this seat wants to play a given card. Higher is keener.

    Deliberately shallow: it counts what is in front of it and never looks at what anybody has
    played before. A seat that tracks the discard pile across a whole round plays better than
    the people at the table, which is its own kind of tell.
    """
    value = deck.value_of(card)
    colour = deck.colour_of(card)
    mine = [deck.colour_of(c) for c in hand]

    if value == 'draw4':
        score = 1                       # the last resort, and the rules mostly agree
    elif value == 'wild':
        score = 2
    elif value in ('draw2', 'skip', 'reverse'):
        # Held back in the ordinary way — a skip is worth more later than it is now — and
        # spent the moment the next player is close to going out.
        nxt = (state['turn'] + state['direction']) % len(state['seats'])
        score = 30 if len(state['hands'][nxt]) <= 2 else 4
    else:
        score = 6

    # Shedding from the colour you hold most of keeps your options open next turn.
    if colour:
        score += mine.count(colour)
        # Matching the colour rather than the number keeps the colour yours.
        if colour == state['colour']:
            score += 2
    return score


def choose(state, seat, rng=None, opts=None):
    """The move this seat makes, as a dict the caller can hand straight to `rules`.

    One of:
        {'what': 'colour', 'colour': 'red'}
        {'what': 'swap', 'seat': 2}
        {'what': 'play', 'card': 17, 'uno': True}
        {'what': 'draw'}
        {'what': 'pass'}
    """
    rng, opts = rng or _SYSTEM, rules.options(opts)
    hand = state['hands'][seat]

    if state['need_colour']:
        return {'what': 'colour', 'colour': rules.best_colour(hand)}

    if state['need_swap']:
        # Take the smallest hand that is not your own — the obvious play, and the one a person
        # makes without thinking about it.
        others = [(len(h), i) for i, h in enumerate(state['hands']) if i != seat]
        return {'what': 'swap', 'seat': min(others)[1]}

    playable = rules.legal(state, seat, opts)
    if not playable:
        # Nothing to play. If a card was drawn and it still does not fit, give the turn up.
        return {'what': 'pass'} if state['drew'] is not None else {'what': 'draw'}

    ranked = sorted(playable, key=lambda c: (-_rank(state, c, hand, opts), c))
    pick = ranked[0]
    if len(ranked) > 1 and rng.random() < MISTAKE:
        pick = ranked[1]                 # the ordinary misjudgement, not a random card

    # Calling it is part of the same move: the server does not get to be the one player who
    # never forgets, so it forgets about as often as anybody.
    call = len(hand) == 2 and rng.random() > MISTAKE
    return {'what': 'play', 'card': pick, 'uno': call}


def apply(state, seat, move, now, rng=None, opts=None):
    """Put a chosen move through the rules. Returns `(state, error)` like everything else."""
    rng, opts = rng or _SYSTEM, rules.options(opts)
    what = move.get('what')
    if what == 'colour':
        return rules.choose_colour(state, seat, move['colour'], now, rng, opts)
    if what == 'swap':
        return rules.choose_swap(state, seat, move['seat'], now, rng, opts)
    if what == 'draw':
        return rules.draw(state, seat, now, rng, opts)
    if what == 'pass':
        return rules.pass_turn(state, seat, now)
    if what == 'play':
        if move.get('uno'):
            rules.say_uno(state, seat, now)
        return rules.play(state, seat, move['card'], now, rng, opts)
    return state, 'No such move.'
