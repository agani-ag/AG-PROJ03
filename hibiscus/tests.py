"""Hibiscus tests.

The five worked examples in the mockup (section 3) are the first five tests here — they are the
agreed definition of the rules, so they belong in code where they can fail.
"""
import json
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import auth as hub
from core.models import Member
from core.models import HubSettings

from . import auth, cycle, defaults, push
from .models import (Audit, Flow, NotifySent, NotifySlot, Person, PeriodDay, Settings,
                     StockItem, StockMove)

D = date


def days(*pairs):
    """Shorthand: days(('2026-09-15', Flow.MEDIUM), ...) → [(date, flow), ...]"""
    return [(D.fromisoformat(d), f) for d, f in pairs]


def run_of(start, length, flow=Flow.MEDIUM):
    first = D.fromisoformat(start)
    return [(first + timedelta(days=i), flow) for i in range(length)]


def make_person(name='Divya', email='d@example.com', phone=None, password='swan7tide',
                syncup_token='', **fields):
    """A hub member who keeps a diary — which is what a Person is now.

    The identity lives on the member; the Person row is the diary's own part of them. The old
    columns are still filled while the move settles, so nothing that reads them breaks.
    """
    member = Member.objects.create(name=name, email=email, phone=phone,
                                   syncup_token=syncup_token)
    if password:
        hub.set_password(member, password)
    return Person.objects.create(member=member, name=name, email=email, phone=phone,
                                 **fields)


def refreshed(person):
    """Re-read both halves: the diary row, and the member that now holds the identity."""
    Person.refresh_from_db(person)
    if person.member_id:
        person.member.refresh_from_db()
    return person


def sign_in(client, contact='d@example.com', password='swan7tide'):
    """Through the hub's one door, signing out first so the account actually swaps."""
    client.post(reverse('member_logout'))
    return client.post(reverse('member_login'),
                       {'sign_in_id': contact, 'password': password})


class WorkedExamplesTests(TestCase):
    """Straight from the mockup's table — same data, same expected answers."""

    def test_leading_and_trailing_spotting_is_shown_but_not_counted(self):
        data = days(('2026-09-14', Flow.SPOTTING), ('2026-09-15', Flow.MEDIUM),
                    ('2026-09-16', Flow.MEDIUM), ('2026-09-17', Flow.HEAVY),
                    ('2026-09-18', Flow.LIGHT), ('2026-09-19', Flow.SPOTTING))
        p, = cycle.periods(data)
        self.assertEqual((p.start, p.end, p.days), (D(2026, 9, 15), D(2026, 9, 18), 4))
        # the spotting days still belong to the run, for the calendar
        self.assertEqual((p.shown_from, p.shown_to), (D(2026, 9, 14), D(2026, 9, 19)))

    def test_one_missing_day_is_bridged(self):
        data = days(('2026-07-13', Flow.HEAVY), ('2026-07-14', Flow.HEAVY),
                    ('2026-07-16', Flow.LIGHT))          # the 15th was never logged
        p, = cycle.periods(data)
        self.assertEqual((p.start, p.end, p.days), (D(2026, 7, 13), D(2026, 7, 16), 4))

    def test_not_recorded_counts_as_a_period_day(self):
        data = run_of('2026-06-11', 4, Flow.NOT_RECORDED)
        p, = cycle.periods(data)
        self.assertEqual((p.start, p.end, p.days), (D(2026, 6, 11), D(2026, 6, 14), 4))

    def test_a_run_of_only_spotting_is_not_a_period(self):
        self.assertEqual(cycle.periods(days(('2026-10-05', Flow.SPOTTING))), [])

    def test_two_empty_days_split_the_run(self):
        data = days(('2026-10-01', Flow.MEDIUM), ('2026-10-02', Flow.LIGHT),
                    ('2026-10-05', Flow.MEDIUM))         # the 3rd and 4th are empty
        first, second = cycle.periods(data)
        self.assertEqual(first.start, D(2026, 10, 1))
        self.assertEqual(second.start, D(2026, 10, 5))
        # …and a 4-day gap is below MIN_GAP? No — it is 4 days, which is inside 1–89, so it counts.
        self.assertEqual(cycle.gaps([first, second]), [4])


class RunGroupingTests(TestCase):
    def test_days_in_any_order_and_duplicates_are_handled(self):
        data = days(('2026-09-16', Flow.MEDIUM), ('2026-09-15', Flow.MEDIUM),
                    ('2026-09-15', Flow.HEAVY))          # same date twice
        p, = cycle.periods(data)
        self.assertEqual(p.days, 2)

    def test_three_separate_periods(self):
        data = run_of('2026-07-13', 4) + run_of('2026-08-13', 4) + run_of('2026-09-15', 5)
        found = cycle.periods(data)
        self.assertEqual([p.start for p in found],
                         [D(2026, 7, 13), D(2026, 8, 13), D(2026, 9, 15)])

    def test_no_days_means_no_periods(self):
        self.assertEqual(cycle.periods([]), [])


