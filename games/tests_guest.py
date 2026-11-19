"""Guests: getting in with a name and nothing else, and going again when nobody comes back.

The deleting half is the one that has to be right. It is a hard delete with no undo and no
account to ask, so these check what goes, what stays, and that a member is never caught by it
however long they have been away.
"""
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from core import cronhub

from games import auth, service
from games.models import Game, Move, Player, Room, Seat, Settings
from games.tests_table import Steady, member, player, sign_in


def guest(client, name='Ravi'):
    client.post(reverse('games_join'), {'what': 'guest', 'name': name})
    return Player.objects.get(name=name, member__isnull=True)


class GettingInTests(TestCase):

    def test_the_way_in_offers_a_name_already_typed(self):
        page = Client().get(reverse('games_join')).content.decode()
        self.assertIn('Play as a guest', page)
        self.assertIn('name="name"', page)
        self.assertIn('value="', page)

    def test_the_suggested_name_is_one_nobody_is_using(self):
        """From the same ordinary names a chair this server plays would get, and checked the
        same way — two pools would be a tell."""
        for _ in range(25):
            self.assertIn(auth.suggest_name(), service.NAMES)
        member('Anitha', 'anitha@example.com')
        taken = service.taken_names(None)
        self.assertIn('anitha', taken)
        for _ in range(40):
            self.assertNotEqual(auth.suggest_name(), 'Anitha')

    def test_a_guest_is_a_player_with_no_member(self):
        row = guest(self.client)
        self.assertIsNone(row.member_id)
        self.assertTrue(row.is_guest)
        self.assertTrue(row.guest_key)

    def test_the_key_is_in_their_session_and_brings_them_back(self):
        row = guest(self.client)
        self.assertEqual(self.client.session[auth.GUEST_KEY], row.guest_key)
        self.assertEqual(self.client.get(reverse('games_home')).status_code, 200)

    def test_a_different_browser_is_a_different_person(self):
        guest(self.client)
        reply = Client().get(reverse('games_home'))
        self.assertEqual(reply.status_code, 302)
        self.assertIn(reverse('games_join'), reply['Location'])

    def test_a_guest_who_gives_no_name_still_gets_one(self):
        self.client.post(reverse('games_join'), {'what': 'guest', 'name': '   '})
        row = Player.objects.get(member__isnull=True)
        self.assertTrue(row.name.strip())

    def test_a_guest_makes_tables_like_anybody_else(self):
        row = guest(self.client)
        reply = self.client.post(reverse('games_new'), {'target': '0'})
        self.assertEqual(reply.status_code, 302)
        self.assertEqual(Room.objects.get().host, row)

    def test_a_guest_sits_down_at_somebody_elses_table(self):
        host = player()
        room = service.make_room(host)
        row = guest(self.client)
        self.client.post(reverse('games_enter'), {'code': room.code})
        self.assertTrue(room.seats.filter(player=row).exists())

    def test_a_guest_is_never_pushed_to(self):
        """There is no member, so there is no device token and nothing to send to."""
        self.assertEqual(guest(self.client).push_token, '')

    def test_a_guest_name_is_not_given_to_a_chair_this_server_plays(self):
        guest(self.client, 'Vikram')
        host = player()
        room = service.make_room(host)
        service.fill(room, service.MAX_SEATS)
        self.assertNotIn('Vikram', [s.name for s in room.seats.all()])

    def test_a_member_still_gets_the_member_door(self):
        member()
        sign_in(self.client)
        page = self.client.get(reverse('games_join')).content.decode()
        self.assertNotIn('Play as a guest', page)

    def test_where_you_were_going_survives_starting(self):
        host = player()
        room = service.make_room(host)
        where = reverse('games_room', args=[room.code])
        reply = self.client.post(reverse('games_join'),
                                 {'what': 'guest', 'name': 'Ravi', 'next': where})
        self.assertEqual(reply['Location'], where)

    def test_and_it_cannot_send_them_off_the_site(self):
        reply = self.client.post(reverse('games_join'),
                                 {'what': 'guest', 'name': 'Ravi',
                                  'next': 'https://example.net/pay'})
        self.assertEqual(reply['Location'], reverse('games_home'))


