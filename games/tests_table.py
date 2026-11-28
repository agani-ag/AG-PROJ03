"""The table, through Django: rooms, seats, the poll, a move, and the two rules that matter.

The two are secrecy — one player is never handed another's cards — and that nothing anywhere a
browser can reach says which chairs this server is holding. Both are enforced by a test rather
than by remembering, because a rule nobody can accidentally break beats one everybody has to
keep in mind.
"""
import json
import random
import re
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.conf import settings as django_settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core import auth as hub
from core.models import Member

from games import auth, service
from games.models import Game, Move, Player, Room, Seat
from games.uno import deck, rules


def member(name='Anand', email='anand@example.com', password='swan7tide'):
    row = Member.objects.create(name=name, email=email)
    hub.set_password(row, password)
    return row


def player(name='Anand', email='anand@example.com'):
    return auth.start(member(name, email))


def sign_in(client, email='anand@example.com', password='swan7tide'):
    return client.post(reverse('member_login'), {'sign_in_id': email, 'password': password})


class Steady(random.Random):
    """Seeded, so a deal and a think time are the same every run."""


class StartingTests(TestCase):

    def setUp(self):
        self.player = player()
        sign_in(self.client)

    def test_a_table_seats_whoever_made_it(self):
        room = service.make_room(self.player)
        self.assertEqual(room.seats.count(), 1)
        self.assertEqual(room.seats.first().player, self.player)
        self.assertEqual(room.phase, Room.Phase.LOBBY)

    def test_a_code_is_easy_to_read_aloud(self):
        """No vowels, no O/0, no I/1/L: somebody is going to say it down a phone."""
        for _ in range(60):
            room = service.make_room(self.player)
            self.assertRegex(room.code, r'^[BCDFGHJKMNPQRSTVWXYZ23456789]{5}$')

    def test_somebody_with_the_code_can_sit_down(self):
        room = service.make_room(self.player)
        seat, error = service.join(room, player('Meena', 'meena@example.com'))
        self.assertIsNone(error)
        self.assertEqual(seat.position, 1)

    def test_sitting_down_twice_keeps_the_same_chair(self):
        room = service.make_room(self.player)
        other = player('Meena', 'meena@example.com')
        first, _ = service.join(room, other)
        again, _ = service.join(room, other)
        self.assertEqual(first.pk, again.pk)

    def test_a_full_table_is_full(self):
        room = service.make_room(self.player)
        service.fill(room, service.MAX_SEATS)
        _, error = service.join(room, player('Meena', 'meena@example.com'))
        self.assertEqual(error, 'That table is full.')

    def test_nobody_joins_once_it_has_started(self):
        room = service.make_room(self.player)
        service.fill(room, 2)
        service.start(room)
        room.refresh_from_db()
        _, error = service.join(room, player('Meena', 'meena@example.com'))
        self.assertEqual(error, 'That game has already started.')

    def test_one_at_the_table_is_not_a_game(self):
        room = service.make_room(self.player)
        _, error = service.start(room)
        self.assertEqual(error, 'There has to be somebody to play against.')

    def test_dealing_gives_everybody_seven(self):
        room = service.make_room(self.player)
        service.fill(room, 4)
        game, error = service.start(room, rng=Steady(1))
        self.assertIsNone(error)
        self.assertEqual([len(h) for h in game.state['hands']], [7, 7, 7, 7])
        room.refresh_from_db()
        self.assertEqual(room.phase, Room.Phase.PLAYING)

    def test_dealing_twice_does_not_start_a_second_round(self):
        room = service.make_room(self.player)
        service.fill(room, 3)
        first, _ = service.start(room, rng=Steady(2))
        again, _ = service.start(room, rng=Steady(3))
        self.assertEqual(first.pk, again.pk)
        self.assertEqual(room.games.count(), 1)


class FillingSeatsTests(TestCase):

    def setUp(self):
        self.player = player()

    def test_an_empty_chair_gets_an_ordinary_name(self):
        room = service.make_room(self.player)
        added = service.fill(room, 4)
        self.assertEqual(len(added), 3)
        for seat in added:
            self.assertIn(seat.name, service.NAMES)
            self.assertIsNone(seat.player_id)

    def test_no_table_ever_has_two_of_the_same_name(self):
        room = service.make_room(self.player)
        service.fill(room, service.MAX_SEATS)
        names = [s.name for s in room.seats.all()]
        self.assertEqual(len(names), len(set(names)))

    def test_a_real_member_never_has_a_double_at_the_table(self):
        """A second Anitha is worse than no illusion: somebody would think the real one was
        there and ignoring them.

        The pool is cut to one name on purpose. Left at thirty, the shuffle picks the clashing
        one about a sixth of the time, and a test that only sometimes notices is not a test.
        """
        member('Anitha', 'anitha@example.com')
        room = service.make_room(self.player)
        with mock.patch.object(service, 'NAMES', ('Anitha',)):
            added = service.fill(room, 4)
        self.assertEqual(added, [], 'the only name left was a real person')
        self.assertNotIn('Anitha', [s.name for s in room.seats.all()])

    def test_the_name_of_somebody_who_plays_is_never_reused_either(self):
        player('Vikram', 'vikram@example.com')
        room = service.make_room(self.player)
        with mock.patch.object(service, 'NAMES', ('Vikram',)):
            added = service.fill(room, 4)
        self.assertEqual(added, [])

    def test_a_name_nobody_is_using_is_fine(self):
        """The other half of it: the check refuses a clash, not every name."""
        room = service.make_room(self.player)
        with mock.patch.object(service, 'NAMES', ('Anitha',)):
            added = service.fill(room, 2)
        self.assertEqual([s.name for s in added], ['Anitha'])


