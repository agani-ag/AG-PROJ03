"""The diary side, stocks, export and notifications."""
from datetime import date as D
from datetime import time, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import auth, defaults, push
from .cron import notify, retention
from .models import (Audit, Flow, NotifyLine, NotifyMute, NotifySent, NotifySlot, Person,
                     PeriodDay, Settings, StockItem, StockMove)
from core import cronhub


def make_person(name='Divya', email='d@example.com', phone=None, password='swan7tide'):
    person = Person.objects.create(name=name, email=email, phone=phone)
    auth.set_password(person, password)
    return person


class SignUpTests(TestCase):
    def test_sign_up_with_an_email(self):
        r = self.client.post(reverse('hibiscus_me_signup'), {
            'name': 'Divya', 'contact': 'Divya@Example.com', 'password': 'swan7tide',
            'cycle_hint': '30'})
        self.assertEqual(r.status_code, 302)
        person = Person.objects.get()
        self.assertEqual(person.email, 'divya@example.com')      # stored lower case
        self.assertEqual(person.signed_up_with, 'email')
        self.assertEqual(person.cycle_hint, 30)
        self.assertTrue(auth.check_person_password(person, 'swan7tide'))
        self.assertEqual(self.client.get(reverse('hibiscus_me_home')).status_code, 200)

    def test_sign_up_with_a_phone(self):
        self.client.post(reverse('hibiscus_me_signup'), {
            'name': 'Meena', 'contact': '+91 98765 43210', 'password': 'swan7tide'})
        person = Person.objects.get()
        self.assertEqual(person.phone, '9876543210')
        self.assertIsNone(person.email)
        self.assertEqual(person.signed_up_with, 'phone')

    def test_a_short_password_is_refused(self):
        r = self.client.post(reverse('hibiscus_me_signup'),
                             {'name': 'X', 'contact': 'x@example.com', 'password': 'short'})
        self.assertContains(r, 'at least 8 characters', status_code=200, msg_prefix='',
                            html=False)
        self.assertFalse(Person.objects.exists())

    def test_an_address_already_in_use_is_refused(self):
        make_person()
        r = self.client.post(reverse('hibiscus_me_signup'),
                             {'name': 'Someone', 'contact': 'd@example.com',
                              'password': 'swan7tide'})
        self.assertContains(r, 'already an account')
        self.assertEqual(Person.objects.count(), 1)

    def test_nonsense_contact_is_refused(self):
        r = self.client.post(reverse('hibiscus_me_signup'),
                             {'name': 'X', 'contact': 'banana', 'password': 'swan7tide'})
        self.assertContains(r, 'email address or a 10-digit mobile')

    def test_sign_up_can_be_closed(self):
        config = Settings.load()
        config.signup_open = False
        config.save()
        self.assertEqual(self.client.get(reverse('hibiscus_me_signup')).status_code, 403)

    def test_an_invite_code_is_checked(self):
        config = Settings.load()
        config.invite_code = 'hibiscus2026'
        config.save()
        bad = self.client.post(reverse('hibiscus_me_signup'),
                               {'name': 'X', 'contact': 'x@example.com',
                                'password': 'swan7tide', 'invite_code': 'wrong'})
        self.assertContains(bad, 'not right')
        self.client.post(reverse('hibiscus_me_signup'),
                         {'name': 'X', 'contact': 'x@example.com',
                          'password': 'swan7tide', 'invite_code': 'hibiscus2026'})
        self.assertTrue(Person.objects.exists())


class SignInTests(TestCase):
    def setUp(self):
        self.person = make_person()

    def test_sign_in_by_email_or_phone(self):
        r = self.client.post(reverse('hibiscus_me_login'),
                             {'contact': 'd@example.com', 'password': 'swan7tide'})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.client.get(reverse('hibiscus_me_home')).status_code, 200)

    def test_a_wrong_password_says_nothing_useful(self):
        r = self.client.post(reverse('hibiscus_me_login'),
                             {'contact': 'd@example.com', 'password': 'nope'})
        self.assertContains(r, 'did not match')

    def test_a_suspended_account_cannot_get_in(self):
        self.person.is_active = False
        self.person.save()
        r = self.client.post(reverse('hibiscus_me_login'),
                             {'contact': 'd@example.com', 'password': 'swan7tide'})
        self.assertContains(r, 'switched off')

    def test_suspending_someone_ends_their_session(self):
        self.client.post(reverse('hibiscus_me_login'),
                         {'contact': 'd@example.com', 'password': 'swan7tide'})
        self.person.is_active = False
        self.person.save()
        r = self.client.get(reverse('hibiscus_me_home'))
        self.assertEqual(r.status_code, 302)                     # checked on every request

    def test_every_diary_page_needs_signing_in(self):
        for name in ['hibiscus_me_home', 'hibiscus_me_calendar', 'hibiscus_me_log',
                     'hibiscus_me_stocks', 'hibiscus_me_settings']:
            self.assertEqual(self.client.get(reverse(name)).status_code, 302, name)

    def test_signing_in_counts_the_visit(self):
        self.client.post(reverse('hibiscus_me_login'),
                         {'contact': 'd@example.com', 'password': 'swan7tide'})
        self.person.refresh_from_db()
        self.assertEqual(self.person.open_count, 1)
        self.assertIsNotNone(self.person.last_opened_at)