class StatsTests(TestCase):
    def setUp(self):
        # 13 Jul, 13 Aug, 15 Sep → gaps of 31 and 33 days
        self.data = run_of('2026-07-13', 4) + run_of('2026-08-13', 4) + run_of('2026-09-15', 5)

    def test_averages_and_prediction(self):
        s = cycle.stats(self.data, today=D(2026, 10, 15))
        self.assertEqual(cycle.gaps(cycle.periods(self.data)), [31, 33])
        self.assertEqual(s.avg_cycle, 32)
        self.assertEqual(s.avg_period, 4)                  # 4, 4, 5 → 4.33 → 4
        self.assertEqual(s.last_start, D(2026, 9, 15))
        self.assertEqual(s.predicted_next, D(2026, 10, 17))
        self.assertEqual(s.days_until, 2)
        self.assertEqual(s.cycle_day, 31)
        self.assertFalse(s.estimated)
        self.assertFalse(s.is_overdue)

    def test_overdue_reads_as_negative_days(self):
        s = cycle.stats(self.data, today=D(2026, 10, 25))
        self.assertEqual(s.days_until, -8)
        self.assertTrue(s.is_overdue)

    def test_one_period_falls_back_to_the_default_and_says_so(self):
        s = cycle.stats(run_of('2026-09-15', 4), today=D(2026, 9, 20))
        self.assertIsNone(s.avg_cycle)
        self.assertTrue(s.estimated)
        self.assertEqual(s.cycle_len, cycle.DEFAULT_CYCLE)
        self.assertEqual(s.predicted_next, D(2026, 10, 13))

    def test_sign_up_hint_is_used_before_there_is_history(self):
        s = cycle.stats(run_of('2026-09-15', 4), today=D(2026, 9, 20), cycle_hint=30)
        self.assertEqual(s.cycle_len, 30)
        self.assertEqual(s.predicted_next, D(2026, 10, 15))

    def test_a_three_month_gap_is_ignored_when_averaging(self):
        data = run_of('2026-01-01', 4) + run_of('2026-06-01', 4) + run_of('2026-07-03', 4)
        self.assertEqual(cycle.gaps(cycle.periods(data)), [32])   # 151 days dropped, 32 kept

    def test_rounding_is_half_up_not_bankers(self):
        self.assertEqual(cycle._mean_half_up([28, 29]), 29)       # round() would give 28
        self.assertEqual(cycle._mean_half_up([28, 28, 29]), 28)
        self.assertIsNone(cycle._mean_half_up([]))


class PhaseTests(TestCase):
    def setUp(self):
        self.data = run_of('2026-07-13', 4) + run_of('2026-08-13', 4) + run_of('2026-09-15', 5)

    def phase_on(self, day):
        s = cycle.stats(self.data, today=day)
        return cycle.phase(s, today=day)

    def test_phases_through_a_cycle(self):
        # Day 1 is 15 Sep, average cycle 32 → ovulation on day 18 (2 Oct), fertile days 13–19.
        self.assertEqual(self.phase_on(D(2026, 9, 16)), 'menstrual')   # day 2 of a 4-day period
        self.assertEqual(self.phase_on(D(2026, 9, 22)), 'follicular')  # day 8
        self.assertEqual(self.phase_on(D(2026, 9, 27)), 'fertile')     # day 13, window opens
        self.assertEqual(self.phase_on(D(2026, 10, 2)), 'ovulation')   # day 18
        self.assertEqual(self.phase_on(D(2026, 10, 3)), 'fertile')     # day 19, window closes
        self.assertEqual(self.phase_on(D(2026, 10, 10)), 'luteal')     # day 26
        self.assertEqual(self.phase_on(D(2026, 10, 25)), 'late')       # day 41, past the cycle

    def test_no_history_means_no_phase(self):
        self.assertIsNone(cycle.phase(cycle.stats([], today=D(2026, 10, 15))))

    def test_windows_use_a_fixed_luteal_phase(self):
        w = cycle.windows(32)
        self.assertEqual((w['ovulation'], w['fertile_from'], w['fertile_to']), (18, 13, 19))


class MonthMapTests(TestCase):
    def test_logged_days_beat_expected_days(self):
        data = run_of('2026-10-01', 3)
        m = cycle.month_map(data, 2026, 10, today=D(2026, 10, 15))
        self.assertEqual(m[D(2026, 10, 1)], f'f{int(Flow.MEDIUM)}')
        self.assertNotIn('pred', [m.get(D(2026, 10, 1))])

    def test_expected_days_appear_for_the_next_cycle_only(self):
        data = run_of('2026-07-13', 4) + run_of('2026-08-13', 4) + run_of('2026-09-15', 5)
        m = cycle.month_map(data, 2026, 10, today=D(2026, 10, 15))
        self.assertEqual(m[D(2026, 10, 17)], 'pred')       # 15 Sep + 32 days
        self.assertEqual(m[D(2026, 10, 20)], 'pred')       # 4-day period
        self.assertNotIn(D(2026, 10, 21), m)

    def test_spotting_keeps_its_own_shade(self):
        m = cycle.month_map(days(('2026-10-05', Flow.SPOTTING)), 2026, 10, today=D(2026, 10, 15))
        self.assertEqual(m[D(2026, 10, 5)], f'f{int(Flow.SPOTTING)}')

    def test_the_map_stays_inside_its_month(self):
        """The map is sparse — only marked days are in it — and never leaks into a neighbour."""
        m = cycle.month_map(run_of('2026-02-01', 2), 2026, 2, today=D(2026, 2, 15))
        self.assertIn(D(2026, 2, 1), m)
        self.assertTrue(all(d.year == 2026 and d.month == 2 for d in m))
        self.assertLessEqual(max(m), D(2026, 2, 28))       # 2026 is not a leap year

    def test_december_rolls_over_to_the_next_year(self):
        m = cycle.month_map(run_of('2026-12-30', 2), 2026, 12, today=D(2026, 12, 31))
        self.assertIn(D(2026, 12, 31), m)
        self.assertTrue(all(d.month == 12 for d in m))


