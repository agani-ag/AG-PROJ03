"""The UNO rules, on their own. No database, no request, no clock of their own.

Every state here is built by hand so the test says what the situation *is* rather than dealing
until one turns up. `_at` is the only clock: time is a number of seconds from a fixed moment, so
a window that closes and a turn that runs out can both be checked without waiting for either.
"""
import json
import re
from datetime import datetime, timedelta, timezone
from unittest import TestCase

from games.uno import deck, rules

START = datetime(2026, 11, 19, 10, 0, tzinfo=timezone.utc)


def at(seconds=0):
    return START + timedelta(seconds=seconds)


def card(colour, value):
    """The first card matching a colour and value — "the red 5", as a person would say it."""
    for index, (c, v) in enumerate(deck.CARDS):
        if c == colour and v == value:
            return index
    raise AssertionError(f'no {colour} {value} in the deck')


def other(colour, value):
    """Its twin. Every card but the zeros and the wilds comes in two."""
    found = [i for i, (c, v) in enumerate(deck.CARDS) if c == colour and v == value]
    return found[1]


class Fixed:
    """A shuffler that does not shuffle, so a deal can be read off the deck order."""

    def __init__(self, order=None):
        self.order = order

    def shuffle(self, cards):
        if self.order is not None:
            cards[:] = list(self.order)

    def randrange(self, n):
        return 0


def table(hands, top, colour=None, turn=0, direction=1, pile=None, **rest):
    """A game in a particular position, written out rather than played into."""
    state = {
        'seats': list(range(len(hands))),
        'turn': turn,
        'direction': direction,
        'colour': colour or deck.colour_of(top),
        'hands': [sorted(h) for h in hands],
        'draw': list(pile if pile is not None else range(60, 90)),
        'discard': [top],
        'pending': {'n': 0, 'kind': None},
        'said_uno': [False] * len(hands),
        'uno': None,
        'need_colour': False,
        'need_swap': False,
        'drew': None,
        'turn_until': rules._at(at(rules.TURN_SECONDS)),
        'think_until': None,
        'misses': [0] * len(hands),
        'log': [],
        'winner': None,
        'phase': 'playing',
    }
    state.update(rest)
    return state


class DeckTests(TestCase):

    def test_there_are_a_hundred_and_eight_of_them(self):
        self.assertEqual(deck.SIZE, 108)
        self.assertEqual(len(set(deck.fresh())), 108)

    def test_each_colour_has_twenty_five(self):
        for colour in deck.COLOURS:
            self.assertEqual(sum(1 for c, _ in deck.CARDS if c == colour), 25, colour)

    def test_one_zero_and_two_of_every_other_number(self):
        for colour in deck.COLOURS:
            values = [v for c, v in deck.CARDS if c == colour]
            self.assertEqual(values.count('0'), 1, colour)
            for number in '123456789':
                self.assertEqual(values.count(number), 2, f'{colour} {number}')

    def test_eight_wilds_split_evenly(self):
        values = [v for c, v in deck.CARDS if c is None]
        self.assertEqual((values.count('wild'), values.count('draw4')), (4, 4))

    def test_a_full_deck_is_worth_what_it_should_be(self):
        """Numbers face value, actions 20, wilds 50 — 1240 in all."""
        self.assertEqual(sum(deck.points(c) for c in deck.fresh()), 1240)

    def test_the_order_never_moves(self):
        """Stored hands are written in these integers. Card 0 changing meaning would silently
        rewrite every saved game."""
        self.assertEqual(deck.CARDS[0], ('red', '0'))
        self.assertEqual(deck.CARDS[107], (None, 'draw4'))
        self.assertEqual(deck.label(card('blue', 'draw2')), 'blue draw two')