class NothingSaysWhichTests(TestCase):
    """The rule, held by a test instead of by good intentions."""

    # "auto" is deliberately not here: ml-auto and autocomplete are ordinary HTML and a test
    # that cries wolf gets deleted. These are the words that would actually give it away.
    WORDS = ('house', 'is_house', 'player_id', 'computer', 'cpu', 'bot', 'robot', 'npc', 'ai')

    def setUp(self):
        self.player = player()
        sign_in(self.client)
        self.room = service.make_room(self.player)
        service.fill(self.room, 4)
        self.game, _ = service.start(self.room, rng=Steady(5))

    def test_the_payload_carries_no_such_field(self):
        seat = service.seat_of(self.room, self.player)
        flat = json.dumps(service.table(self.room, self.game, seat)).lower()
        for word in self.WORDS:
            self.assertIsNone(re.search(r'\b' + word + r'\b', flat), word)

    def test_a_seat_is_a_name_and_a_score_and_nothing_else(self):
        seat = service.seat_of(self.room, self.player)
        rows = service.table(self.room, self.game, seat)['seats']
        for row in rows:
            self.assertEqual(set(row), {'position', 'name', 'score'})

    def test_the_polled_page_says_nothing_either(self):
        reply = self.client.get(reverse('games_poll', args=[self.room.code]))
        self.assertEqual(reply.status_code, 200)
        flat = reply.content.decode().lower()
        for word in self.WORDS:
            self.assertIsNone(re.search(r'\b' + word + r'\b', flat), word)

    def test_the_table_page_says_nothing_either(self):
        page = self.client.get(reverse('games_room', args=[self.room.code])).content.decode()
        flat = page.lower()
        for word in self.WORDS:
            self.assertIsNone(re.search(r'\b' + word + r'\b', flat), word)

    def test_no_file_a_browser_is_given_contains_the_vocabulary(self):
        """Templates and static files, read off disk. A word that is not in the repo cannot
        leak into a page by accident later."""
        roots = [Path(django_settings.BASE_DIR) / 'games' / 'templates' / 'games' / 'user',
                 Path(django_settings.BASE_DIR) / 'static' / 'games']
        checked = 0
        for root in roots:
            for path in root.rglob('*'):
                if not path.is_file():
                    continue
                checked += 1
                text = path.read_text(encoding='utf-8').lower()
                for word in ('bot', 'robot', 'computer', 'cpu', 'is_house'):
                    self.assertIsNone(re.search(r'\b' + word + r'\b', text),
                                      f'{path.name} says "{word}"')
        self.assertGreater(checked, 4, 'nothing was actually read')

    def test_the_console_on_the_other_hand_says_so_plainly(self):
        """It is behind the admin sign-in and it is for three of us. A table that will not
        move has to be explicable when you are looking at it."""
        User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        console = Client()
        console.force_login(User.objects.get(username='admin'))
        page = console.get(reverse('games_room_admin', args=[self.room.code])).content.decode()
        self.assertIn('this server', page)


class WhatOnePlayerSeesTests(TestCase):

    def setUp(self):
        self.player = player()
        self.other = player('Meena', 'meena@example.com')
        self.room = service.make_room(self.player)
        service.join(self.room, self.other)
        self.game, _ = service.start(self.room, rng=Steady(7))

    def test_you_get_your_own_hand(self):
        seat = service.seat_of(self.room, self.player)
        seen = service.table(self.room, self.game, seat)
        self.assertEqual(sorted(seen['game']['hand']), sorted(self.game.state['hands'][0]))

    def test_you_never_get_anybody_elses(self):
        seat = service.seat_of(self.room, self.player)
        seen = json.dumps(service.table(self.room, self.game, seat))
        theirs = self.game.state['hands'][1]
        mine = set(self.game.state['hands'][0])
        for card in theirs:
            if card in mine:
                continue
            self.assertNotIn(f',{card},', seen.replace('[', ',').replace(']', ','),
                             f'card {card} leaked')

    def test_you_never_get_the_order_of_the_pile(self):
        seat = service.seat_of(self.room, self.player)
        seen = service.table(self.room, self.game, seat)
        self.assertNotIn('draw', seen['game'])
        self.assertEqual(seen['game']['draw_count'], len(self.game.state['draw']))

    def test_a_stranger_who_is_not_sitting_down_sees_no_hand_at_all(self):
        seen = service.table(self.room, self.game, None)
        self.assertEqual(seen['game']['hand'], [])
        self.assertIsNone(seen['you'])