class ModelTests(TestCase):
    def setUp(self):
        self.person = make_person()

    def test_one_row_per_day_per_person(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))
        with self.assertRaises(IntegrityError):
            PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))

    def test_two_people_can_log_the_same_day(self):
        other = make_person('Meena', email=None, phone='9876543210')
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))
        PeriodDay.objects.create(person=other, date=D(2026, 10, 1))
        self.assertEqual(PeriodDay.objects.count(), 2)

    def test_spotting_does_not_count_as_a_period_day(self):
        self.assertFalse(Flow.counts_as_period(Flow.SPOTTING))
        for flow in (Flow.NOT_RECORDED, Flow.LIGHT, Flow.MEDIUM, Flow.HEAVY):
            self.assertTrue(Flow.counts_as_period(flow))

    def test_stock_is_the_sum_of_its_movements(self):
        item = StockItem.objects.create(person=self.person, name='Pads', low_at=5)
        StockMove.objects.create(item=item, date=D(2026, 8, 2), change=10,
                                 reason=StockMove.Reason.OPENING)
        StockMove.objects.create(item=item, date=D(2026, 8, 5), change=-4,
                                 reason=StockMove.Reason.USED)
        self.assertEqual(item.in_stock, 6)
        self.assertFalse(item.is_low)
        StockMove.objects.create(item=item, date=D(2026, 8, 7), change=-2,
                                 reason=StockMove.Reason.USED)
        self.assertEqual(item.in_stock, 4)
        self.assertTrue(item.is_low)

    def test_low_at_zero_turns_the_alert_off(self):
        item = StockItem.objects.create(person=self.person, name='Sofy', low_at=0)
        StockMove.objects.create(item=item, date=D(2026, 8, 2), change=1,
                                 reason=StockMove.Reason.OPENING)
        self.assertFalse(item.is_low)

    def test_deleting_a_person_takes_everything_with_them(self):
        day = PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))
        day.symptoms.create(symptom='cramps')
        item = StockItem.objects.create(person=self.person, name='Pads')
        StockMove.objects.create(item=item, date=D(2026, 10, 1), change=5,
                                 reason=StockMove.Reason.OPENING)
        self.person.delete()
        self.assertEqual(PeriodDay.objects.count(), 0)
        self.assertEqual(StockItem.objects.count(), 0)
        self.assertEqual(StockMove.objects.count(), 0)     # the old app orphaned these

    def test_admin_entry_records_who_typed_it(self):
        admin = User.objects.create_superuser('admin2', 'a2@example.com', 'pw-for-tests-1234')
        day = PeriodDay.objects.create(person=self.person, date=D(2026, 10, 2), created_by=admin)
        self.assertEqual(day.created_by, admin)


class SettingsTests(TestCase):
    def test_there_is_only_ever_one_row(self):
        a = Settings.load()
        a.default_cycle = 30
        a.save()
        b = Settings.load()
        self.assertEqual(b.pk, 1)
        self.assertEqual(b.default_cycle, 30)
        self.assertEqual(Settings.objects.count(), 1)

    def test_today_follows_the_module_time_zone(self):
        config = Settings.load()
        config.time_zone = 'Asia/Kolkata'
        config.save()
        self.assertEqual(config.today, timezone.localtime(timezone.now(), config.tzinfo).date())

    def test_a_bad_time_zone_does_not_break_the_page(self):
        config = Settings.load()
        config.time_zone = 'Not/AZone'
        config.save()
        self.assertIsNotNone(config.today)


class PasswordTests(TestCase):
    def setUp(self):
        self.person = make_person()

    def test_generated_passwords_avoid_look_alike_characters(self):
        for _ in range(20):
            pw = auth.generate_password()
            self.assertEqual(len(pw), auth.PASSWORD_LENGTH)
            self.assertFalse(set(pw) & set('ol01iI'))

    def test_password_is_stored_hashed_and_checks_out(self):
        auth.set_password(self.person, 'swan7tide')
        refreshed(self.person)
        self.assertNotIn('swan7tide', self.person.password_hash)
        self.assertTrue(auth.check_person_password(self.person, 'swan7tide'))
        self.assertFalse(auth.check_person_password(self.person, 'wrong'))

    def test_no_password_set_means_no_sign_in(self):
        self.assertFalse(auth.check_person_password(self.person, ''))

    def test_people_are_found_by_email_or_phone(self):
        other = make_person('Meena', email=None, phone='9876543210')
        self.assertEqual(auth.find_person('D@Example.com'), self.person)
        self.assertEqual(auth.find_person('9876543210'), other)
        self.assertEqual(auth.find_person('+91 98765 43210'), other)   # typed with spaces
        self.assertIsNone(auth.find_person('nobody@example.com'))
        self.assertIsNone(auth.find_person(''))


class AdminSideTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person()

    # ---- access
    def test_every_page_needs_the_admin(self):
        self.client.logout()
        for url in [reverse('hibiscus_people'),
                    reverse('hibiscus_person', args=[self.person.pk]),
                    reverse('hibiscus_person_add'),
                    reverse('hibiscus_settings')]:
            self.assertEqual(self.client.get(url).status_code, 302, url)

    def test_a_signed_in_non_superuser_is_turned_away(self):
        User.objects.create_user('helper', 'h@example.com', 'pw-for-tests-1234')
        self.client.logout()
        self.client.login(username='helper', password='pw-for-tests-1234')
        self.assertEqual(self.client.get(reverse('hibiscus_people')).status_code, 302)

    # ---- people list
    def test_people_list_shows_everyone_active(self):
        r = self.client.get(reverse('hibiscus_people'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Divya')

    def test_archived_people_are_behind_their_own_view(self):
        self.person.is_archived = True
        self.person.save()
        self.assertNotContains(self.client.get(reverse('hibiscus_people')), 'd@example.com')
        self.assertContains(self.client.get(reverse('hibiscus_people') + '?archived=1'), 'Divya')

    # ---- adding
    def test_adding_someone_shows_a_password_once(self):
        r = self.client.post(reverse('hibiscus_person_add'),
                             {'name': 'Meena', 'phone': '9876543210', 'email': '', 'cycle_hint': ''})
        self.assertEqual(r.status_code, 200)
        meena = Person.objects.get(name='Meena')
        self.assertEqual(meena.phone, '9876543210')
        self.assertEqual(meena.signed_up_with, 'admin')
        self.assertEqual(meena.created_by, self.admin)
        self.assertTrue(meena.member.password_hash, 'the hash belongs to the hub member')
        self.assertEqual(meena.password_hash, '', 'and not to the diary row any more')
        shown = r.context['password']
        self.assertTrue(auth.check_person_password(meena, shown))      # the one on screen works
        self.assertNotContains(r, meena.member.password_hash)          # the hash is not on screen

    def test_an_account_needs_an_email_or_a_phone(self):
        r = self.client.post(reverse('hibiscus_person_add'), {'name': 'Nobody'})
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Add an email address or a phone number.')
        self.assertFalse(Person.objects.filter(name='Nobody').exists())

    def test_a_bad_phone_number_is_refused(self):
        r = self.client.post(reverse('hibiscus_person_add'), {'name': 'X', 'phone': '12345'})
        self.assertContains(r, 'Enter a 10-digit mobile number.')

    def test_phone_numbers_are_stored_as_ten_digits(self):
        self.client.post(reverse('hibiscus_person_add'),
                         {'name': 'Meena', 'phone': '+91 98765 43210'})
        self.assertEqual(Person.objects.get(name='Meena').phone, '9876543210')

    # ---- password, state
    def test_new_password_replaces_the_old_one(self):
        auth.set_password(self.person, 'old7pass')
        r = self.client.post(reverse('hibiscus_person_password', args=[self.person.pk]))
        refreshed(self.person)
        self.assertFalse(auth.check_person_password(self.person, 'old7pass'))
        self.assertTrue(auth.check_person_password(self.person, r.context['password']))

    def test_suspend_and_resume(self):
        self.client.post(reverse('hibiscus_person_state', args=[self.person.pk, 'suspend']))
        refreshed(self.person)
        self.assertFalse(self.person.is_active)
        self.client.post(reverse('hibiscus_person_state', args=[self.person.pk, 'resume']))
        refreshed(self.person)
        self.assertTrue(self.person.is_active)

    def test_unknown_state_action_is_404(self):
        r = self.client.post(reverse('hibiscus_person_state', args=[self.person.pk, 'explode']))
        self.assertEqual(r.status_code, 404)

    # ---- deleting
    def test_delete_needs_the_name_typed_correctly(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))
        r = self.client.post(reverse('hibiscus_person_delete', args=[self.person.pk]),
                             {'confirm': 'wrong name'})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(Person.objects.filter(pk=self.person.pk).exists())

    def test_delete_takes_everything(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))
        item = StockItem.objects.create(person=self.person, name='Pads')
        StockMove.objects.create(item=item, date=D(2026, 10, 1), change=5,
                                 reason=StockMove.Reason.OPENING)
        self.client.post(reverse('hibiscus_person_delete', args=[self.person.pk]),
                         {'confirm': 'divya'})                     # case does not matter
        self.assertFalse(Person.objects.filter(pk=self.person.pk).exists())
        self.assertEqual(PeriodDay.objects.count(), 0)
        self.assertEqual(StockMove.objects.count(), 0)
        self.assertTrue(Audit.objects.filter(action='deleted_person').exists())

    # ---- audit
    def test_opening_a_diary_is_recorded(self):
        self.client.get(reverse('hibiscus_person', args=[self.person.pk]))
        row = Audit.objects.filter(action='viewed_person', person=self.person).first()
        self.assertEqual(row.actor, self.admin)

    def test_creating_and_resetting_are_recorded(self):
        self.client.post(reverse('hibiscus_person_add'), {'name': 'Meena', 'phone': '9876543210'})
        self.client.post(reverse('hibiscus_person_password', args=[self.person.pk]))
        self.assertTrue(Audit.objects.filter(action='created_person').exists())
        self.assertTrue(Audit.objects.filter(action='reset_password').exists())

    # ---- settings
    def test_settings_can_be_saved(self):
        r = self.client.post(reverse('hibiscus_settings'), {
            'display_name': 'Hibiscus', 'time_zone': 'Asia/Kolkata', 'week_starts_sunday': 'on',
            'default_cycle': 30, 'default_period': 4, 'luteal_days': 14, 'bridge_days': 1,
            'signup_open': 'on', 'invite_code': '', 'keep_audit_days': 180,
            'syncup_base': '', 'syncup_notify_key': '', 'notify_late_hours': 2,
        })
        self.assertEqual(r.status_code, 302)
        config = Settings.load()
        self.assertEqual(config.default_cycle, 30)
        self.assertEqual(config.updated_by, self.admin)
        self.assertTrue(Audit.objects.filter(action='changed_settings').exists())

    # ---- periods tab
    def periods_url(self):
        return reverse('hibiscus_person_periods', args=[self.person.pk])

    def test_a_range_saves_one_row_per_day(self):
        r = self.client.post(self.periods_url(), {
            'first_day': '2026-10-01', 'last_day': '2026-10-05',
            'flow': int(Flow.MEDIUM), 'symptoms': ['cramps', 'backache'], 'note': 'first cycle',
        })
        self.assertEqual(r.status_code, 302)
        days = list(self.person.days.order_by('date'))
        self.assertEqual(len(days), 5)
        self.assertEqual(days[0].date, D(2026, 10, 1))
        self.assertEqual(days[0].flow, Flow.MEDIUM)
        self.assertEqual(days[0].note, 'first cycle')
        self.assertEqual(days[0].created_by, self.admin)          # typed on her behalf
        self.assertEqual(
            sorted(days[2].symptoms.values_list('symptom', flat=True)), ['backache', 'cramps'])

    def test_a_blank_last_day_saves_a_single_day(self):
        self.client.post(self.periods_url(),
                         {'first_day': '2026-10-01', 'flow': int(Flow.HEAVY)})
        self.assertEqual(self.person.days.count(), 1)

    def test_saving_a_day_twice_updates_it_instead_of_duplicating(self):
        self.client.post(self.periods_url(),
                         {'first_day': '2026-10-01', 'flow': int(Flow.MEDIUM), 'symptoms': ['cramps']})
        self.client.post(self.periods_url(),
                         {'first_day': '2026-10-01', 'flow': int(Flow.HEAVY), 'symptoms': ['nausea']})
        self.assertEqual(self.person.days.count(), 1)
        day = self.person.days.get()
        self.assertEqual(day.flow, Flow.HEAVY)
        self.assertEqual(list(day.symptoms.values_list('symptom', flat=True)), ['nausea'])

    def test_future_days_are_refused(self):
        tomorrow = Settings.load().today + timedelta(days=1)
        r = self.client.post(self.periods_url(),
                             {'first_day': tomorrow.isoformat(), 'flow': int(Flow.MEDIUM)})
        self.assertContains(r, 'in the future')
        self.assertEqual(self.person.days.count(), 0)

    def test_a_backwards_range_is_refused(self):
        r = self.client.post(self.periods_url(), {'first_day': '2026-10-05',
                                                  'last_day': '2026-10-01',
                                                  'flow': int(Flow.MEDIUM)})
        self.assertContains(r, 'cannot be before')
        self.assertEqual(self.person.days.count(), 0)

    def test_an_absurd_range_is_refused(self):
        r = self.client.post(self.periods_url(), {'first_day': '2026-01-01',
                                                  'last_day': '2026-06-01',
                                                  'flow': int(Flow.MEDIUM)})
        self.assertContains(r, 'more than 31 days')
        self.assertEqual(self.person.days.count(), 0)

    def test_the_table_groups_days_and_explains_the_rules(self):
        # spotting, then three real days, with one day in the middle never logged
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1), flow=Flow.SPOTTING)
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 2), flow=Flow.MEDIUM)
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 4), flow=Flow.LIGHT)
        r = self.client.get(self.periods_url())
        self.assertEqual(r.status_code, 200)
        row, = r.context['rows']
        self.assertEqual((row['period'].start, row['period'].end), (D(2026, 10, 2), D(2026, 10, 4)))
        self.assertEqual(row['period'].days, 3)
        self.assertTrue(row['has_lead_spotting'])
        self.assertEqual(row['bridged'], [D(2026, 10, 3)])
        self.assertContains(r, 'not counted')
        self.assertContains(r, 'bridged')

    # ---- one day at a time
    def test_editing_one_day(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 2), flow=Flow.MEDIUM)
        url = reverse('hibiscus_day_edit', args=[self.person.pk, '2026-10-02'])
        self.assertEqual(self.client.get(url).status_code, 200)
        r = self.client.post(url, {'flow': int(Flow.SPOTTING), 'symptoms': ['acne'], 'note': 'ok'})
        self.assertEqual(r.status_code, 302)
        day = self.person.days.get()
        self.assertEqual(day.flow, Flow.SPOTTING)
        self.assertEqual(day.note, 'ok')
        self.assertEqual(list(day.symptoms.values_list('symptom', flat=True)), ['acne'])
        self.assertTrue(Audit.objects.filter(action='edited_day').exists())

    def test_editing_a_day_that_was_never_logged_is_404(self):
        url = reverse('hibiscus_day_edit', args=[self.person.pk, '2026-10-02'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_a_nonsense_date_in_the_url_is_404(self):
        url = reverse('hibiscus_day_edit', args=[self.person.pk, 'last-tuesday'])
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_removing_a_middle_day_keeps_the_period_together(self):
        for i in range(4):
            PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1) + timedelta(days=i))
        self.client.post(reverse('hibiscus_day_delete', args=[self.person.pk, '2026-10-03']))
        self.assertEqual(self.person.days.count(), 3)
        row, = self.client.get(self.periods_url()).context['rows']
        self.assertEqual(row['period'].days, 4)                   # bridged, so still one period
        self.assertTrue(Audit.objects.filter(action='deleted_day').exists())

    def test_removing_a_whole_period_takes_its_spotting_days_too(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1), flow=Flow.SPOTTING)
        for i in range(1, 4):
            PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1) + timedelta(days=i))
        PeriodDay.objects.create(person=self.person, date=D(2026, 11, 5))   # a later period
        self.client.post(reverse('hibiscus_period_delete', args=[self.person.pk, '2026-10-02']))
        self.assertEqual([d.date for d in self.person.days.all()], [D(2026, 11, 5)])
        self.assertTrue(Audit.objects.filter(action='deleted_period').exists())

    def test_deleting_a_period_that_does_not_start_there_is_404(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 2))
        r = self.client.post(reverse('hibiscus_period_delete', args=[self.person.pk, '2026-10-09']))
        self.assertEqual(r.status_code, 404)

    def test_averages_appear_once_there_are_two_periods(self):
        for start in (D(2026, 8, 1), D(2026, 9, 2)):
            for i in range(4):
                PeriodDay.objects.create(person=self.person, date=start + timedelta(days=i))
        r = self.client.get(self.periods_url())
        self.assertEqual(r.context['stats'].avg_cycle, 32)
        self.assertEqual(r.context['stats'].avg_period, 4)
        self.assertContains(r, 'average cycle 32 days')

    def test_the_list_uses_the_module_defaults_for_a_prediction(self):
        """A person with one period and no history is predicted from the settings, not a constant."""
        config = Settings.load()
        config.default_cycle = 30
        config.save()
        for i in range(3):
            PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1) + timedelta(days=i))
        r = self.client.get(reverse('hibiscus_people'))
        self.assertContains(r, '31 Oct 2026')                      # 1 Oct + 30 days


