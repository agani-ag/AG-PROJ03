"""Hibiscus tests.

The five worked examples in the mockup (section 3) are the first five tests here — they are the
agreed definition of the rules, so they belong in code where they can fail.
"""
from datetime import date, timedelta

from django.contrib.auth.models import User
from django.db import IntegrityError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import auth, cycle
from .models import Audit, Flow, Person, PeriodDay, Settings, StockItem, StockMove

D = date


def days(*pairs):
    """Shorthand: days(('2026-09-15', Flow.MEDIUM), ...) → [(date, flow), ...]"""
    return [(D.fromisoformat(d), f) for d, f in pairs]


def run_of(start, length, flow=Flow.MEDIUM):
    first = D.fromisoformat(start)
    return [(first + timedelta(days=i), flow) for i in range(length)]


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
        self.person = Person.objects.create(name='Divya', email='d@example.com')

    def test_one_row_per_day_per_person(self):
        PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))
        with self.assertRaises(IntegrityError):
            PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1))

    def test_two_people_can_log_the_same_day(self):
        other = Person.objects.create(name='Meena', phone='9876543210')
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
        self.person = Person.objects.create(name='Divya', email='d@example.com')

    def test_generated_passwords_avoid_look_alike_characters(self):
        for _ in range(20):
            pw = auth.generate_password()
            self.assertEqual(len(pw), auth.PASSWORD_LENGTH)
            self.assertFalse(set(pw) & set('ol01iI'))

    def test_password_is_stored_hashed_and_checks_out(self):
        auth.set_password(self.person, 'swan7tide')
        self.person.refresh_from_db()
        self.assertNotIn('swan7tide', self.person.password_hash)
        self.assertTrue(auth.check_person_password(self.person, 'swan7tide'))
        self.assertFalse(auth.check_person_password(self.person, 'wrong'))

    def test_no_password_set_means_no_sign_in(self):
        self.assertFalse(auth.check_person_password(self.person, ''))

    def test_people_are_found_by_email_or_phone(self):
        other = Person.objects.create(name='Meena', phone='9876543210')
        self.assertEqual(auth.find_person('D@Example.com'), self.person)
        self.assertEqual(auth.find_person('9876543210'), other)
        self.assertEqual(auth.find_person('+91 98765 43210'), other)   # typed with spaces
        self.assertIsNone(auth.find_person('nobody@example.com'))
        self.assertIsNone(auth.find_person(''))


class AdminSideTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.person = Person.objects.create(name='Divya', email='d@example.com')

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
        self.assertTrue(meena.password_hash)
        shown = r.context['password']
        self.assertTrue(auth.check_person_password(meena, shown))      # the one on screen works
        self.assertNotContains(r, meena.password_hash)                 # the hash is not on screen

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
        self.person.refresh_from_db()
        self.assertFalse(auth.check_person_password(self.person, 'old7pass'))
        self.assertTrue(auth.check_person_password(self.person, r.context['password']))

    def test_suspend_and_resume(self):
        self.client.post(reverse('hibiscus_person_state', args=[self.person.pk, 'suspend']))
        self.person.refresh_from_db()
        self.assertFalse(self.person.is_active)
        self.client.post(reverse('hibiscus_person_state', args=[self.person.pk, 'resume']))
        self.person.refresh_from_db()
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
        })
        self.assertEqual(r.status_code, 302)
        config = Settings.load()
        self.assertEqual(config.default_cycle, 30)
        self.assertEqual(config.updated_by, self.admin)
        self.assertTrue(Audit.objects.filter(action='changed_settings').exists())

    def test_the_list_uses_the_module_defaults_for_a_prediction(self):
        """A person with one period and no history is predicted from the settings, not a constant."""
        config = Settings.load()
        config.default_cycle = 30
        config.save()
        for i in range(3):
            PeriodDay.objects.create(person=self.person, date=D(2026, 10, 1) + timedelta(days=i))
        r = self.client.get(reverse('hibiscus_people'))
        self.assertContains(r, '31 Oct 2026')                      # 1 Oct + 30 days
