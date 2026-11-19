"""The seats the server holds — that they play legally, and that they are not obviously it.

The second half is the point. A seat that never errs, answers in a constant time and is awake
at four in the morning is spotted inside a few rounds, so these check the spread and the
mistakes as hard as they check the legality.
"""
import random
from unittest import TestCase

from games.tests_rules import Fixed, at, card, table
from games.uno import deck, house, rules


class Steady:
    """An rng that answers to order, so a choice can be asserted rather than hoped for."""

    def __init__(self, value=0.5, uniform=5.0):
        self.value, self.uniform_value = value, uniform

    def random(self):
        return self.value

    def uniform(self, low, high):
        return min(max(self.uniform_value, low), high)

    def shuffle(self, cards):
        pass

    def randrange(self, n):
        return 0


class ChoosingTests(TestCase):

    def test_it_plays_something_it_is_allowed_to_play(self):
        state = table([[card('red', '9'), card('blue', '2')], [0], [1]], top=card('red', '3'))
        move = house.choose(state, 0, Steady())
        self.assertEqual(move['what'], 'play')
        self.assertIn(move['card'], rules.legal(state, 0, rules.options()))

    def test_it_draws_when_it_has_nothing(self):
        state = table([[card('blue', '2'), card('green', '8')], [0]], top=card('red', '3'))
        self.assertEqual(house.choose(state, 0, Steady())['what'], 'draw')

    def test_it_gives_up_the_turn_once_it_has_drawn_and_still_cannot_play(self):
        state = table([[card('blue', '2')], [0]], top=card('red', '3'),
                      drew=card('blue', '2'))
        self.assertEqual(house.choose(state, 0, Steady())['what'], 'pass')

    def test_it_names_the_colour_it_holds_most_of(self):
        state = table([[card(None, 'wild'), card('green', '1'), card('green', '5')], [0]],
                      top=card('red', '3'), need_colour=True)
        move = house.choose(state, 0, Steady())
        self.assertEqual(move, {'what': 'colour', 'colour': 'green'})

    def test_it_takes_the_smallest_hand_when_a_seven_asks(self):
        state = table([[card('red', '1')], [0, 1, 2, 3], [9]], top=card('red', '3'),
                      need_swap=True)
        self.assertEqual(house.choose(state, 0, Steady())['seat'], 2)

    def test_it_never_swaps_with_itself(self):
        state = table([[card('red', '1')], [0, 1], [2, 3]], top=card('red', '3'),
                      need_swap=True)
        self.assertNotEqual(house.choose(state, 0, Steady())['seat'], 0)

    def test_it_saves_the_wild_draw_four_for_when_it_is_stuck(self):
        """Rank, not legality — holding a blue means the four is not even allowed here."""
        state = table([[card(None, 'draw4'), card(None, 'wild'), card('blue', '9')], [0]],
                      top=card('blue', '3'))
        move = house.choose(state, 0, Steady())
        self.assertEqual(move['card'], card('blue', '9'))

    def test_it_spends_an_action_card_on_somebody_about_to_go_out(self):
        """A skip is worth more later than now — until the next player is nearly out."""
        calm = table([[card('red', 'skip'), card('red', '9')],
                      [40, 41, 42, 43], [50]], top=card('red', '3'))
        self.assertEqual(house.choose(calm, 0, Steady())['card'], card('red', '9'))

        close = table([[card('red', 'skip'), card('red', '9')],
                       [40], [50]], top=card('red', '3'))
        self.assertEqual(house.choose(close, 0, Steady())['card'], card('red', 'skip'))