class DiaryTests(TestCase):
    def setUp(self):
        self.person = make_person()
        self.client.post(reverse('hibiscus_me_login'),
                         {'contact': 'd@example.com', 'password': 'swan7tide'})
        self.config = Settings.load()
        self.today = self.config.today

    def test_today_without_history_invites_a_first_period(self):
        r = self.client.get(reverse('hibiscus_me_home'))
        self.assertContains(r, 'start with one period')

    def test_today_shows_the_prediction_and_phase(self):
        start = self.today - timedelta(days=30)
        for i in range(4):
            PeriodDay.objects.create(person=self.person, date=start + timedelta(days=i))
        r = self.client.get(reverse('hibiscus_me_home'))
        self.assertEqual(r.context['stats'].cycle_day, 31)
        self.assertContains(r, 'cycle day 31')

    def test_logging_a_day_from_the_sheet(self):
        url = reverse('hibiscus_me_day', args=[self.today.isoformat()])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'flow': int(Flow.HEAVY), 'symptoms': ['cramps'], 'note': 'ouch'})
        self.assertEqual(r.status_code, 302)
        day = self.person.days.get()
        self.assertEqual(day.flow, Flow.HEAVY)
        self.assertEqual(day.note, 'ouch')
        self.assertIsNone(day.created_by)                        # she logged it, not the admin
        self.assertTrue(Audit.objects.filter(action='logged_day', actor=None).exists())

    def test_nothing_clears_a_day(self):
        PeriodDay.objects.create(person=self.person, date=self.today)
        self.client.post(reverse('hibiscus_me_day', args=[self.today.isoformat()]),
                         {'flow': 'none'})
        self.assertEqual(self.person.days.count(), 0)

    def test_the_sheet_fragment_is_returned_for_javascript(self):
        r = self.client.get(reverse('hibiscus_me_day', args=[self.today.isoformat()]),
                            headers={'X-Sheet': '1'})
        self.assertContains(r, 'class="sheet"')
        self.assertNotContains(r, '<!DOCTYPE html>')

    def test_a_future_day_cannot_be_opened(self):
        tomorrow = (self.today + timedelta(days=1)).isoformat()
        self.assertEqual(self.client.get(
            reverse('hibiscus_me_day', args=[tomorrow])).status_code, 404)

    def test_one_person_cannot_touch_another_persons_day(self):
        other = make_person('Meena', email=None, phone='9876543210')
        PeriodDay.objects.create(person=other, date=self.today)
        self.client.post(reverse('hibiscus_me_day', args=[self.today.isoformat()]),
                         {'flow': int(Flow.LIGHT)})
        self.assertEqual(other.days.get().flow, Flow.MEDIUM)     # untouched
        self.assertEqual(self.person.days.count(), 1)            # her own row was made

    def test_logging_a_range(self):
        first = self.today - timedelta(days=4)
        r = self.client.post(reverse('hibiscus_me_log'), {
            'first_day': first.isoformat(), 'last_day': self.today.isoformat(),
            'flow': int(Flow.MEDIUM), 'symptoms': ['cramps']})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.person.days.count(), 5)

    def test_the_calendar_marks_logged_and_expected_days(self):
        start = self.today.replace(day=1)
        for i in range(3):
            PeriodDay.objects.create(person=self.person, date=start + timedelta(days=i),
                                     flow=Flow.HEAVY)
        r = self.client.get(reverse('hibiscus_me_calendar'))
        marks = {c['date']: c['mark'] for week in r.context['grid'] for c in week}
        self.assertEqual(marks[start], f'f{int(Flow.HEAVY)}')
        self.assertContains(r, 'Spotting')                       # the legend

    def test_a_silly_month_in_the_url_falls_back_to_today(self):
        r = self.client.get(reverse('hibiscus_me_calendar') + '?y=99999&m=44')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context['month_name'].month, self.today.month)

    def test_pages_are_not_cached(self):
        r = self.client.get(reverse('hibiscus_me_home'))
        self.assertIn('no-store', r['Cache-Control'])

    def test_she_can_change_her_own_name_and_password(self):
        r = self.client.post(reverse('hibiscus_me_settings'),
                             {'name': 'Divya R', 'cycle_hint': '31',
                              'new_password': 'newer7pass'})
        self.assertEqual(r.status_code, 302)
        self.person.refresh_from_db()
        self.assertEqual(self.person.name, 'Divya R')
        self.assertTrue(auth.check_person_password(self.person, 'newer7pass'))

    def test_theme_is_remembered_on_the_server_too(self):
        self.client.post(reverse('hibiscus_me_theme'), '{"theme": "dark"}',
                         content_type='application/json')
        self.person.refresh_from_db()
        self.assertEqual(self.person.theme, 'dark')

    def test_the_syncup_token_is_stored_once(self):
        self.client.post(reverse('hibiscus_me_token'), '{"token": "abc123"}',
                         content_type='application/json')
        self.person.refresh_from_db()
        self.assertEqual(self.person.syncup_token, 'abc123')
        self.assertTrue(Audit.objects.filter(action='syncup_token_saved').exists())


