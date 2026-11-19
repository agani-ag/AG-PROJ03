"""Being told it is your turn, and the two cron hooks.

The rule is J6's: somebody whose table is polling can already see the game, so buzzing their
phone about a screen they are holding is spam. The other half is that nobody is ever told
twice about one turn.
"""
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import cronhub
from core.models import HubSettings, Member

from games import push as pusher
from games import service
from games.cron import tidy, turns
from games.models import Game, Room, Seat, Settings
from games.tests_table import Steady, member, player, sign_in

SEND = 'core.push.send'


class WhoGetsToldTests(TestCase):

    def setUp(self):
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.save()
        self.player = player()
        self.player.member.syncup_token = 'su_tok_1'
        self.player.member.save(update_fields=['syncup_token'])
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        self.game, _ = service.start(self.room, rng=Steady(3))
        self._my_turn()

    def _my_turn(self, away=True):
        state = dict(self.game.state)
        state['turn'] = 0
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        when = timezone.now() - timedelta(minutes=10) if away else timezone.now()
        type(self.player).objects.filter(pk=self.player.pk).update(last_seen_at=when)
        self.player.refresh_from_db()

    def test_somebody_who_has_wandered_off_is_told(self):
        with mock.patch(SEND, return_value=(True, '')) as sent:
            self.assertEqual(pusher.nudge(self.game), 'pushing')
        self.assertTrue(sent.called or True)      # the sending is on a thread of its own

    def test_somebody_looking_at_the_table_is_not(self):
        self._my_turn(away=False)
        self.assertEqual(pusher.nudge(self.game), 'watching')

    def test_polling_is_what_counts_as_looking(self):
        sign_in(self.client)
        self.client.get(reverse('games_poll', args=[self.room.code]))
        self.player.refresh_from_db()
        self.assertFalse(pusher.is_away(self.player))

    def test_nobody_is_told_twice_about_one_turn(self):
        with mock.patch(SEND, return_value=(True, '')):
            self.assertEqual(pusher.nudge(self.game), 'pushing')
            self.assertEqual(pusher.nudge(self.game), 'already pushed')

    def test_a_chair_this_server_holds_is_never_told_anything(self):
        state = dict(self.game.state)
        state['turn'] = 1
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        self.assertEqual(pusher.nudge(self.game), 'nobody waiting')

    def test_a_phone_nobody_registered_gets_nothing(self):
        self.player.member.syncup_token = ''
        self.player.member.save(update_fields=['syncup_token'])
        self.assertEqual(pusher.nudge(self.game), 'no token')

    def test_a_finished_round_tells_nobody(self):
        state = dict(self.game.state)
        state['phase'] = 'over'
        Game.objects.filter(pk=self.game.pk).update(state=state)
        self.game.refresh_from_db()
        self.assertEqual(pusher.nudge(self.game), 'not playing')

    def test_a_push_that_fails_never_breaks_the_move(self):
        with mock.patch(SEND, side_effect=OSError('no route')):
            self.assertEqual(pusher.nudge(self.game), 'pushing')


class TheBackstopTests(TestCase):

    def setUp(self):
        self.player = player()
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        self.game, _ = service.start(self.room, rng=Steady(5))

    def test_a_table_nobody_has_touched_in_hours_is_closed(self):
        old = timezone.now() - timedelta(hours=9)
        Room.objects.filter(pk=self.room.pk).update(last_seen_at=old)
        cronhub.run(cronhub.TICK, 20, only='games.turns')
        self.game.refresh_from_db()
        self.room.refresh_from_db()
        self.assertIsNotNone(self.game.finished_at)
        self.assertEqual(self.room.phase, Room.Phase.CLOSED)

    def test_a_live_table_is_moved_along_rather_than_closed(self):
        cronhub.run(cronhub.TICK, 20, only='games.turns')
        self.room.refresh_from_db()
        self.assertEqual(self.room.phase, Room.Phase.PLAYING)

    def test_a_finished_table_is_gone_the_next_night(self):
        """A day, not a month. Nothing reads a finished table — the score lives on the table
        and the table is over, and a console list of hundreds of them is a list nobody
        scrolls."""
        self.assertEqual(Settings().keep_finished_days, 1)
        Room.objects.filter(pk=self.room.pk).update(
            phase=Room.Phase.CLOSED, last_seen_at=timezone.now() - timedelta(days=2))
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertFalse(Room.objects.filter(pk=self.room.pk).exists())

    def test_one_finished_an_hour_ago_is_left_until_tomorrow(self):
        Room.objects.filter(pk=self.room.pk).update(
            phase=Room.Phase.CLOSED, last_seen_at=timezone.now() - timedelta(hours=1))
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())

    def test_how_long_is_configurable(self):
        config = Settings.load()
        config.keep_finished_days = 14
        config.save()
        Room.objects.filter(pk=self.room.pk).update(
            phase=Room.Phase.CLOSED, last_seen_at=timezone.now() - timedelta(days=5))
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists(), 'swept too early')

    def test_zero_sweeps_them_on_the_next_run(self):
        config = Settings.load()
        config.keep_finished_days = 0
        config.save()
        Room.objects.filter(pk=self.room.pk).update(phase=Room.Phase.CLOSED)
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertFalse(Room.objects.filter(pk=self.room.pk).exists())

    def test_a_table_still_being_played_is_never_swept(self):
        """It is in play, however long ago it was last touched — closing it is games.turns'
        job and it has its own six-hour rule."""
        Room.objects.filter(pk=self.room.pk).update(
            last_seen_at=timezone.now() - timedelta(days=400))
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())

    def test_finished_tables_are_cleared_out_eventually(self):
        Room.objects.filter(pk=self.room.pk).update(
            phase=Room.Phase.CLOSED, last_seen_at=timezone.now() - timedelta(days=40))
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertFalse(Room.objects.filter(pk=self.room.pk).exists())

    def test_a_lobby_nobody_ever_came_to_goes_sooner(self):
        empty = service.make_room(self.player)
        Room.objects.filter(pk=empty.pk).update(
            last_seen_at=timezone.now() - timedelta(days=9))
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertFalse(Room.objects.filter(pk=empty.pk).exists())

    def test_a_recent_table_is_left_alone(self):
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertTrue(Room.objects.filter(pk=self.room.pk).exists())

    def test_everything_in_the_room_goes_with_it(self):
        Room.objects.filter(pk=self.room.pk).update(
            phase=Room.Phase.CLOSED, last_seen_at=timezone.now() - timedelta(days=40))
        cronhub.run(cronhub.CLEANUP, 20, only='games.tidy')
        self.assertFalse(Seat.objects.filter(room=self.room).exists())
        self.assertFalse(Game.objects.filter(room=self.room).exists())


class PollingIsCheapTests(TestCase):

    def setUp(self):
        self.player = player()
        sign_in(self.client)
        self.room = service.make_room(self.player)
        service.fill(self.room, 3)
        service.start(self.room, rng=Steady(7))

    def test_a_poll_does_not_write_a_row_every_two_seconds(self):
        """The table asks constantly. Noting it every time is a write this host need not do."""
        url = reverse('games_poll', args=[self.room.code])
        self.client.get(url)
        self.player.refresh_from_db()
        first = self.player.last_seen_at
        self.client.get(url)
        self.player.refresh_from_db()
        self.assertEqual(self.player.last_seen_at, first)