class MagicLinkAdminTests(TestCase):
    """Making, replacing and switching off a link from someone's page."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person()

    def _make(self):
        return self.client.post(reverse('hibiscus_person_link', args=[self.person.pk, 'make']))

    def test_the_panel_offers_a_link_when_there_is_none(self):
        r = self.client.get(reverse('hibiscus_person', args=[self.person.pk]))
        self.assertContains(r, 'Magic link')
        self.assertContains(r, 'Create a link')

    def test_making_one_shows_the_whole_link_with_the_host(self):
        self._make()
        refreshed(self.person)
        r = self.client.get(reverse('hibiscus_person', args=[self.person.pk]))
        self.assertContains(r, f'http://testserver/hibiscus/k/{self.person.member.login_key}')
        self.assertContains(r, 'New link')
        self.assertContains(r, 'Switch it off')

    def test_making_one_records_it(self):
        self._make()
        refreshed(self.person)
        self.assertTrue(self.person.has_login_link)
        self.assertIsNotNone(self.person.member.login_key_made_at)
        row = Audit.objects.filter(person=self.person, action='made_login_link').first()
        self.assertEqual(row.actor, self.admin)
        self.assertEqual(row.detail, 'first link')

    def test_making_a_second_one_says_it_replaced_the_first(self):
        self._make()
        refreshed(self.person)
        first = self.person.member.login_key
        self._make()
        refreshed(self.person)
        self.assertNotEqual(self.person.member.login_key, first)
        self.assertEqual(Audit.objects.filter(person=self.person, action='made_login_link',
                                              detail='replaced the old link').count(), 1)

    def test_switching_it_off_clears_the_key(self):
        self._make()
        self.client.post(reverse('hibiscus_person_link', args=[self.person.pk, 'off']))
        refreshed(self.person)
        self.assertIsNone(self.person.member.login_key)
        self.assertTrue(Audit.objects.filter(person=self.person,
                                             action='removed_login_link').exists())

    def test_the_people_list_marks_who_has_one(self):
        r = self.client.get(reverse('hibiscus_people'))
        self.assertNotContains(r, 'Has a magic link')
        self._make()
        self.assertContains(self.client.get(reverse('hibiscus_people')), 'Has a magic link')

    def test_only_post_and_only_the_admin(self):
        url = reverse('hibiscus_person_link', args=[self.person.pk, 'make'])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(
            self.client.post(reverse('hibiscus_person_link',
                                     args=[self.person.pk, 'nonsense'])).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.post(url).status_code, 302)
        refreshed(self.person)
        self.assertIsNone(self.person.member.login_key)

class PersonTabsTests(TestCase):
    """Every page about a person shows the same four tabs.

    The Stocks tab had gone missing from the Periods page because the row was copied into each
    template by hand; it is one include now, and this is what keeps it that way.
    """

    TABS = ['Account', 'Periods', 'Stocks', 'Activity']

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person()
        PeriodDay.objects.create(person=self.person, date=D(2026, 9, 15), flow=Flow.MEDIUM)
        StockItem.objects.create(person=self.person, name='Pads', low_at=2)

    def test_all_four_tabs_on_every_person_page(self):
        stocks_url = reverse('hibiscus_person_stocks', args=[self.person.pk])
        for name in ['hibiscus_person', 'hibiscus_person_periods', 'hibiscus_person_stocks',
                     'hibiscus_person_activity']:
            body = self.client.get(reverse(name, args=[self.person.pk])).content.decode()
            for tab in self.TABS:
                self.assertIn(f'>{tab}', body, f'{tab} missing from {name}')
            if name != 'hibiscus_person_stocks':
                self.assertIn(stocks_url, body, f'Stocks not linked from {name}')

    def test_the_counts_are_real(self):
        body = self.client.get(reverse('hibiscus_person_periods',
                                       args=[self.person.pk])).content.decode()
        self.assertIn('Periods <span class="n">1</span>', body)
        self.assertIn('Stocks <span class="n">1</span>', body)

    def test_the_page_you_are_on_is_not_a_link(self):
        body = self.client.get(reverse('hibiscus_person_stocks',
                                       args=[self.person.pk])).content.decode()
        self.assertIn('<button type="button" aria-pressed="true">Stocks', body)


class FlowColourTests(TestCase):
    """The day chips carry the flow palette instead of the plain grey chip."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person()
        for offset, flow in enumerate([Flow.SPOTTING, Flow.MEDIUM, Flow.HEAVY, Flow.LIGHT]):
            PeriodDay.objects.create(person=self.person,
                                     date=D(2026, 9, 15) + timedelta(days=offset), flow=flow)

    def _periods(self):
        return self.client.get(reverse('hibiscus_person_periods',
                                       args=[self.person.pk])).content.decode()

    def test_each_day_gets_its_level_class(self):
        body = self._periods()
        for level in [1, 2, 3, 4]:
            self.assertIn(f'class="flow f{level}"', body)

    def test_the_palette_travels_with_the_page(self):
        """Inline, so a wrong /static/ mapping on a server cannot strip the colour off.

        That is not hypothetical: the first version of this was a collected stylesheet, and on
        the live server it 404'd and the pills rendered as plain blue links.
        """
        body = self._periods()
        self.assertNotIn('admin.css', body)              # nothing to collect, nothing to lose
        self.assertIn('<style>', body)
        for level in [1, 2, 3, 4]:
            self.assertIn(f'.pdx .flow.f{level}', body)
        self.assertIn('data-theme="dark"', body)         # readable on the dark console too
        self.assertIn('class="card pdx"', body)          # the styles are scoped to that card

    def test_a_bridged_day_is_marked_apart(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 9, 20), flow=Flow.MEDIUM)
        self.assertIn('class="flow gap"', self._periods())

    def test_there_is_a_key_under_the_table(self):
        body = self._periods()
        self.assertIn('flow-key', body)
        for word in ['spotting', 'light', 'medium', 'heavy', 'not recorded']:
            self.assertIn(word, body)