class DealTests(TestCase):

    def test_seven_each_and_one_turned_over(self):
        state = rules.new_round([1, 2, 3], at(), Fixed())
        self.assertEqual([len(h) for h in state['hands']], [7, 7, 7])
        self.assertEqual(len(state['discard']), 1)
        self.assertEqual(len(state['draw']), 108 - 21 - 1)

    def test_nobody_is_dealt_the_same_card_twice(self):
        state = rules.new_round([1, 2, 3, 4], at(), Fixed())
        seen = [c for hand in state['hands'] for c in hand] + state['draw'] + state['discard']
        self.assertEqual(len(seen), 108)
        self.assertEqual(len(set(seen)), 108)

    def test_a_wild_draw_four_never_starts_the_game(self):
        """It goes back into the pile and another is turned over."""
        order = deck.fresh()
        order.remove(card(None, 'draw4'))
        order.append(card(None, 'draw4'))            # dealt last, so it would be turned over
        state = rules.new_round([1, 2], at(), Fixed(order))
        self.assertNotEqual(deck.value_of(state['discard'][0]), 'draw4')

    def test_a_wild_turned_over_waits_for_a_colour(self):
        order = deck.fresh()
        order.remove(card(None, 'wild'))
        order.append(card(None, 'wild'))
        state = rules.new_round([1, 2], at(), Fixed(order))
        self.assertTrue(state['need_colour'])
        self.assertIsNone(state['colour'])
        self.assertEqual(state['turn'], 0)           # the first player names it, then plays

    def test_naming_the_colour_of_the_opening_wild_does_not_pass_the_turn(self):
        order = deck.fresh()
        order.remove(card(None, 'wild'))
        order.append(card(None, 'wild'))
        state = rules.new_round([1, 2], at(), Fixed(order))
        state, error = rules.choose_colour(state, 0, 'green', at(1))
        self.assertIsNone(error)
        self.assertEqual((state['colour'], state['turn']), ('green', 0))

    def test_a_skip_turned_over_costs_the_first_player_their_turn(self):
        order = deck.fresh()
        order.remove(card('red', 'skip'))
        order.append(card('red', 'skip'))
        state = rules.new_round([1, 2, 3], at(), Fixed(order))
        self.assertEqual(state['turn'], 1)

    def test_a_reverse_turned_over_starts_play_the_other_way(self):
        order = deck.fresh()
        order.remove(card('red', 'reverse'))
        order.append(card('red', 'reverse'))
        state = rules.new_round([1, 2, 3], at(), Fixed(order))
        self.assertEqual(state['direction'], -1)
        self.assertEqual(state['turn'], 2)           # the dealer leads

    def test_a_reverse_turned_over_with_two_playing_acts_as_a_skip(self):
        order = deck.fresh()
        order.remove(card('red', 'reverse'))
        order.append(card('red', 'reverse'))
        state = rules.new_round([1, 2], at(), Fixed(order))
        self.assertEqual(state['turn'], 1)

    def test_a_draw_two_turned_over_hits_the_first_player(self):
        order = deck.fresh()
        order.remove(card('red', 'draw2'))
        order.append(card('red', 'draw2'))
        state = rules.new_round([1, 2, 3], at(), Fixed(order))
        self.assertEqual(len(state['hands'][0]), 9)
        self.assertEqual(state['turn'], 1)

    def test_the_whole_state_survives_being_written_down(self):
        """It is stored as JSON. Integer keys would come back as strings, which is why nothing
        here is keyed by a seat id."""
        state = rules.new_round([4, 5, 6], at(), Fixed())
        self.assertEqual(json.loads(json.dumps(state)), state)