class BecomingARealUserTests(TestCase):
    """A guest signs in and keeps everything — because it is the same row all along."""

    def test_signing_in_turns_the_guest_into_that_member(self):
        row = guest(self.client, 'Ravi')
        member()
        sign_in(self.client)
        self.client.get(reverse('games_home'))
        row.refresh_from_db()
        self.assertFalse(row.is_guest)
        self.assertIsNone(row.guest_key)
        self.assertEqual(Player.objects.count(), 1, 'no second row was made')

    def test_the_name_the_score_and_the_tables_all_come_with_them(self):
        row = guest(self.client, 'Ravi')
        table = service.make_room(row)
        service.fill(table, 3)
        other = service.make_room(player('Meena', 'meena@example.com'))
        service.join(other, row)
        Player.objects.filter(pk=row.pk).update(played=4, won=2)

        member()
        sign_in(self.client)
        self.client.get(reverse('games_home'))

        row.refresh_from_db()
        self.assertEqual(row.name, 'Ravi')
        self.assertEqual((row.played, row.won), (4, 2))
        self.assertEqual(table.host_id, row.pk)
        self.assertTrue(other.seats.filter(player=row).exists())

    def test_the_session_key_is_spent(self):
        guest(self.client, 'Ravi')
        member()
        sign_in(self.client)
        self.client.get(reverse('games_home'))
        self.assertNotIn(auth.GUEST_KEY, self.client.session)

    def test_converting_survives_the_nightly_sweep_from_then_on(self):
        """The whole point: what was going to be deleted in thirty days now never is."""
        row = guest(self.client, 'Ravi')
        member()
        sign_in(self.client)
        self.client.get(reverse('games_home'))
        Player.objects.filter(pk=row.pk).update(
            last_seen_at=timezone.now() - timedelta(days=3000))
        cronhub.run(cronhub.CLEANUP, 20, only='games.guests')
        self.assertTrue(Player.objects.filter(pk=row.pk).exists())

    def test_somebody_who_already_plays_keeps_their_own_account(self):
        """Two rows for one person could be sitting at the same table, and merging them there
        would seat somebody twice. The older account wins and the guest row is left alone."""
        theirs = member()
        mine = auth.start(theirs)
        row = guest(self.client, 'Ravi')
        sign_in(self.client)
        self.client.get(reverse('games_home'))
        row.refresh_from_db()
        self.assertTrue(row.is_guest, 'the guest row was not swallowed')
        self.assertEqual(auth.profile_for(theirs).pk, mine.pk)
        self.assertNotIn(auth.GUEST_KEY, self.client.session)

    def test_adopting_onto_a_member_who_already_plays_is_refused_at_the_door(self):
        """Called straight, not through the guard. The guard checks this too, which means the
        check in adopt() is never reached from there and would look tested without being —
        and without it a direct call is an IntegrityError on the one-to-one, not a no-op."""
        theirs = member()
        mine = auth.start(theirs)
        row = guest(self.client, 'Ravi')
        request = self.client.request().wsgi_request
        request.session = self.client.session
        self.assertIsNone(auth.adopt(request, theirs, row))
        row.refresh_from_db()
        self.assertTrue(row.is_guest)
        self.assertEqual(auth.profile_for(theirs).pk, mine.pk)

    def test_the_invitation_is_always_on_their_own_page(self):
        guest(self.client, 'Ravi')
        page = self.client.get(reverse('games_settings')).content.decode()
        self.assertIn('playing as a guest', page.lower())
        self.assertIn(reverse('member_login'), page)

    def test_a_member_is_not_pestered_with_it(self):
        member()
        sign_in(self.client)
        self.client.post(reverse('games_join'), {'name': 'Anand'})
        page = self.client.get(reverse('games_settings')).content.decode()
        self.assertNotIn('playing as a guest', page.lower())