class MovingTests(TestCase):

    def setUp(self):
        self.player = player()
        sign_in(self.client)
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        self.game, _ = service.start(self.room, rng=Steady(11))
        self.url = reverse('games_move', args=[self.room.code])

    def _mine(self):
        self.game.refresh_from_db()
        return self.game.state['hands'][0]

    def _make_my_turn(self):
        state = dict(self.game.state)
        state['turn'] = 0
        state['need_colour'] = False
        state['think_until'] = None
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()

    def test_a_legal_card_is_played(self):
        self._make_my_turn()
        playable = rules.legal(self.game.state, 0, self.room.options)
        if not playable:                       # the deal did not hand us one; make it so
            state = dict(self.game.state)
            state['colour'] = deck.colour_of(state['hands'][0][0]) or 'red'
            state['discard'] = [state['hands'][0][0]]
            Game.objects.filter(pk=self.game.pk).update(state=state)
            self.game.refresh_from_db()
            playable = rules.legal(self.game.state, 0, self.room.options)
        card = playable[0]
        reply = self.client.post(self.url, json.dumps({'what': 'play', 'card': card}),
                                 content_type='application/json')
        self.assertEqual(reply.status_code, 200)
        self.assertNotIn('error', reply.json())
        self.assertNotIn(card, self._mine())

    def test_an_illegal_card_changes_nothing_and_says_why(self):
        self._make_my_turn()
        before = list(self._mine())
        bad = [c for c in before if c not in rules.legal(self.game.state, 0, self.room.options)]
        if not bad:
            self.skipTest('every card was playable in this deal')
        reply = self.client.post(self.url, json.dumps({'what': 'play', 'card': bad[0]}),
                                 content_type='application/json')
        self.assertIn('error', reply.json())
        self.assertEqual(self._mine(), before)

    def test_a_card_you_do_not_hold_is_refused(self):
        self._make_my_turn()
        mine = set(self._mine())
        outside = next(c for c in range(deck.SIZE) if c not in mine)
        reply = self.client.post(self.url, json.dumps({'what': 'play', 'card': outside}),
                                 content_type='application/json')
        self.assertIn('error', reply.json())

    def test_rubbish_where_a_card_should_be_is_refused(self):
        reply = self.client.post(self.url, json.dumps({'what': 'play', 'card': 'the red one'}),
                                 content_type='application/json')
        self.assertEqual(reply.status_code, 400)

    def test_somebody_elses_table_is_not_yours_to_move_on(self):
        other = Client()
        member('Meena', 'meena@example.com')
        sign_in(other, 'meena@example.com')
        other.post(reverse('games_join'), {'name': 'Meena'})
        reply = other.post(self.url, json.dumps({'what': 'draw'}),
                           content_type='application/json')
        self.assertEqual(reply.status_code, 404)

    def test_a_move_worked_out_from_a_table_that_has_moved_on_is_refused(self):
        """SQLite has no row locks, so the seq the move was worked out from is the lock. This
        is the double tap, the retried request and two players racing, all at once."""
        self._make_my_turn()
        state = dict(self.game.state)
        stale = Game.objects.get(pk=self.game.pk)
        # Somebody else's move lands first.
        Game.objects.filter(pk=self.game.pk, seq=stale.seq).update(seq=stale.seq + 1)
        game, error = service.act(stale, 0, 'draw', {}, timezone.now())
        self.assertIsNotNone(error)

    def test_every_move_a_person_makes_is_written_down(self):
        self._make_my_turn()
        self.client.post(self.url, json.dumps({'what': 'draw'}),
                         content_type='application/json')
        self.assertTrue(Move.objects.filter(game=self.game, kind='draw').exists())


class PollingTests(TestCase):

    def setUp(self):
        self.player = player()
        sign_in(self.client)
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        service.start(self.room, rng=Steady(13))
        self.url = reverse('games_poll', args=[self.room.code])

    def test_a_table_where_nothing_moved_costs_a_request_with_no_body(self):
        first = self.client.get(self.url)
        self.assertEqual(first.status_code, 200)
        again = self.client.get(self.url, HTTP_IF_NONE_MATCH=first['ETag'])
        self.assertEqual(again.status_code, 304)
        self.assertEqual(again.content, b'')

    def test_signing_in_is_needed(self):
        self.assertEqual(Client().get(self.url).status_code, 302)

    def test_the_page_ships_with_the_table_already_on_it(self):
        """No flash of an empty table: the first paint comes from the page, not a fetch."""
        page = self.client.get(reverse('games_room', args=[self.room.code])).content.decode()
        self.assertIn('data-boot', page)
        self.assertIn('data-poll', page)


class TheServerPlayingTests(TestCase):
    """The chairs this server holds, driven through the service rather than the engine."""

    def setUp(self):
        self.player = player()
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        self.game, _ = service.start(self.room, rng=Steady(17))

    def test_nothing_moves_until_somebody_looks(self):
        """There is no scheduler on this host, and none is wanted."""
        before = Game.objects.get(pk=self.game.pk).seq
        self.assertEqual(Game.objects.get(pk=self.game.pk).seq, before)

    def test_looking_later_plays_the_turns_that_were_waiting(self):
        state = dict(self.game.state)
        state['turn'] = 1                               # a chair the server holds
        state['think_until'] = rules._at(timezone.now() - timedelta(seconds=1))
        state['need_colour'] = False
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        game = service.catch_up(self.game, timezone.now(), Steady(19))
        self.assertGreater(game.seq, 0)

    def test_a_round_between_three_of_them_plays_itself_out(self):
        now = timezone.now()
        game = self.game
        for step in range(400):
            game = service.catch_up(game, now + timedelta(seconds=step * 30), Steady(step))
            game.refresh_from_db()
            if game.state['phase'] != 'playing':
                break
        self.assertEqual(game.state['phase'], 'over')
        self.assertIsNotNone(game.finished_at)
        everywhere = ([c for hand in game.state['hands'] for c in hand]
                      + game.state['draw'] + game.state['discard'])
        self.assertEqual(sorted(everywhere), deck.fresh(), 'a card went missing')

    def test_the_score_is_kept_on_the_seat_when_a_round_ends(self):
        now = timezone.now()
        game = self.game
        for step in range(400):
            game = service.catch_up(game, now + timedelta(seconds=step * 30), Steady(step))
            game.refresh_from_db()
            if game.state['phase'] != 'playing':
                break
        winner = game.state['winner']
        seat = self.room.seats.get(position=winner)
        self.assertGreaterEqual(seat.score, 0)
        self.assertEqual(sum(s.score for s in self.room.seats.all()),
                         rules.round_points(game.state))

    def test_somebody_who_stops_answering_has_their_chair_played_for_them(self):
        """It keeps the name, the hand and the score, so from the other chairs nothing
        happened at all."""
        state = dict(self.game.state)
        state['turn'] = 0
        state['misses'] = [rules.MISSES_BEFORE_TAKEOVER, 0, 0]
        state['turn_until'] = rules._at(timezone.now() - timedelta(seconds=1))
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        name_before = self.room.seats.get(position=0).name
        service.catch_up(self.game, timezone.now(), Steady(23))
        seat = self.room.seats.get(position=0)
        self.assertTrue(seat.is_held)
        self.assertEqual(seat.name, name_before)
        # Still their chair. That is the only reason they can walk back into it.
        self.assertEqual(seat.player_id, self.player.pk)

    def test_and_they_take_it_back_by_opening_the_table_again(self):
        state = dict(self.game.state)
        state['turn'] = 0
        state['misses'] = [rules.MISSES_BEFORE_TAKEOVER, 0, 0]
        state['turn_until'] = rules._at(timezone.now() - timedelta(seconds=1))
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        service.catch_up(self.game, timezone.now(), Steady(23))
        self.assertTrue(self.room.seats.get(position=0).is_held)

        self.assertTrue(service.back(self.room, self.player))
        seat = self.room.seats.get(position=0)
        self.assertFalse(seat.is_held)
        self.game.refresh_from_db()
        self.assertEqual(self.game.state['misses'][0], 0, 'they start with a clean slate')

    def test_coming_back_when_you_never_left_does_nothing(self):
        self.assertFalse(service.back(self.room, self.player))

    def test_nothing_at_the_table_says_either_thing_happened(self):
        """Leaving and coming back are both invisible from the other chairs."""
        state = dict(self.game.state)
        state['turn'] = 0
        state['misses'] = [rules.MISSES_BEFORE_TAKEOVER, 0, 0]
        state['turn_until'] = rules._at(timezone.now() - timedelta(seconds=1))
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        service.catch_up(self.game, timezone.now(), Steady(23))
        service.back(self.room, self.player)
        self.game.refresh_from_db()
        said = ' '.join(self.game.state['log']).lower()
        for word in ('away', 'left', 'took over', 'taken', 'back', 'returned'):
            self.assertNotIn(word, said, word)

    def test_catching_up_is_bounded(self):
        """It runs inside somebody's poll. A loop that ends only if the rules are right is a
        loop that can hang a web worker."""
        game = service.catch_up(self.game, timezone.now() + timedelta(days=30), Steady(29),
                                rounds=3)
        self.assertLessEqual(game.seq, 4)