class StockItemEditTests(TestCase):
    """The admin can rename an item and change the level that makes it count as low."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person()
        self.item = StockItem.objects.create(person=self.person, name='Pads', low_at=2)
        self.url = reverse('hibiscus_item_edit', args=[self.person.pk, self.item.pk])

    def test_the_form_opens_with_the_current_values(self):
        r = self.client.get(self.url)
        self.assertContains(r, 'value="Pads"')
        self.assertContains(r, 'Low at')

    def test_changing_the_low_level(self):
        r = self.client.post(self.url, {'name': 'Pads', 'low_at': 5}, follow=True)
        self.item.refresh_from_db()
        self.assertEqual(self.item.low_at, 5)
        self.assertContains(r, 'Pads saved.')
        self.assertTrue(Audit.objects.filter(person=self.person, action='edited_item',
                                             detail='low at 2 → 5').exists())

    def test_renaming_keeps_the_ledger(self):
        StockMove.objects.create(item=self.item, date=D(2026, 9, 1), change=10,
                                 reason=StockMove.Reason.OPENING)
        self.client.post(self.url, {'name': 'Sofy', 'low_at': 2})
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Sofy')
        self.assertEqual(self.item.in_stock, 10)
        self.assertTrue(Audit.objects.filter(person=self.person,
                                             detail='Pads → Sofy').exists())

    def test_a_new_low_level_changes_what_counts_as_low(self):
        StockMove.objects.create(item=self.item, date=D(2026, 9, 1), change=3,
                                 reason=StockMove.Reason.OPENING)
        self.assertFalse(StockItem.objects.get(pk=self.item.pk).is_low)
        self.client.post(self.url, {'name': 'Pads', 'low_at': 3})
        self.assertTrue(StockItem.objects.get(pk=self.item.pk).is_low)

    def test_the_count_is_not_editable_here(self):
        r = self.client.get(self.url)
        self.assertNotContains(r, 'name="opening"')
        self.assertContains(r, 'The count is not edited here')

    def test_the_stocks_table_links_to_it(self):
        self.assertContains(self.client.get(
            reverse('hibiscus_person_stocks', args=[self.person.pk])), self.url)

    def test_someone_elses_item_is_a_404(self):
        other = make_person('Meena', 'm@example.com')
        self.assertEqual(self.client.get(
            reverse('hibiscus_item_edit', args=[other.pk, self.item.pk])).status_code, 404)

    def test_it_needs_the_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
        self.assertEqual(self.client.post(self.url, {'name': 'X', 'low_at': 1}).status_code, 302)
        self.item.refresh_from_db()
        self.assertEqual(self.item.name, 'Pads')

class FakeSyncUp:
    """Stands in for SyncUp's partner endpoint and keeps what was posted to it."""

    def __init__(self, delivered=1, raises=None):
        self.delivered = delivered
        self.raises = raises
        self.url = None
        self.payload = None

    def __call__(self, request, timeout=None):
        self.url = request.full_url
        self.payload = json.loads(request.data)
        if self.raises:
            raise self.raises
        fake = self

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({'delivered': fake.delivered}).encode()

        return Response()