class StockTests(TestCase):
    def setUp(self):
        self.person = make_person()
        self.client.post(reverse('hibiscus_me_login'),
                         {'contact': 'd@example.com', 'password': 'swan7tide'})
        self.config = Settings.load()

    def test_adding_an_item_with_an_opening_count(self):
        r = self.client.post(reverse('hibiscus_me_stocks'),
                             {'what': 'item', 'name': 'Pads', 'opening': '10', 'low_at': '5'})
        self.assertEqual(r.status_code, 302)
        item = self.person.items.get()
        self.assertEqual(item.in_stock, 10)
        self.assertEqual(item.moves.get().reason, StockMove.Reason.OPENING)

    def test_the_minus_button_records_one_used(self):
        item = StockItem.objects.create(person=self.person, name='Pads', low_at=5)
        StockMove.objects.create(item=item, date=self.config.today, change=6,
                                 reason=StockMove.Reason.OPENING)
        r = self.client.post(reverse('hibiscus_me_stock_move', args=[item.pk]),
                             '{"change": -1}', content_type='application/json')
        self.assertEqual(r.json(), {'in_stock': 5, 'is_low': True})
        self.assertEqual(item.moves.count(), 2)

    def test_a_movement_can_be_back_dated(self):
        item = StockItem.objects.create(person=self.person, name='Pads')
        older = self.config.today - timedelta(days=5)
        self.client.post(reverse('hibiscus_me_stock_move', args=[item.pk]),
                         {'date': older.isoformat(), 'change': '-2',
                          'reason': StockMove.Reason.USED, 'note': ''})
        self.assertEqual(item.moves.get().date, older)

    def test_a_future_movement_is_refused(self):
        item = StockItem.objects.create(person=self.person, name='Pads')
        future = (self.config.today + timedelta(days=1)).isoformat()
        self.client.post(reverse('hibiscus_me_stock_move', args=[item.pk]),
                         {'date': future, 'change': '-1', 'reason': StockMove.Reason.USED})
        self.assertEqual(item.moves.count(), 0)

    def test_another_persons_item_is_out_of_reach(self):
        other = make_person('Meena', email=None, phone='9876543210')
        item = StockItem.objects.create(person=other, name='Sofy')
        r = self.client.post(reverse('hibiscus_me_stock_move', args=[item.pk]),
                             '{"change": -1}', content_type='application/json')
        self.assertEqual(r.status_code, 404)
        self.assertEqual(item.moves.count(), 0)

    def test_low_stock_shows_on_today(self):
        item = StockItem.objects.create(person=self.person, name='Sofy', low_at=3)
        StockMove.objects.create(item=item, date=self.config.today, change=2,
                                 reason=StockMove.Reason.OPENING)
        self.assertContains(self.client.get(reverse('hibiscus_me_home')), 'Running low')


class ExportTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person()

    def test_the_export_is_a_normal_file_download(self):
        day = PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1), flow=Flow.HEAVY,
                                        note='heavy day')
        day.symptoms.create(symptom='cramps')
        item = StockItem.objects.create(person=self.person, name='Pads')
        StockMove.objects.create(item=item, date=D(2026, 10, 1), change=5,
                                 reason=StockMove.Reason.OPENING)
        r = self.client.get(reverse('hibiscus_person_export', args=[self.person.pk]))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r['Content-Type'].startswith('text/csv'))
        self.assertIn('attachment; filename="hibiscus-divya-', r['Content-Disposition'])
        body = r.content.decode()
        self.assertIn('2026-10-01,Heavy,cramps,heavy day', body)
        self.assertIn('Pads,5,Opening stock', body)
        self.assertTrue(Audit.objects.filter(action='exported').exists())

    def test_the_export_needs_the_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(
            reverse('hibiscus_person_export', args=[self.person.pk])).status_code, 302)


