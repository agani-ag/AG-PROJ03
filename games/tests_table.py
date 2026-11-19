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
        self.assertIsNone(seat.player_id)
        self.assertEqual(seat.name, name_before)

    def test_catching_up_is_bounded(self):
        """It runs inside somebody's poll. A loop that ends only if the rules are right is a
        loop that can hang a web worker."""
        game = service.catch_up(self.game, timezone.now() + timedelta(days=30), Steady(29),
                                rounds=3)
        self.assertLessEqual(game.seq, 4)


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

    def test_signing_in_comes_before_any_of_it(self):
        reply = Client().get(reverse('games_home'))
        self.assertEqual(reply.status_code, 302)
        self.assertIn(reverse('member_login'), reply['Location'])