class TestPushTests(TestCase):
    """The Test push button: one real notification, now, and the answer as SyncUp gave it.

    Everything about push looks fine until something is actually delivered — the token, the
    address and the key are each only proved by a send — so the button exists to prove it and
    the messages have to be specific about which of the three is missing.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person(name='Riya', email='r@example.com', syncup_token='tok-123')
        self.url = reverse('hibiscus_person_test_push', args=[self.person.pk])
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.syncup_notify_key = 'secret-key'
        hub.site_base = ''
        hub.save()

    def _send(self, fake):
        with patch('core.push.urllib.request.urlopen', fake):
            return self.client.post(self.url, follow=True)

    # ---- the button
    def test_the_button_is_there_when_a_token_is(self):
        r = self.client.get(reverse('hibiscus_person', args=[self.person.pk]))
        self.assertContains(r, 'Test push')
        self.assertContains(r, self.url)

    def test_no_token_means_no_button_but_an_explanation(self):
        self.person.member.syncup_token = ''
        self.person.member.save(update_fields=['syncup_token'])
        r = self.client.get(reverse('hibiscus_person', args=[self.person.pk]))
        self.assertNotContains(r, 'Test push')
        self.assertContains(r, 'No token yet')
        self.assertContains(r, 'inside the SyncUp app')

    # ---- what actually goes out
    def test_it_posts_a_real_message_to_syncups_endpoint(self):
        fake = FakeSyncUp(delivered=2)
        r = self._send(fake)
        self.assertEqual(fake.url, 'https://syncup.example.com/app/v1/partner/notify')
        self.assertEqual(fake.payload['token'], 'tok-123')
        self.assertEqual(fake.payload['key'], 'secret-key')
        self.assertIn('test message', fake.payload['title'])
        self.assertIn('notifications are working', fake.payload['body'])
        self.assertContains(r, 'delivered it to 2 devices')

    def test_the_notification_opens_the_diary_when_tapped(self):
        fake = FakeSyncUp()
        self._send(fake)
        self.assertTrue(fake.payload['url'].endswith(reverse('hibiscus_me_home')),
                        fake.payload['url'])

    def test_the_first_test_remembers_this_sites_address_for_the_whole_hub(self):
        self.assertEqual(HubSettings.load().site_base, '')
        self._send(FakeSyncUp())
        self.assertEqual(HubSettings.load().site_base, 'http://testserver')

    def test_an_address_already_set_is_left_alone(self):
        hub = HubSettings.load()
        hub.site_base = 'https://synchub.example.com'
        hub.save()
        self._send(FakeSyncUp())
        self.assertEqual(HubSettings.load().site_base, 'https://synchub.example.com')

    # ---- every way it can go wrong says which one it was
    def test_no_syncup_address_yet(self):
        hub = HubSettings.load()
        hub.syncup_base = ''
        hub.save()
        fake = FakeSyncUp()
        r = self._send(fake)
        self.assertIsNone(fake.url)                      # nothing was posted anywhere
        self.assertContains(r, 'No SyncUp address yet')

    def test_the_person_has_no_token(self):
        self.person.member.syncup_token = ''
        self.person.member.save(update_fields=['syncup_token'])
        fake = FakeSyncUp()
        r = self._send(fake)
        self.assertIsNone(fake.url)
        self.assertContains(r, 'no device token yet')

    def test_syncup_refusing_it_is_shown_verbatim(self):
        r = self._send(FakeSyncUp(raises=urllib.error.URLError('connection refused')))
        self.assertContains(r, 'SyncUp refused it')
        self.assertContains(r, 'connection refused')

    def test_accepted_but_delivered_to_nothing(self):
        r = self._send(FakeSyncUp(delivered=0))
        self.assertContains(r, 'delivered it to no device')
        self.assertContains(r, 'token is probably stale')

    # ---- the record
    def test_every_test_is_audited_with_its_outcome(self):
        self._send(FakeSyncUp(delivered=3))
        row = Audit.objects.filter(person=self.person, action='test_push').first()
        self.assertEqual(row.actor, self.admin)
        self.assertEqual(row.detail, '3 device(s)')

    def test_a_test_does_not_use_up_a_days_real_message(self):
        self._send(FakeSyncUp())
        self.assertFalse(NotifySent.objects.exists())

    # ---- access
    def test_post_only_and_admin_only(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.client.logout()
        fake = FakeSyncUp()
        with patch('core.push.urllib.request.urlopen', fake):
            self.assertEqual(self.client.post(self.url).status_code, 302)
        self.assertIsNone(fake.url)


class DiaryLinkTests(TestCase):
    """The link a notification opens — the cron's one blind spot until now."""

    def test_no_address_means_no_link(self):
        hub = HubSettings.load()
        hub.site_base = ''
        hub.save()
        self.assertEqual(push.diary_url(), '')

    def test_the_link_points_at_the_diary(self):
        hub = HubSettings.load()
        hub.site_base = 'https://synchub.example.com/'
        hub.save()
        self.assertEqual(push.diary_url(),
                         'https://synchub.example.com' + reverse('hibiscus_me_home'))

    def test_the_cron_sends_that_link_with_every_message(self):
        config = Settings.load()
        hub = HubSettings.load()
        hub.site_base = 'https://synchub.example.com'
        hub.syncup_base = 'https://syncup.example.com'
        hub.save()
        defaults.ensure_slots()
        person = make_person(name='Riya', email='r@example.com', syncup_token='tok-123')
        PeriodDay.objects.create(person=person, date=config.today, flow=Flow.MEDIUM)
        slot = NotifySlot.objects.filter(is_enabled=True).first()
        fake = FakeSyncUp()
        with patch('core.push.urllib.request.urlopen', fake):
            push.run_slot(slot, config, base_url=push.diary_url(config))
        self.assertIsNotNone(fake.payload, 'nothing was sent at all')
        self.assertEqual(fake.payload['url'],
                         'https://synchub.example.com' + reverse('hibiscus_me_home'))