class WhenARoundEndsTests(TestCase):
    """What happens between rounds — which was two bugs, both only visible end to end."""

    def setUp(self):
        self.player = player()
        sign_in(self.client)
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        self.game, _ = service.start(self.room, rng=Steady(3))
        self._play_it_out()

    def _play_it_out(self):
        now = timezone.now()
        game = self.game
        for step in range(500):
            game = service.catch_up(game, now + timedelta(seconds=step * 40), Steady(step))
            game.refresh_from_db()
            if game.state['phase'] != 'playing':
                break
        self.game = game
        self.room.refresh_from_db()
        self.assertEqual(game.state['phase'], 'over', 'the round never ended')

    def test_the_finished_round_is_still_what_the_table_is_handed(self):
        """Filtering on finished_at alone handed the page nothing, so it fell back to the
        lobby and the winner was never announced."""
        seat = service.seat_of(self.room, self.player)
        payload = service.table(self.room, service.current_game(self.room), seat)
        self.assertIn('game', payload)
        self.assertEqual(payload['game']['phase'], 'over')
        self.assertIsNotNone(payload['game']['winner'])

    def test_the_poll_says_so_too(self):
        reply = self.client.get(reverse('games_poll', args=[self.room.code]))
        self.assertEqual(reply.json()['game']['phase'], 'over')

    def test_a_table_with_no_target_goes_back_to_waiting(self):
        """There is nothing to win, so there is nothing to close. The score carries."""
        self.assertEqual(self.room.phase, Room.Phase.LOBBY)

    def test_play_again_deals_another_round_into_the_same_table(self):
        reply = self.client.post(reverse('games_seat', args=[self.room.code]),
                                 {'what': 'again'})
        self.assertEqual(reply.status_code, 302)
        self.room.refresh_from_db()
        self.assertEqual(self.room.games.count(), 2)
        self.assertEqual(self.room.phase, Room.Phase.PLAYING)

    def test_the_score_carries_between_rounds(self):
        before = sum(s.score for s in self.room.seats.all())
        self.assertGreater(before, 0)
        self.client.post(reverse('games_seat', args=[self.room.code]), {'what': 'again'})
        self.assertEqual(sum(s.score for s in self.room.seats.all()), before)

    def test_only_the_host_deals_again(self):
        other = Client()
        member('Meena', 'meena@example.com')
        sign_in(other, 'meena@example.com')
        other.post(reverse('games_join'), {'name': 'Meena'})
        other.post(reverse('games_seat', args=[self.room.code]), {'what': 'again'})
        self.assertEqual(self.room.games.count(), 1)

    def test_a_table_that_has_been_won_closes(self):
        Seat.objects.filter(room=self.room, position=0).update(score=600)
        Room.objects.filter(pk=self.room.pk).update(target=500)
        room = Room.objects.get(pk=self.room.pk)
        game, _ = service.start(room, rng=Steady(11))
        state = dict(game.state)
        state['phase'], state['winner'] = 'over', 0
        service._commit(game, state, game.seq, timezone.now())
        game.refresh_from_db()
        service._finish(game, timezone.now())
        room.refresh_from_db()
        self.assertEqual(room.phase, Room.Phase.CLOSED)

    def test_a_closed_table_is_not_dealt_again(self):
        Room.objects.filter(pk=self.room.pk).update(phase=Room.Phase.CLOSED)
        room = Room.objects.get(pk=self.room.pk)
        _, error = service.start(room)
        self.assertEqual(error, 'That table has finished.')

    def test_nobody_leaves_once_a_round_has_been_played(self):
        """The chairs are numbered and a finished round refers to them by number."""
        other = player('Meena', 'meena@example.com')
        service.join(self.room, other)
        self.assertFalse(service.leave(self.room, other))

    def test_but_leaving_before_the_first_deal_is_fine(self):
        fresh = service.make_room(self.player)
        other = player('Ravi', 'ravi@example.com')
        service.join(fresh, other)
        self.assertTrue(service.leave(fresh, other))
        self.assertEqual(fresh.seats.count(), 1)


