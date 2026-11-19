"""The 108 cards, in one fixed order, so a card is an integer.

A hand is `[4, 61, 104]`, not three rows and three joins. The order below never changes, because
the integers are what the saved games are written in — adding a card in the middle would silently
turn every stored hand into a different one. New cards go on the end or not at all.

Nothing here knows about Django, a player or a rule. What a card *does* is in `rules.py`.
"""

COLOURS = ('red', 'yellow', 'green', 'blue')

#: The values that are not numbers. Kept apart because they score differently and because a
#: match on value ("a skip on a skip") has to work for them exactly as it does for a 7.
ACTIONS = ('skip', 'reverse', 'draw2')
WILDS = ('wild', 'draw4')

#: What each is worth in the hand of somebody who did not go out.
ACTION_POINTS = 20
WILD_POINTS = 50


def _build():
    """One 0, two of every other number, two of each action, per colour. Then eight wilds."""
    cards = []
    for colour in COLOURS:
        cards.append((colour, '0'))
        for number in '123456789':
            cards += [(colour, number)] * 2
        for action in ACTIONS:
            cards += [(colour, action)] * 2
    for wild in WILDS:
        cards += [(None, wild)] * 4
    return tuple(cards)


CARDS = _build()
SIZE = len(CARDS)                      # 108, and a test says so rather than this comment


def colour_of(card):
    """The colour printed on it. None for a wild — what colour it *is* while in play is the
    game's `colour`, which the person playing it chooses."""
    return CARDS[card][0]


def value_of(card):
    return CARDS[card][1]


def is_wild(card):
    return CARDS[card][0] is None


def is_number(card):
    return CARDS[card][1].isdigit()


def points(card):
    """What this card adds to the winner's score when it is left in somebody's hand."""
    colour, value = CARDS[card]
    if colour is None:
        return WILD_POINTS
    return int(value) if value.isdigit() else ACTION_POINTS


WORDS = {'skip': 'skip', 'reverse': 'reverse', 'draw2': 'draw two',
         'wild': 'wild', 'draw4': 'wild draw four'}


def label(card):
    """How a card is written in the log: "red 5", "blue skip", "wild draw four"."""
    colour, value = CARDS[card]
    word = WORDS.get(value, value)
    return word if colour is None else f'{colour} {word}'


def fresh():
    """Every card once, in canonical order. Shuffling is the caller's business — and it uses
    SystemRandom, because a card game seeded from the clock is a card game somebody can read."""
    return list(range(SIZE))