class SyncUpAddressTests(TestCase):
    """The SyncUp address pointing at this same site — the one wrong value that hangs.

    It does not fail: the server is asked to call itself while it is busy answering the page that
    asked, so nothing answers until the timeout, and the trail fills with TimeoutError. Both the
    Test push and the settings page name it instead.
    """

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = make_person(name='Riya', email='r@example.com', syncup_token='tok-123')
        self.url = reverse('hibiscus_person_test_push', args=[self.person.pk])

    def _set(self, address):
        hub = HubSettings.load()
        hub.syncup_base = address
        hub.syncup_notify_key = 'k'
        hub.save()

    def test_a_self_pointing_address_is_refused_before_anything_is_sent(self):
        self._set('https://testserver')
        fake = FakeSyncUp()
        with patch('core.push.urllib.request.urlopen', fake):
            r = self.client.post(self.url, follow=True)
        self.assertIsNone(fake.url, 'it tried to send anyway')
        self.assertContains(r, 'this same site')
        self.assertTrue(Audit.objects.filter(person=self.person, action='test_push',
                                             detail='syncup address points at this site').exists())

    def test_the_settings_page_says_so_too(self):
        self._set('https://testserver/')
        self.assertContains(self.client.get(reverse('hibiscus_settings')),
                            'The SyncUp address is this same site')

    def test_a_real_address_is_left_alone(self):
        self._set('https://syncup.example.com')
        self.assertNotContains(self.client.get(reverse('hibiscus_settings')),
                               'The SyncUp address is this same site')
        fake = FakeSyncUp()
        with patch('core.push.urllib.request.urlopen', fake):
            self.client.post(self.url, follow=True)
        self.assertEqual(fake.url, 'https://syncup.example.com/app/v1/partner/notify')

    def test_a_timeout_explains_itself(self):
        self._set('https://syncup.example.com')
        r = self._timeout()
        self.assertContains(r, 'No answer from https://syncup.example.com')
        self.assertContains(r, 'whitelist')

    def _timeout(self):
        fake = FakeSyncUp(raises=TimeoutError('The read operation timed out'))
        with patch('core.push.urllib.request.urlopen', fake):
            return self.client.post(self.url, follow=True)

    def test_the_manual_test_waits_longer_than_the_cron(self):
        self.assertGreater(push.TEST_TIMEOUT, push.SEND_TIMEOUT)
        self._set('https://syncup.example.com')
        seen = {}

        def capture(request, timeout=None):
            seen['timeout'] = timeout
            raise TimeoutError('The read operation timed out')

        with patch('core.push.urllib.request.urlopen', capture):
            self.client.post(self.url)
        self.assertEqual(seen['timeout'], push.TEST_TIMEOUT)