class TheTableFitsAndHidesTests(TestCase):
    """Two rules about the table that are CSS, and so have nothing else watching them."""

    def css(self):
        return (Path(django_settings.BASE_DIR) / 'static' / 'games' / 'user.css').read_text(
            encoding='utf-8')

    def test_the_hidden_attribute_is_made_to_work(self):
        """It shipped broken and somebody photographed it: the lobby sitting underneath the
        live table, both on screen at once.

        `hidden` hides by a UA stylesheet rule, and ANY author rule beats it — these divs
        carry class="stack", which is display:flex. The script set .hidden = true and nothing
        happened. One override fixes it for every element, now and later, which is why it is
        worth a test that will fail if somebody tidies it away.
        """
        css = self.css()
        self.assertRegex(css, r'\[hidden\]\s*\{[^}]*display:\s*none\s*!important')

    def test_the_divs_that_get_hidden_really_do_carry_such_a_class(self):
        """The other half: without this, the test above looks like superstition."""
        page = (Path(django_settings.BASE_DIR) / 'games' / 'templates' / 'games' / 'user'
                / 'room.html').read_text(encoding='utf-8')
        for which in ('id="lobby"', 'id="over"'):
            line = next(l for l in page.splitlines() if which in l)
            self.assertIn('class="stack"', line, which)
        self.assertRegex(self.css(), r'\.stack\s*\{[^}]*display:\s*flex')

    def test_the_table_page_is_the_viewport_rather_than_a_document(self):
        """Your hand, the pile and whose turn it is all have to be true at once. Hunting for
        the fan under your thumb while a clock runs is the worst thing a phone can do to a
        card game."""
        player_row = player()
        sign_in(self.client)
        room = service.make_room(player_row)
        page = self.client.get(reverse('games_room', args=[room.code])).content.decode()
        self.assertIn('class="fit"', page)
        css = self.css()
        self.assertRegex(css, r'body\.fit\s*\{[^}]*height:\s*100dvh')
        self.assertRegex(css, r'body\.fit\s*\{[^}]*overflow:\s*hidden')

    def test_and_no_other_page_is(self):
        """Everything else is a document and should scroll like one."""
        player()
        sign_in(self.client)
        for name in ('games_home', 'games_settings'):
            page = self.client.get(reverse(name)).content.decode()
            self.assertNotIn('class="fit"', page, name)


class EndingAndLeavingTests(TestCase):
    """Getting out of a table — which until now only an admin could do."""

    def setUp(self):
        self.player = player()
        sign_in(self.client)
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        self.game, _ = service.start(self.room, rng=Steady(3))
        self.url = reverse('games_seat', args=[self.room.code])

    def test_the_host_can_end_a_table_in_play(self):
        self.client.post(self.url, {'what': 'end'})
        self.room.refresh_from_db()
        self.game.refresh_from_db()
        self.assertEqual(self.room.phase, Room.Phase.CLOSED)
        self.assertIsNotNone(self.game.finished_at)

    def test_nobody_else_can(self):
        other = Client()
        member('Meena', 'meena@example.com')
        sign_in(other, 'meena@example.com')
        other.post(reverse('games_join'), {'name': 'Meena'})
        guest_player = Player.objects.get(name='Meena')
        service.join(self.room, guest_player) if self.room.is_open else None
        other.post(self.url, {'what': 'end'})
        self.room.refresh_from_db()
        self.assertEqual(self.room.phase, Room.Phase.PLAYING)

    def test_stepping_away_mid_round_hands_the_chair_over_rather_than_pulling_it(self):
        """Chairs are numbered and a round refers to them by number, so the seat stays — it is
        played for them, exactly as if they had gone quiet, and is theirs again when they
        come back."""
        self.client.post(self.url, {'what': 'leave'})
        seat = self.room.seats.get(position=0)
        self.assertTrue(seat.away)
        self.assertEqual(seat.player_id, self.player.pk)
        self.assertEqual(self.room.seats.count(), 3)

    def test_and_coming_back_takes_it_again(self):
        self.client.post(self.url, {'what': 'leave'})
        self.client.get(reverse('games_room', args=[self.room.code]))
        self.assertFalse(self.room.seats.get(position=0).away)

    def test_before_the_first_deal_leaving_really_leaves(self):
        fresh = service.make_room(self.player)
        other = player('Ravi', 'ravi@example.com')
        service.join(fresh, other)
        self.assertTrue(service.leave(fresh, other))
        self.assertEqual(fresh.seats.count(), 1)

    def test_the_host_can_change_the_rules_between_rounds(self):
        """Which is how a table made under the old defaults stops being hard work."""
        service.close(self.room)
        Room.objects.filter(pk=self.room.pk).update(phase=Room.Phase.LOBBY)
        self.room.refresh_from_db()
        self.client.post(self.url, {'what': 'rules', 'draw_until': 'on', 'target': '0'})
        self.room.refresh_from_db()
        self.assertTrue(self.room.options['draw_until'])
        self.assertFalse(self.room.options['stacking'])

    def test_but_never_in_the_middle_of_one(self):
        """The engine reads them on every move; swapping them halfway through would change
        what is legal under somebody's hand."""
        before = dict(self.room.options)
        self.client.post(self.url, {'what': 'rules', 'stacking': 'on'})
        self.room.refresh_from_db()
        self.assertEqual(self.room.options, before)

    def test_a_new_table_draws_until_you_can_play(self):
        """The default that makes the pile bearable. Tables made before it keep their own."""
        made = service.make_room(self.player)
        self.assertTrue(made.options['draw_until'])