class NotificationTests(TestCase):
    def setUp(self):
        defaults.ensure_slots()
        self.config = Settings.load()
        self.config.syncup_base = ''              # nothing is actually sent in tests
        self.config.save()
        self.person = make_person()
        self.person.syncup_token = 'tok'
        self.person.save()
        self.today = self.config.today
        self.morning = NotifySlot.objects.get(key=NotifySlot.Key.MORNING)
        self.afternoon = NotifySlot.objects.get(key=NotifySlot.Key.AFTERNOON)
        self.evening = NotifySlot.objects.get(key=NotifySlot.Key.EVENING)

    def log_period(self, start, length=4, flow=Flow.MEDIUM):
        for i in range(length):
            PeriodDay.objects.create(person=self.person, date=start + timedelta(days=i), flow=flow)

    # ---- the defaults
    def test_three_slots_are_on_and_the_fourth_is_not(self):
        self.assertEqual(NotifySlot.objects.filter(is_enabled=True).count(), 3)
        self.assertFalse(NotifySlot.objects.get(key=NotifySlot.Key.NIGHT).is_enabled)
        self.assertTrue(NotifyLine.objects.filter(slot=self.morning).exists())

    def test_seeding_twice_changes_nothing(self):
        before = NotifyLine.objects.count()
        defaults.ensure_slots()
        self.assertEqual(NotifyLine.objects.count(), before)

    # ---- situations
    def test_a_quiet_day_only_speaks_in_the_morning(self):
        self.log_period(self.today - timedelta(days=12))
        self.assertIsNotNone(push.compose(self.person, self.morning, self.config, self.today))
        self.assertIsNone(push.compose(self.person, self.afternoon, self.config, self.today))
        self.assertIsNone(push.compose(self.person, self.evening, self.config, self.today))

    def test_an_unlogged_running_period_speaks_three_times_differently(self):
        self.log_period(self.today - timedelta(days=2), length=2)   # nothing logged today
        bodies = []
        for slot in (self.morning, self.afternoon, self.evening):
            message = push.compose(self.person, slot, self.config, self.today)
            self.assertIsNotNone(message, slot.key)
            bodies.append(message['body'])
        self.assertEqual(len(set(bodies)), 3)                  # three different sentences

    def test_a_late_period_is_named_as_late(self):
        self.log_period(self.today - timedelta(days=40))
        message = push.compose(self.person, self.morning, self.config, self.today)
        self.assertIn(message['situation'], (NotifyLine.Situation.LATE_EARLY,
                                             NotifyLine.Situation.LATE_LONG))
        self.assertTrue(message['body'])

    def test_low_stock_rides_along_in_the_morning_and_speaks_in_the_afternoon(self):
        self.log_period(self.today - timedelta(days=12))
        item = StockItem.objects.create(person=self.person, name='Sofy', low_at=3)
        StockMove.objects.create(item=item, date=self.today, change=1,
                                 reason=StockMove.Reason.OPENING)
        morning = push.compose(self.person, self.morning, self.config, self.today)
        afternoon = push.compose(self.person, self.afternoon, self.config, self.today)
        self.assertIn('Sofy', morning['body'])
        self.assertIsNotNone(afternoon)
        self.assertIn('Sofy', afternoon['body'])

    def test_every_message_fits_the_syncup_limits(self):
        self.log_period(self.today - timedelta(days=2), length=2)
        for slot in NotifySlot.objects.filter(is_enabled=True):
            message = push.compose(self.person, slot, self.config, self.today)
            if message:
                self.assertLessEqual(len(message['title']), push.TITLE_MAX)
                self.assertLessEqual(len(message['body']), push.BODY_MAX)

    # ---- sending
    def test_a_slot_goes_out_once_a_day(self):
        self.log_period(self.today - timedelta(days=12))
        first = push.run_slot(self.morning, self.config)
        second = push.run_slot(self.morning, self.config)
        self.assertEqual(NotifySent.objects.count(), 1)
        self.assertEqual(second['sent'] + second['quiet'] + second['muted'], 0)
        self.assertEqual(first['failed'], 1)                  # no SyncUp base: recorded, not sent

    def test_a_muted_slot_is_skipped(self):
        self.log_period(self.today - timedelta(days=12))
        NotifyMute.objects.create(person=self.person, slot=self.morning)
        result = push.run_slot(self.morning, self.config)
        self.assertEqual(result['muted'], 1)
        self.assertFalse(NotifySent.objects.exists())

    def test_a_dry_run_sends_nothing(self):
        self.log_period(self.today - timedelta(days=12))
        result = push.run_slot(self.morning, self.config, dry_run=True)
        self.assertEqual(len(result['preview']), 1)
        self.assertFalse(NotifySent.objects.exists())

    # ---- due-ness
    def test_a_slot_is_due_only_once_its_time_has_passed(self):
        tz = self.config.tzinfo
        at_seven = timezone.datetime(2026, 10, 15, 7, 0, tzinfo=tz)
        at_eight = timezone.datetime(2026, 10, 15, 8, 5, tzinfo=tz)
        much_later = timezone.datetime(2026, 10, 15, 14, 0, tzinfo=tz)
        self.assertFalse(push.slot_is_due(self.morning, self.config, at_seven))
        self.assertTrue(push.slot_is_due(self.morning, self.config, at_eight))
        self.assertFalse(push.slot_is_due(self.morning, self.config, much_later))  # too late

    # ---- the cron hooks
    def test_the_tick_hook_is_registered_with_the_hub(self):
        names = [h.name for h in cronhub.hooks(cronhub.TICK)]
        self.assertIn('hibiscus.notify', names)

    def test_the_tick_seeds_the_slots_if_nobody_has_opened_the_admin_page(self):
        """Otherwise the hook would find no slots and quietly send nothing for ever."""
        NotifySlot.objects.all().delete()
        notify(cronhub.Context(kind=cronhub.TICK))
        self.assertEqual(NotifySlot.objects.count(), 4)
        self.assertTrue(NotifyLine.objects.exists())

    def test_the_tick_hook_runs_and_says_what_it_did(self):
        self.log_period(self.today - timedelta(days=12))
        result = notify(cronhub.Context(kind=cronhub.TICK))
        self.assertIn('slots', result)
        self.assertIn('SyncUp base not set', result['note'])

    def test_retention_trims_old_rows(self):
        old = NotifySent.objects.create(person=self.person, slot=self.morning,
                                        date=self.today - timedelta(days=200))
        NotifySent.objects.create(person=self.person, slot=self.evening, date=self.today)
        Audit.objects.create(action='viewed_person', person=self.person)
        Audit.objects.filter(pk__gt=0).update(at=timezone.now() - timedelta(days=400))
        result = retention(cronhub.Context(kind=cronhub.CLEANUP))
        self.assertEqual(result['sent_deleted'], 1)
        self.assertGreaterEqual(result['audit_deleted'], 1)
        self.assertFalse(NotifySent.objects.filter(pk=old.pk).exists())