class GoingAgainTests(TestCase):
    """The hard delete. No undo, no account to ask, so what goes has to be exactly right."""

    def setUp(self):
        self.config = Settings.load()
        self.old = timezone.now() - timedelta(days=90)

    def age(self, row, days=None):
        when = timezone.now() - timedelta(days=days if days is not None else 90)
        Player.objects.filter(pk=row.pk).update(last_seen_at=when, created_at=when)
        row.refresh_from_db()
        return row

    def sweep(self):
        return cronhub.run(cronhub.CLEANUP, 20, only='games.guests')

    def test_a_guest_nobody_has_seen_is_removed_outright(self):
        row = self.age(guest(self.client))
        self.sweep()
        self.assertFalse(Player.objects.filter(pk=row.pk).exists())

    def test_their_tables_go_with_them_down_to_the_last_move(self):
        row = guest(self.client)
        room = service.make_room(row)
        service.fill(room, 3)
        game, _ = service.start(room, rng=Steady(3))
        Move.objects.create(game=game, seq=1, position=0, kind='draw')
        self.age(row)
        self.sweep()
        self.assertFalse(Room.objects.filter(pk=room.pk).exists())
        self.assertFalse(Seat.objects.filter(room_id=room.pk).exists())
        self.assertFalse(Game.objects.filter(pk=game.pk).exists())
        self.assertFalse(Move.objects.filter(game_id=game.pk).exists())

    def test_their_chair_at_somebody_elses_table_goes_too(self):
        host = player()
        room = service.make_room(host)
        row = guest(self.client)
        service.join(room, row)
        self.age(row)
        self.sweep()
        self.assertTrue(Room.objects.filter(pk=room.pk).exists())
        self.assertFalse(Seat.objects.filter(player_id=row.pk).exists())
        self.assertEqual(room.seats.count(), 1)

    def test_a_round_still_open_is_closed_before_the_chair_is_pulled(self):
        """A seat vanishing out from under a live game is worse than a dead table."""
        host = player()
        room = service.make_room(host)
        row = guest(self.client)
        service.join(room, row)
        game, _ = service.start(room, rng=Steady(5))
        self.age(row)
        self.sweep()
        game.refresh_from_db()
        room.refresh_from_db()
        self.assertIsNotNone(game.finished_at)
        self.assertEqual(room.phase, Room.Phase.CLOSED)

    def test_a_guest_who_has_been_back_lately_is_left_alone(self):
        row = guest(self.client)
        Player.objects.filter(pk=row.pk).update(last_seen_at=timezone.now())
        self.sweep()
        self.assertTrue(Player.objects.filter(pk=row.pk).exists())

    def test_a_guest_who_never_came_back_at_all_still_ages(self):
        row = guest(self.client)
        Player.objects.filter(pk=row.pk).update(
            last_seen_at=None, created_at=timezone.now() - timedelta(days=90))
        self.sweep()
        self.assertFalse(Player.objects.filter(pk=row.pk).exists())

    def test_a_member_is_never_touched_however_long_they_have_been_away(self):
        row = player()
        Player.objects.filter(pk=row.pk).update(
            last_seen_at=timezone.now() - timedelta(days=3000))
        self.sweep()
        self.assertTrue(Player.objects.filter(pk=row.pk).exists())

    # ---- the number is yours to set
    def test_the_days_are_configurable(self):
        self.config.keep_guest_days = 5
        self.config.save()
        row = self.age(guest(self.client), days=7)
        self.sweep()
        self.assertFalse(Player.objects.filter(pk=row.pk).exists())

    def test_and_a_shorter_setting_spares_somebody_inside_it(self):
        self.config.keep_guest_days = 10
        self.config.save()
        row = self.age(guest(self.client), days=7)
        self.sweep()
        self.assertTrue(Player.objects.filter(pk=row.pk).exists())

    def test_zero_keeps_them_for_ever(self):
        self.config.keep_guest_days = 0
        self.config.save()
        row = self.age(guest(self.client), days=4000)
        self.sweep()
        self.assertTrue(Player.objects.filter(pk=row.pk).exists())

    def test_thirty_days_is_what_it_starts_at(self):
        self.assertEqual(Settings().keep_guest_days, 30)

    def test_the_console_sets_it(self):
        from django.contrib.auth.models import User
        User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        console = Client()
        console.force_login(User.objects.get(username='admin'))
        console.post(reverse('games_settings_admin'), {'what': 'keep', 'keep_guest_days': '10'})
        self.assertEqual(Settings.load().keep_guest_days, 10)

    def test_the_console_refuses_nonsense(self):
        from django.contrib.auth.models import User
        User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        console = Client()
        console.force_login(User.objects.get(username='admin'))
        console.post(reverse('games_settings_admin'), {'what': 'keep', 'keep_guest_days': 'soon'})
        self.assertEqual(Settings.load().keep_guest_days, 30)

    def test_the_console_says_who_is_a_guest(self):
        from django.contrib.auth.models import User
        guest(self.client, 'Ravi')
        User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        console = Client()
        console.force_login(User.objects.get(username='admin'))
        page = console.get(reverse('games_players')).content.decode()
        self.assertIn('Ravi', page)
        self.assertIn('guest', page)