class JoiningGamesTests(TestCase):

    def test_the_first_visit_asks_for_one_name_and_nothing_else(self):
        member()
        sign_in(self.client)
        page = self.client.get(reverse('games_join')).content.decode()
        self.assertIn('name', page)
        self.assertNotIn('password', page.lower())

    def test_starting_makes_the_player_row(self):
        member()
        sign_in(self.client)
        self.client.post(reverse('games_join'), {'name': 'Anand'})
        self.assertTrue(Player.objects.filter(name='Anand').exists())

    def test_the_tables_page_needs_a_player_row_first(self):
        member()
        sign_in(self.client)
        reply = self.client.get(reverse('games_home'))
        self.assertEqual(reply.status_code, 302)
        self.assertIn(reverse('games_join'), reply['Location'])

    def test_a_stranger_is_offered_a_way_in_rather_than_a_sign_in_page(self):
        """Bouncing somebody to the hub's sign-in was the old behaviour. A table of two is not
        a game, and the people most likely to make up the numbers are the ones who will never
        sign up for anything — so they get the choice instead."""
        reply = Client().get(reverse('games_home'))
        self.assertEqual(reply.status_code, 302)
        self.assertIn(reverse('games_join'), reply['Location'])

    def test_and_that_page_offers_both_doors(self):
        page = Client().get(reverse('games_join')).content.decode()
        self.assertIn('Play as a guest', page)
        self.assertIn(reverse('member_login'), page)

class NothingMatchesTests(TestCase):
    """When no card in your hand matches, the table takes one for you.

    Drawing is not a decision. The rules make you take one either way, and with draw-until
    on they make you keep taking until you can play — so a tap that only confirms there was
    nothing to decide is a tap worth removing. What IS a decision is left strictly alone,
    and most of the tests below are about that half.
    """

    def setUp(self):
        self.player = player()
        self.room = service.make_room(self.player)
        service.fill(self.room, 2)
        self.game, _ = service.start(self.room, rng=Steady(5))

    def _set(self, **changes):
        state = dict(self.game.state)
        state.update(changes)
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        return state

    def _stuck(self):
        """Seat 0's turn, holding a hand with nothing in it that matches."""
        state = dict(self.game.state)
        # A red 5 is on top, and the hand is seven blue numbers: nothing matches by colour
        # and nothing by value, which is the position this whole feature is about.
        red5 = deck.CARDS.index(('red', '5'))
        blues = [i for i, c in enumerate(deck.CARDS) if c[0] == 'blue' and c[1] == '1'][:1]
        blues += [i for i, c in enumerate(deck.CARDS) if c[0] == 'blue' and c[1] == '2'][:1]
        blues += [i for i, c in enumerate(deck.CARDS) if c[0] == 'blue' and c[1] == '3'][:1]
        state['turn'] = 0
        state['hands'] = [list(blues)] + [list(h) for h in state['hands'][1:]]
        state['discard'] = [red5]
        state['colour'] = 'red'
        state['drew'] = None
        state['need_colour'] = False
        state['need_swap'] = False
        state['pending'] = {'n': 0, 'kind': None}
        state['think_until'] = None
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        return blues

    def test_the_table_draws_without_being_asked(self):
        held = self._stuck()
        self.assertEqual(rules.legal(self.game.state, 0, self.room.options), [])
        game = service.catch_up(self.game, rng=Steady(2))
        self.assertGreater(len(game.state['hands'][0]), len(held),
                           'nobody tapped anything and no card arrived')

    def test_it_keeps_drawing_until_one_of_them_matches(self):
        """Draw-until is on by default, so the hand it leaves you with is a playable one —
        or the turn has already passed on, which the rules do when the pile runs dry."""
        self._stuck()
        game = service.catch_up(self.game, rng=Steady(3))
        state = game.state
        if state['turn'] == 0:
            self.assertTrue(rules.legal(state, 0, self.room.options),
                            'left holding a hand it still cannot play out of')

    def test_a_hand_that_can_be_played_is_left_alone(self):
        """The commonest case, and the one it must never touch."""
        self._stuck()
        state = dict(self.game.state)
        state['colour'] = 'blue'                  # now every card in that hand matches
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        before = list(self.game.state['hands'][0])
        game = service.catch_up(self.game, rng=Steady(4))
        self.assertEqual(game.state['hands'][0], before)
        self.assertEqual(game.state['turn'], 0, 'and the turn is still theirs')

    def test_a_colour_still_to_be_named_is_a_decision_and_is_left_alone(self):
        self._stuck()
        self._set(need_colour=True)
        before = list(self.game.state['hands'][0])
        game = service.catch_up(self.game, rng=Steady(6))
        self.assertEqual(game.state['hands'][0], before)
        self.assertTrue(game.state['need_colour'])

    def test_a_hand_still_to_be_picked_is_a_decision_and_is_left_alone(self):
        self._stuck()
        self._set(need_swap=True)
        before = list(self.game.state['hands'][0])
        game = service.catch_up(self.game, rng=Steady(7))
        self.assertEqual(game.state['hands'][0], before)
        self.assertTrue(game.state['need_swap'])

    def test_a_card_already_drawn_is_a_decision_and_is_left_alone(self):
        """Having drawn, you choose to play it or keep it. That is a real choice, and it is
        the one the old `drew` flag exists to protect."""
        held = self._stuck()
        self._set(drew=held[0])
        before = list(self.game.state['hands'][0])
        game = service.catch_up(self.game, rng=Steady(8))
        self.assertEqual(game.state['hands'][0], before)
        self.assertEqual(game.state['drew'], held[0])

    def test_a_stack_you_could_answer_is_a_decision_and_is_left_alone(self):
        """Two twos on the table and a two in your hand: taking four or passing it on is
        the most interesting choice in the game, and nothing may take it from you."""
        state = dict(self.game.state)
        red2 = deck.CARDS.index(('red', 'draw2'))
        other = [i for i, c in enumerate(deck.CARDS) if c == ('blue', 'draw2')][0]
        state['turn'] = 0
        state['hands'] = [[red2]] + [list(h) for h in state['hands'][1:]]
        state['discard'] = [other]
        state['colour'] = 'blue'
        state['pending'] = {'n': 2, 'kind': 'draw2'}
        state['drew'] = None
        state['need_colour'] = False
        state['need_swap'] = False
        state['think_until'] = None
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        if not rules.legal(self.game.state, 0, self.room.options):
            self.skipTest('stacking is off at this table, so there is no choice to protect')
        game = service.catch_up(self.game, rng=Steady(9))
        self.assertEqual(game.state['hands'][0], [red2], 'the choice was taken away')
        self.assertEqual(game.state['pending']['n'], 2)

    def test_a_stack_you_cannot_answer_is_taken_for_you(self):
        """No two in hand: taking the four is forced, so there is nothing to tap about."""
        state = dict(self.game.state)
        blue1 = [i for i, c in enumerate(deck.CARDS) if c == ('blue', '1')][0]
        other = [i for i, c in enumerate(deck.CARDS) if c == ('red', 'draw2')][0]
        state['turn'] = 0
        state['hands'] = [[blue1]] + [list(h) for h in state['hands'][1:]]
        state['discard'] = [other]
        state['colour'] = 'red'
        state['pending'] = {'n': 2, 'kind': 'draw2'}
        state['drew'] = None
        state['need_colour'] = False
        state['need_swap'] = False
        state['think_until'] = None
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        game = service.catch_up(self.game, rng=Steady(10))
        self.assertGreater(len(game.state['hands'][0]), 1, 'the stack was never taken')
        self.assertEqual(game.state['pending']['n'], 0)

    def test_the_poll_alone_is_enough_to_make_it_happen(self):
        """It has to work from a plain poll, because that is all a waiting player does."""
        self._stuck()
        sign_in(self.client)
        before = len(self.game.state['hands'][0])
        self.client.get(reverse('games_poll', args=[self.room.code]))
        self.game.refresh_from_db()
        self.assertGreater(len(self.game.state['hands'][0]), before)