class PlayingTests(TestCase):

    def test_colour_matches(self):
        state = table([[card('red', '9')], [0]], top=card('red', '3'))
        _, error = rules.play(state, 0, card('red', '9'), at(1))
        self.assertIsNone(error)

    def test_value_matches_across_colours(self):
        state = table([[card('blue', '3')], [0]], top=card('red', '3'))
        _, error = rules.play(state, 0, card('blue', '3'), at(1))
        self.assertIsNone(error)

    def test_neither_matching_is_refused_and_changes_nothing(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'))
        before = json.dumps(state)
        state, error = rules.play(state, 0, card('blue', '9'), at(1))
        self.assertEqual(error, 'That card does not go there.')
        self.assertEqual(json.dumps(state), before)

    def test_only_the_colour_counts_on_top_of_a_wild(self):
        """A wild has no value to match, so a 3 on a wild-named-red is a red 3 or nothing."""
        state = table([[card('blue', '3')], [0]], top=card(None, 'wild'), colour='red')
        _, error = rules.play(state, 0, card('blue', '3'), at(1))
        self.assertEqual(error, 'That card does not go there.')

    def test_a_card_you_do_not_hold_is_refused(self):
        state = table([[card('red', '9')], [0]], top=card('red', '3'))
        _, error = rules.play(state, 0, card('red', '5'), at(1))
        self.assertEqual(error, 'That card is not in your hand.')

    def test_playing_out_of_turn_is_refused(self):
        state = table([[card('red', '9')], [card('red', '5')]], top=card('red', '3'))
        _, error = rules.play(state, 1, card('red', '5'), at(1))
        self.assertEqual(error, 'It is not your turn.')

    def test_skip_costs_the_next_player_their_turn(self):
        state = table([[card('red', 'skip'), card('blue', '4')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'skip'), at(1))
        self.assertEqual(state['turn'], 2)

    def test_reverse_turns_play_around(self):
        state = table([[card('red', 'reverse'), card('blue', '4')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'reverse'), at(1))
        self.assertEqual(state['direction'], -1)
        self.assertEqual(state['turn'], 2)

    def test_reverse_with_two_playing_is_a_skip(self):
        """The one rule everybody gets wrong: it comes straight back to you."""
        state = table([[card('red', 'reverse'), card('red', '1')], [0]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'reverse'), at(1))
        self.assertEqual(state['turn'], 0)

    def test_draw_two_without_stacking_hits_the_next_player_and_skips_them(self):
        state = table([[card('red', 'draw2'), card('blue', '4')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1))
        self.assertEqual(len(state['hands'][1]), 3)
        self.assertEqual(state['turn'], 2)
        self.assertEqual(state['pending']['n'], 0)

    def test_a_wild_waits_for_its_colour_before_the_turn_moves(self):
        state = table([[card(None, 'wild'), card('red', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card(None, 'wild'), at(1))
        self.assertTrue(state['need_colour'])
        self.assertEqual(state['turn'], 0)
        state, error = rules.choose_colour(state, 0, 'blue', at(2))
        self.assertIsNone(error)
        self.assertEqual((state['colour'], state['turn']), ('blue', 1))

    def test_nothing_else_may_be_played_while_a_colour_is_owed(self):
        state = table([[card(None, 'wild'), card('red', '1')], [0]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card(None, 'wild'), at(1))
        _, error = rules.play(state, 0, card('red', '1'), at(2))
        self.assertEqual(error, 'Choose a colour first.')

    def test_a_colour_that_is_not_one_of_the_four_is_refused(self):
        state = table([[card(None, 'wild'), card('red', '1')], [0]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card(None, 'wild'), at(1))
        _, error = rules.choose_colour(state, 0, 'purple', at(2))
        self.assertEqual(error, 'That is not one of the four colours.')

    def test_a_wild_draw_four_needs_you_to_be_stuck(self):
        """The printed rule. The server holds the hand, so it answers rather than challenges."""
        state = table([[card(None, 'draw4'), card('red', '1')], [0]], top=card('red', '3'))
        _, error = rules.play(state, 0, card(None, 'draw4'), at(1))
        self.assertIn('nothing of that colour', error)

    def test_a_wild_draw_four_is_fine_when_you_really_are_stuck(self):
        state = table([[card(None, 'draw4'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, error = rules.play(state, 0, card(None, 'draw4'), at(1))
        self.assertIsNone(error)
        state, _ = rules.choose_colour(state, 0, 'blue', at(2))
        self.assertEqual(len(state['hands'][1]), 5)
        self.assertEqual(state['turn'], 2)

    def test_the_free_wild_four_toggle_lifts_that(self):
        state = table([[card(None, 'draw4'), card('red', '1')], [0]], top=card('red', '3'))
        _, error = rules.play(state, 0, card(None, 'draw4'), at(1),
                              opts={'free_wild_four': True})
        self.assertIsNone(error)

    def test_a_number_card_matching_only_by_colour_blocks_the_wild_four(self):
        """Holding a red *anything* is what counts, not a red that would be legal."""
        state = table([[card(None, 'draw4'), card('red', 'skip')], [0]], top=card('red', '3'))
        _, error = rules.play(state, 0, card(None, 'draw4'), at(1))
        self.assertIsNotNone(error)


class DrawingTests(TestCase):

    def test_drawing_something_playable_leaves_the_turn_with_you(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'),
                      pile=[card('red', '7')])
        state, error = rules.draw(state, 0, at(1))
        self.assertIsNone(error)
        self.assertEqual(state['turn'], 0)
        self.assertEqual(state['drew'], card('red', '7'))

    def test_drawing_something_useless_ends_the_turn_on_its_own(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'),
                      pile=[card('blue', '7')])
        state, _ = rules.draw(state, 0, at(1))
        self.assertEqual(state['turn'], 1)
        self.assertIsNone(state['drew'])

    def test_you_cannot_draw_twice(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'),
                      pile=[card('red', '7'), card('red', '8')])
        state, _ = rules.draw(state, 0, at(1))
        _, error = rules.draw(state, 0, at(2))
        self.assertEqual(error, 'You have already drawn this turn.')

    def test_passing_needs_a_draw_first(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'))
        _, error = rules.pass_turn(state, 0, at(1))
        self.assertEqual(error, 'Draw a card first.')

    def test_draw_until_keeps_going_until_something_fits(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'),
                      pile=[card('red', '7'), card('blue', '2'), card('green', '2')])
        state, _ = rules.draw(state, 0, at(1), opts={'draw_until': True})
        self.assertEqual(len(state['hands'][0]), 4)       # two useless, then the red 7
        self.assertEqual(state['turn'], 0)

    def test_the_pile_is_shuffled_back_in_when_it_runs_out(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'), pile=[])
        state['discard'] = [card('green', '1'), card('green', '2'), card('red', '3')]
        state, _ = rules.draw(state, 0, at(1), rng=Fixed())
        self.assertEqual(state['discard'], [card('red', '3')])
        self.assertEqual(len(state['hands'][0]), 2)

    def test_running_out_with_nothing_to_shuffle_back_does_not_hang(self):
        """Impossible with a real deck. A loop that cannot end is worse than a short hand."""
        state = table([[card('blue', '9')], [0]], top=card('red', '3'), pile=[])
        state, error = rules.draw(state, 0, at(1), opts={'draw_until': True})
        self.assertIsNone(error)
        self.assertEqual(state['turn'], 1)


class StackingTests(TestCase):

    STACK = {'stacking': True}

    def test_a_draw_two_waits_to_be_answered(self):
        state = table([[card('red', 'draw2'), card('blue', '4')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1), opts=self.STACK)
        self.assertEqual(state['pending'], {'n': 2, 'kind': 'draw2'})
        self.assertEqual(state['turn'], 1)
        self.assertEqual(len(state['hands'][1]), 1)       # nothing drawn yet

    def test_a_draw_two_on_a_draw_two_doubles_it(self):
        state = table([[card('red', 'draw2'), card('blue', '4')],
                       [other('blue', 'draw2'), card('green', '4')], [1]],
                      top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1), opts=self.STACK)
        state, error = rules.play(state, 1, other('blue', 'draw2'), at(2), opts=self.STACK)
        self.assertIsNone(error)
        self.assertEqual(state['pending'], {'n': 4, 'kind': 'draw2'})

    def test_the_whole_stack_falls_on_whoever_stops(self):
        state = table([[card('red', 'draw2'), card('blue', '4')],
                       [other('blue', 'draw2'), card('green', '4')],
                       [card('green', '9')]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1), opts=self.STACK)
        state, _ = rules.play(state, 1, other('blue', 'draw2'), at(2), opts=self.STACK)
        state, _ = rules.draw(state, 2, at(3), opts=self.STACK)
        self.assertEqual(len(state['hands'][2]), 5)       # one it had, four taken
        self.assertEqual(state['pending']['n'], 0)
        self.assertEqual(state['turn'], 0)

    def test_an_ordinary_card_cannot_answer_a_stack(self):
        state = table([[card('red', 'draw2'), card('blue', '4')], [card('red', '9')], [1]],
                      top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1), opts=self.STACK)
        _, error = rules.play(state, 1, card('red', '9'), at(2), opts=self.STACK)
        self.assertEqual(error, 'That card does not go there.')

    def test_a_draw_four_cannot_be_put_on_a_draw_two(self):
        """Like on like. The other way round is a different house rule and not this one."""
        state = table([[card('red', 'draw2')], [card(None, 'draw4'), card('blue', '1')], [1]],
                      top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1), opts=self.STACK)
        _, error = rules.play(state, 1, card(None, 'draw4'), at(2), opts=self.STACK)
        self.assertIsNotNone(error)

    def test_without_the_toggle_nothing_ever_waits(self):
        state = table([[card('red', 'draw2'), card('blue', '4')], [other('blue', 'draw2')], [1]],
                      top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1))
        self.assertEqual(state['pending']['n'], 0)
        self.assertEqual(len(state['hands'][1]), 3)


class SevenZeroTests(TestCase):

    SEVEN = {'seven_zero': True}

    def test_a_seven_asks_whose_hand_to_take(self):
        state = table([[card('red', '7'), card('red', '1')], [0, 1, 2], [3]],
                      top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '7'), at(1), opts=self.SEVEN)
        self.assertTrue(state['need_swap'])
        self.assertEqual(state['turn'], 0)
        state, error = rules.choose_swap(state, 0, 1, at(2), opts=self.SEVEN)
        self.assertIsNone(error)
        self.assertEqual(len(state['hands'][0]), 3)
        self.assertEqual(state['hands'][1], [card('red', '1')])
        self.assertEqual(state['turn'], 1)

    def test_with_two_playing_there_is_nobody_to_choose(self):
        state = table([[card('red', '7'), card('red', '1')], [0, 1, 2]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '7'), at(1), opts=self.SEVEN)
        self.assertFalse(state['need_swap'])
        self.assertEqual(len(state['hands'][0]), 3)

    def test_you_cannot_swap_with_yourself(self):
        state = table([[card('red', '7'), card('red', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '7'), at(1), opts=self.SEVEN)
        _, error = rules.choose_swap(state, 0, 0, at(2), opts=self.SEVEN)
        self.assertEqual(error, 'Pick somebody else at the table.')

    def test_a_zero_moves_every_hand_along(self):
        state = table([[card('red', '0'), card('blue', '4')], [10], [20]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '0'), at(1), opts=self.SEVEN)
        # Each hand goes to the next seat, so the one you end up with is the seat behind.
        self.assertEqual(state['hands'], [[20], [card('blue', '4')], [10]])

    def test_without_the_toggle_a_seven_is_just_a_seven(self):
        state = table([[card('red', '7'), card('red', '1')], [0, 1, 2], [3]],
                      top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '7'), at(1))
        self.assertFalse(state['need_swap'])
        self.assertEqual(state['turn'], 1)


class CallingItTests(TestCase):

    def test_playing_down_to_one_opens_the_window(self):
        state = table([[card('red', '9'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        self.assertEqual(state['uno'], {'seat': 0, 'until': rules._at(at(1 + rules.UNO_SECONDS))})

    def test_calling_it_first_means_no_window_at_all(self):
        state = table([[card('red', '9'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.say_uno(state, 0, at(1))
        state, _ = rules.play(state, 0, card('red', '9'), at(2))
        self.assertIsNone(state['uno'])

    def test_calling_it_inside_the_window_closes_it(self):
        state = table([[card('red', '9'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        state, error = rules.say_uno(state, 0, at(3))
        self.assertIsNone(error)
        self.assertIsNone(state['uno'])

    def test_being_caught_costs_two_cards(self):
        state = table([[card('red', '9'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        state, error = rules.catch(state, 1, at(3))
        self.assertIsNone(error)
        self.assertEqual(len(state['hands'][0]), 3)
        self.assertIsNone(state['uno'])

    def test_too_late_is_too_late(self):
        state = table([[card('red', '9'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        _, error = rules.catch(state, 1, at(1 + rules.UNO_SECONDS + 1))
        self.assertEqual(error, 'There is nobody to catch.')

    def test_you_cannot_catch_yourself(self):
        state = table([[card('red', '9'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        _, error = rules.catch(state, 0, at(2))
        self.assertEqual(error, 'You cannot catch yourself.')

    def test_the_window_closes_itself_when_nobody_is_looking(self):
        """Nothing runs on a schedule: the next person to look at the table closes it."""
        state = table([[card('red', '9'), card('blue', '1')], [0], [1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        state, moved = rules.tick(state, at(1 + rules.UNO_SECONDS), rng=Fixed())
        self.assertTrue(moved)
        self.assertIsNone(state['uno'])
        self.assertTrue(state['said_uno'][0])

    def test_drawing_back_above_one_card_spends_the_call(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'),
                      pile=[card('blue', '7')], said_uno=[True, False])
        state, _ = rules.draw(state, 0, at(1))
        self.assertFalse(state['said_uno'][0])


class ClockTests(TestCase):

    def test_a_turn_that_runs_out_draws_one_and_moves_on(self):
        state = table([[card('blue', '9')], [0], [1]], top=card('red', '3'))
        state, moved = rules.tick(state, at(rules.TURN_SECONDS), rng=Fixed())
        self.assertTrue(moved)
        self.assertEqual(state['turn'], 1)
        self.assertEqual(len(state['hands'][0]), 2)
        self.assertEqual(state['misses'][0], 1)

    def test_nothing_happens_before_the_time_is_up(self):
        state = table([[card('blue', '9')], [0]], top=card('red', '3'))
        state, moved = rules.tick(state, at(rules.TURN_SECONDS - 1), rng=Fixed())
        self.assertFalse(moved)
        self.assertEqual(state['turn'], 0)

    def test_a_colour_owed_when_the_time_runs_out_is_answered_for_them(self):
        """Otherwise the next player is handed a wild with no colour and nothing to play on."""
        state = table([[card(None, 'wild'), card('blue', '1'), card('blue', '2')], [0], [1]],
                      top=card('red', '3'))
        state, _ = rules.play(state, 0, card(None, 'wild'), at(1))
        state, _ = rules.tick(state, at(rules.TURN_SECONDS + 1), rng=Fixed())
        self.assertFalse(state['need_colour'])
        self.assertEqual(state['colour'], 'blue')         # what they held most of
        self.assertEqual(state['turn'], 1)

    def test_a_swap_owed_when_the_time_runs_out_simply_lapses(self):
        state = table([[card('red', '7'), card('red', '1')], [0, 1], [2]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '7'), at(1), opts={'seven_zero': True})
        state, _ = rules.tick(state, at(rules.TURN_SECONDS + 1), rng=Fixed())
        self.assertFalse(state['need_swap'])
        self.assertEqual(state['hands'][1], [0, 1])       # nobody's hand moved
        self.assertEqual(state['turn'], 1)

    def test_three_in_a_row_marks_the_seat_as_gone(self):
        state = table([[card('blue', '9')], [0], [1]], top=card('red', '3'),
                      misses=[2, 0, 0])
        self.assertIsNone(rules.stuck_seat(state))
        state, _ = rules.tick(state, at(rules.TURN_SECONDS), rng=Fixed())
        self.assertEqual(rules.stuck_seat(state), 0)

    def test_a_finished_round_has_no_clock_left_to_run(self):
        state = table([[card('red', '9')], [0]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        self.assertEqual(state['phase'], 'over')
        state, moved = rules.tick(state, at(10000), rng=Fixed())
        self.assertFalse(moved)


class EndOfRoundTests(TestCase):

    def test_the_last_card_ends_it(self):
        state = table([[card('red', '9')], [0, 1]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        self.assertEqual((state['phase'], state['winner']), ('over', 0))

    def test_a_wild_as_the_last_card_does_not_ask_for_a_colour(self):
        state = table([[card(None, 'wild')], [0]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card(None, 'wild'), at(1))
        self.assertFalse(state['need_colour'])
        self.assertEqual(state['phase'], 'over')

    def test_a_draw_two_as_the_last_card_is_still_taken(self):
        """The printed rule, and the one the first draft of these rules got wrong: those cards
        are drawn and they count towards the winner's score."""
        state = table([[card('red', 'draw2')], [card('blue', '5')]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', 'draw2'), at(1), rng=Fixed())
        self.assertEqual(state['winner'], 0)
        self.assertEqual(len(state['hands'][1]), 3)
        self.assertGreater(rules.round_points(state), 5)

    def test_a_draw_four_as_the_last_card_asks_for_no_colour(self):
        state = table([[card(None, 'draw4')], [card('blue', '5')]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card(None, 'draw4'), at(1), rng=Fixed())
        self.assertFalse(state['need_colour'])
        self.assertEqual(len(state['hands'][1]), 5)

    def test_the_winner_takes_what_everybody_else_is_holding(self):
        state = table([[card('red', '9')],
                       [card('blue', '5'), card('green', 'skip')],
                       [card(None, 'draw4')]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        self.assertEqual(rules.round_points(state), 5 + 20 + 50)

    def test_an_unfinished_round_is_worth_nothing(self):
        state = table([[card('red', '9'), card('red', '1')], [0]], top=card('red', '3'))
        self.assertEqual(rules.round_points(state), 0)

    def test_nothing_may_be_played_after_it_is_over(self):
        state = table([[card('red', '9')], [card('red', '5')]], top=card('red', '3'))
        state, _ = rules.play(state, 0, card('red', '9'), at(1))
        _, error = rules.play(state, 1, card('red', '5'), at(2))
        self.assertEqual(error, 'That round is over.')


class WhatOnePlayerSeesTests(TestCase):
    """The whole of the game's secrecy is one function. These are its tests."""

    def setUp(self):
        self.state = table([[card('red', '9'), card('red', '1')],
                            [card('blue', '5'), card('blue', '6'), card('blue', '7')],
                            [card('green', '2')]], top=card('red', '3'))

    def test_you_are_given_your_own_hand(self):
        seen = rules.view(self.state, 0)
        self.assertEqual(seen['hand'], sorted([card('red', '9'), card('red', '1')]))

    def test_you_are_never_given_anybody_elses(self):
        seen = rules.view(self.state, 0)
        flat = json.dumps(seen)
        for hidden in self.state['hands'][1] + self.state['hands'][2]:
            self.assertNotIn(str(hidden), flat.split('"counts"')[0], f'card {hidden} leaked')
        self.assertEqual(seen['counts'], [2, 3, 1])

    def test_you_are_never_given_the_order_of_the_pile(self):
        """Knowing what comes next is the whole game. A count is all anybody needs."""
        seen = rules.view(self.state, 0)
        self.assertNotIn('draw', [k for k in seen if k != 'draw_count'])
        self.assertEqual(seen['draw_count'], len(self.state['draw']))

    def test_nothing_in_it_says_who_is_playing_a_seat(self):
        """Not false, not hidden — absent. A field that is never sent cannot be read."""
        flat = json.dumps(rules.view(self.state, 0)).lower()
        for word in ('house', 'kind', 'auto', 'computer', 'cpu', 'ai', 'seat_kind'):
            # As whole words: "said_uno" is not somebody saying "ai".
            self.assertIsNone(re.search(r'\b' + word + r'\b', flat), word)

    def test_it_says_what_you_may_actually_play(self):
        seen = rules.view(self.state, 0)
        self.assertEqual(seen['playable'], sorted([card('red', '9'), card('red', '1')]))

    def test_somebody_else_is_told_nothing_they_may_play(self):
        self.assertEqual(rules.view(self.state, 1)['playable'], [])

    def test_the_view_is_json(self):
        self.assertEqual(json.loads(json.dumps(rules.view(self.state, 0))),
                         rules.view(self.state, 0))


class OptionTests(TestCase):

    def test_anything_missing_comes_from_the_box(self):
        self.assertEqual(rules.options({'stacking': True}),
                         {**rules.DEFAULTS, 'stacking': True})

    def test_nonsense_is_ignored_rather_than_stored(self):
        self.assertEqual(rules.options({'wildcards_are_free': True}), rules.DEFAULTS)

    def test_the_box_is_the_default(self):
        self.assertEqual(rules.options(None), rules.DEFAULTS)
        self.assertFalse(rules.DEFAULTS['stacking'])