class NotObviouslyItTests(TestCase):
    """The tells, and that each of them is covered."""

    def test_think_time_is_a_spread_not_a_number(self):
        rng = random.Random(7)
        times = {house.think_seconds(rng) for _ in range(200)}
        self.assertGreater(len(times), 100, 'the same answer every time is the tell')
        self.assertGreaterEqual(min(times), house.QUICK[0])
        self.assertLessEqual(max(times), house.SLOW[1])

    def test_some_turns_are_a_long_think_and_some_are_instant(self):
        rng = random.Random(11)
        times = [house.think_seconds(rng) for _ in range(400)]
        self.assertTrue(any(t <= house.QUICK[1] for t in times), 'never answers quickly')
        self.assertTrue(any(t >= house.SLOW[0] for t in times), 'never stops to think')

    def test_it_does_not_always_take_the_best_card(self):
        state = table([[card('red', '9'), card('red', '1'), card('red', '5')], [0], [1]],
                      top=card('red', '3'))
        rng = random.Random(3)
        picked = {house.choose(state, 0, rng)['card'] for _ in range(80)}
        self.assertGreater(len(picked), 1, 'the same card every time is the tell')

    def test_every_card_it_ever_picks_is_still_legal(self):
        """Being imperfect is not the same as being wrong: a mistake is a worse card, never an
        illegal one."""
        state = table([[card('red', '9'), card('red', '1'), card('blue', '2')], [0], [1]],
                      top=card('red', '3'))
        allowed = set(rules.legal(state, 0, rules.options()))
        rng = random.Random(5)
        for _ in range(200):
            self.assertIn(house.choose(state, 0, rng)['card'], allowed)

    def test_it_forgets_to_call_it_about_as_often_as_anybody(self):
        state = table([[card('red', '9'), card('red', '1')], [0], [1]], top=card('red', '3'))
        rng = random.Random(13)
        calls = [house.choose(state, 0, rng).get('uno') for _ in range(300)]
        self.assertTrue(any(calls), 'never calls it')
        self.assertTrue(not all(calls), 'never forgets, which nobody manages')

    def test_it_misses_a_turn_now_and_then(self):
        rng = random.Random(17)
        missed = [house.wanders_off(rng) for _ in range(500)]
        self.assertTrue(any(missed), 'always answers, which nobody does')
        self.assertLess(sum(missed) / len(missed), 0.1, 'away more often than it plays')

    def test_it_does_not_call_uno_while_holding_a_fistful(self):
        state = table([[card('red', '9'), card('red', '1'), card('red', '5')], [0]],
                      top=card('red', '3'))
        self.assertFalse(house.choose(state, 0, Steady())['uno'])


class PlayingItOutTests(TestCase):

    def test_a_whole_round_between_four_of_them_finishes(self):
        """The strongest thing available without a browser: deal four seats, let them play each
        other to the end, and check the deck is all still there when somebody goes out."""
        rng = random.Random(29)
        state = rules.new_round([1, 2, 3, 4], at(), rng)
        seen = 0
        while state['phase'] == 'playing' and seen < 600:
            seat = state['turn']
            move = house.choose(state, seat, rng)
            state, error = house.apply(state, seat, move, at(seen), rng)
            self.assertIsNone(error, f'move {seen}: {move} → {error}')
            seen += 1
        self.assertEqual(state['phase'], 'over')
        self.assertIsNotNone(state['winner'])
        self.assertEqual(len(state['hands'][state['winner']]), 0)

        everywhere = ([c for hand in state['hands'] for c in hand]
                      + state['draw'] + state['discard'])
        self.assertEqual(sorted(everywhere), deck.fresh(), 'a card went missing')

    def test_a_whole_round_with_every_house_rule_on_also_finishes(self):
        opts = {'stacking': True, 'draw_until': True, 'seven_zero': True,
                'free_wild_four': True}
        rng = random.Random(31)
        state = rules.new_round([1, 2, 3], at(), rng, opts)
        seen = 0
        while state['phase'] == 'playing' and seen < 900:
            seat = state['turn']
            move = house.choose(state, seat, rng, opts)
            state, error = house.apply(state, seat, move, at(seen), rng, opts)
            self.assertIsNone(error, f'move {seen}: {move} → {error}')
            seen += 1
        self.assertEqual(state['phase'], 'over')
        everywhere = ([c for hand in state['hands'] for c in hand]
                      + state['draw'] + state['discard'])
        self.assertEqual(sorted(everywhere), deck.fresh(), 'a card went missing')

    def test_two_at_the_table_also_finishes(self):
        """Where reverse means skip and a seven has only one person to choose."""
        opts = {'seven_zero': True}
        rng = random.Random(37)
        state = rules.new_round([1, 2], at(), rng, opts)
        seen = 0
        while state['phase'] == 'playing' and seen < 600:
            seat = state['turn']
            state, error = house.apply(state, seat, house.choose(state, seat, rng, opts),
                                       at(seen), rng, opts)
            self.assertIsNone(error)
            seen += 1
        self.assertEqual(state['phase'], 'over')

    def test_a_hundred_rounds_never_lose_a_card_or_hang(self):
        rng = random.Random(41)
        for round_number in range(100):
            count = rng.choice([2, 3, 4, 5, 6])
            opts = {name: rng.random() < 0.5 for name in rules.DEFAULTS}
            state = rules.new_round(list(range(count)), at(), rng, opts)
            seen = 0
            while state['phase'] == 'playing' and seen < 1200:
                seat = state['turn']
                state, error = house.apply(state, seat, house.choose(state, seat, rng, opts),
                                           at(seen), rng, opts)
                self.assertIsNone(error, f'round {round_number}: {error}')
                seen += 1
            self.assertEqual(state['phase'], 'over', f'round {round_number} never ended')
            everywhere = ([c for hand in state['hands'] for c in hand]
                          + state['draw'] + state['discard'])
            self.assertEqual(sorted(everywhere), deck.fresh(),
                             f'round {round_number} lost a card')
            self.assertGreaterEqual(rules.round_points(state), 0)