class TheDeckIsPeopleTests(TestCase):
    """Three things about the drawing that nothing else is watching.

    None of them is about how it looks — no test can be. They are the three that fail
    silently: a figure painted black because the light it refers to is not in the page, a
    line of people wider than the phone they are on, and the word nobody at this table is
    ever called.
    """

    def css(self):
        return (Path(django_settings.BASE_DIR) / 'static' / 'games' / 'user.css').read_text(
            encoding='utf-8')

    def js(self):
        return (Path(django_settings.BASE_DIR) / 'static' / 'games' / 'user.js').read_text(
            encoding='utf-8')

    def room_template(self):
        return (Path(django_settings.BASE_DIR) / 'games' / 'templates' / 'games' / 'user'
                / 'room.html').read_text(encoding='utf-8')

    def test_every_light_the_script_paints_with_is_in_the_page(self):
        """The moulding is a handful of gradients defined once in the template and pointed
        at from the script. Delete one and every figure that used it goes flat black — on
        the table, in your hand, and on the round-over screen at once. Nothing throws."""
        js = self.js()
        wanted = set(re.findall(r'url\(#([a-z0-9-]+)\)', js))
        # The five kit gradients are never written out whole — they are built as
        # "url(#k-" + key + ")" from the colour, so a regex looking for a literal finds
        # four of the nine and misses every one that matters. The keys come from the map
        # the script builds them with, plus the grey the ones still waiting wear.
        keys = set(re.findall(r':\s*"([a-z])"', js[js.index('var KEY'):js.index('var FLAT')]))
        self.assertEqual(keys, set('rygb'), 'the four colours are not where they were')
        wanted |= {'k-' + k for k in keys | {'n'}}
        self.assertGreaterEqual(len(wanted), 9)
        defined = set(re.findall(r'<radialGradient id="([a-z0-9-]+)"', self.room_template()))
        self.assertEqual(wanted - defined, set())

    def test_a_line_of_people_cannot_push_the_page_wider_than_the_phone(self):
        """Seven of them side by side are wider than a phone, and a flex item will not
        shrink below its own contents — so without this the hand pushed the whole page out
        to 436 pixels on a 390 pixel screen. Everything went off-centre, the buttons ran
        off the right-hand edge, and the measuring that is supposed to close the line up
        then measured the pushed-out width, decided it had room, and left them spread. The
        overflow hid the overflow, which is why it needs a test and not a memory."""
        css = self.css()
        self.assertRegex(css, r'\.line[^{]*\{[^}]*min-width:\s*0')
        # And the floor under it: they overlap before a line of script has run.
        self.assertRegex(css, r'\.line \.who \+ \.who\s*\{[^}]*margin-left:\s*-\d')

    def test_each_of_them_is_named_in_words_for_a_screen_reader(self):
        """The value is drawn, not written, so there is nothing to read unless it is said.
        A colour swatch is not a label at the best of times, and about one man in twelve
        cannot separate two of these four."""
        self.assertIn('aria-label', self.js())
        self.assertIn('name(index)', self.js())

    def test_nothing_is_drawn_as_a_card_any_more(self):
        """The old card markup is gone from all three files, not just stopped being used."""
        for what, text in (('the stylesheet', self.css()), ('the script', self.js()),
                           ('the template', self.room_template())):
            self.assertNotIn('class="uc"', text, what)
            self.assertNotIn('.uc ', text, what)

def _srgb(channel):
    channel /= 255
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _luminance(rgb):
    return 0.2126 * _srgb(rgb[0]) + 0.7152 * _srgb(rgb[1]) + 0.0722 * _srgb(rgb[2])


def _rgb(text):
    text = text.lstrip('#')
    if len(text) == 3:
        text = ''.join(c * 2 for c in text)
    return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))