class NotificationAdminTests(TestCase):
    def setUp(self):
        User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(User.objects.get(username='admin'))

    def test_the_page_seeds_and_lists_the_slots(self):
        r = self.client.get(reverse('hibiscus_notifications'))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(NotifySlot.objects.count(), 4)
        self.assertContains(r, 'Morning')
        self.assertContains(r, 'Nothing can be delivered yet')      # no SyncUp address set

    def test_times_and_modes_can_be_changed(self):
        defaults.ensure_slots()
        morning = NotifySlot.objects.get(key=NotifySlot.Key.MORNING)
        evening = NotifySlot.objects.get(key=NotifySlot.Key.EVENING)
        data = {}
        for slot in NotifySlot.objects.all():
            data[f'slot-{slot.pk}-at'] = '09:15' if slot == morning else slot.at.strftime('%H:%M')
            data[f'slot-{slot.pk}-mode'] = slot.mode
            if slot.is_enabled and slot != evening:
                data[f'slot-{slot.pk}-enabled'] = 'on'
        self.client.post(reverse('hibiscus_notifications'), data)
        morning.refresh_from_db()
        evening.refresh_from_db()
        self.assertEqual(morning.at, time(9, 15))
        self.assertFalse(evening.is_enabled)

    def test_preview_sends_nothing(self):
        defaults.ensure_slots()
        person = make_person()
        for i in range(4):
            PeriodDay.objects.create(person=person, date=Settings.load().today - timedelta(days=12 - i))
        r = self.client.post(reverse('hibiscus_notifications_preview'), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(NotifySent.objects.exists())
        self.assertTrue(self.client.session['hibiscus_preview'])

    def test_the_page_needs_the_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('hibiscus_notifications')).status_code, 302)