def contrast(one, other):
    """The WCAG ratio between two colours, 1:1 to 21:1."""
    a, b = _luminance(_rgb(one)), _luminance(_rgb(other))
    high, low = max(a, b), min(a, b)
    return (high + 0.05) / (low + 0.05)


def _at(stops, want):
    """What a gradient resolves to at `want` along its length."""
    previous = stops[0]
    for offset, colour in stops:
        if offset >= want:
            last_offset, last_colour = previous
            if offset == last_offset:
                return '#%02x%02x%02x' % _rgb(colour)
            share = (want - last_offset) / (offset - last_offset)
            a, b = _rgb(last_colour), _rgb(colour)
            return '#%02x%02x%02x' % tuple(
                round(a[i] + share * (b[i] - a[i])) for i in range(3))
        previous = (offset, colour)
    return '#%02x%02x%02x' % _rgb(stops[-1][1])


class ItCanBeReadTests(TestCase):
    """The value on a figure's chest is the only thing on this table that MUST be read.

    It is printed on a gradient, which makes the usual way of checking this useless in both
    directions: measured against the lightest stop everything fails, and against the mid
    colour anybody would quote everything flatters itself. So this resolves the gradient AT
    THE POINT THE GLYPH SITS and measures against that.

    Doing it found that white on green was 2.6:1 — under even the 3:1 that large text needs
    — on the one table where colour is deliberately never the only signal. The fix was to
    bring each mid stop forward and down, which darkens the middle of the body without
    touching the highlight at the top-left, where there is no type.

    The glyph also carries a dark outline, which helps a real eye and is not counted here.
    """

    # Where the glyph is, taken from the drawing rather than guessed: the torso path's
    # bounds, and the text baseline the script sets.
    BODY = (14.4, 41.0, 49.6, 81.0)
    SIZE = 26.0

    def setUp(self):
        base = Path(django_settings.BASE_DIR)
        self.page = (base / 'games' / 'templates' / 'games' / 'user'
                     / 'room.html').read_text(encoding='utf-8')
        self.css = (base / 'static' / 'games' / 'user.css').read_text(encoding='utf-8')
        self.js = (base / 'static' / 'games' / 'user.js').read_text(encoding='utf-8')

    def gradients(self):
        found = {}
        for m in re.finditer(
                r'<radialGradient id="(k-[a-z])"([^>]*)>(.*?)</radialGradient>',
                self.page, re.S):
            head, body = m.group(2), m.group(3)
            found[m.group(1)] = (
                float(re.search(r'cx="(\d+)%', head).group(1)) / 100,
                float(re.search(r'cy="(\d+)%', head).group(1)) / 100,
                float(re.search(r'r="(\d+)%', head).group(1)) / 100,
                [(float(o or 0), c) for o, c in re.findall(
                    r'offset="([\d.]*)"\s+stop-color="(#[0-9a-fA-F]{6})"', body)])
        return found

    def behind_the_glyph(self, key):
        cx, cy, radius, stops = self.gradients()[key]
        baseline = float(re.search(r'y: "(\d+)", "text-anchor"', self.js).group(1))
        x = (32.0 - self.BODY[0]) / (self.BODY[2] - self.BODY[0])
        y = ((baseline - self.SIZE * 0.35) - self.BODY[1]) / (self.BODY[3] - self.BODY[1])
        reach = min(1.0, ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / radius)
        return _at(stops, reach)

    def test_the_value_reads_on_every_colour_it_is_printed_on(self):
        """Large bold text needs 3:1 for AA and 4.5:1 for AAA. This asks for AAA, because
        the value IS the game — a 7 you cannot read is not a card you can play."""
        ink = {'k-y': '#4a3300'}                 # yellow is the one that cannot take white
        for key, colour in (('k-r', 'red'), ('k-y', 'yellow'),
                            ('k-g', 'green'), ('k-b', 'blue')):
            with self.subTest(colour=colour):
                behind = self.behind_the_glyph(key)
                got = contrast(ink.get(key, '#ffffff'), behind)
                self.assertGreaterEqual(
                    round(got, 1), 4.5,
                    f'{colour}: the value is {got:.1f}:1 on {behind} behind the glyph')

    def test_the_ink_chosen_for_each_colour_is_the_better_of_the_two(self):
        """Not a style choice. Whichever of white or near-black reads better on the colour
        behind the glyph is the one that should be there, and yellow is the only one of the
        four where that is not white."""
        for key in ('k-r', 'k-y', 'k-g', 'k-b'):
            behind = self.behind_the_glyph(key)
            on_white, on_dark = contrast('#ffffff', behind), contrast('#4a3300', behind)
            better = '#4a3300' if on_dark > on_white else '#ffffff'
            used = '#4a3300' if key == 'k-y' else '#ffffff'
            self.assertEqual(used, better, f'{key} is printed in the worse of the two inks')

    def test_every_word_on_the_table_clears_AA(self):
        """The smallest print was 2.4:1 against the page, which is the line under the table
        code and the count under the queue — the things somebody squints at."""
        light = dict(re.findall(r'--(\w[\w-]*): (#[0-9a-fA-F]{3,6});',
                                self.css.split(':root[data-theme="dark"]')[0]))
        dark = dict(light)
        dark.update(dict(re.findall(
            r'--(\w[\w-]*): (#[0-9a-fA-F]{3,6});',
            self.css.split(':root[data-theme="dark"]')[1].split('}')[0])))
        for theme, tokens in (('light', light), ('dark', dark)):
            for name in ('ink', 'mut', 'faint', 'me', 'bad'):
                with self.subTest(theme=theme, token=name):
                    got = contrast(tokens[name], tokens['bg'])
                    self.assertGreaterEqual(
                        round(got, 1), 4.5,
                        f'{theme} --{name} is {got:.1f}:1 on --bg')
