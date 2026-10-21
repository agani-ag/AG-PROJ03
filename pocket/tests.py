"""Pocket Money — the sums, and who is allowed to see them.

Three things carry the module and are tested hardest: the signed arithmetic, the mirror that
lets two people read one row from opposite sides, and `service.access`. Everything else is a
thin view over those.
"""
import json
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core import cronhub
from core.models import HubSettings

from core import auth as hub
from core.models import Member

from . import auth, push, service
from .cron import notify, retention
from .models import (MIRROR, PERSON_SIGN, Account, EntryLog, Flow, Folder, Kind, LedgerEntry,
                     Person, Settings, TrackerEntry, clean_amount, validate_upi)

D = Decimal


def make_account(name='Anand', sign_in_id=None, password='pw-for-tests'):
    """A hub member who has started Pocket Money — which is what an account is now."""
    sign_in_id = sign_in_id or f'{name.lower().replace(" ", "")}@example.com'
    email, phone = hub.split_sign_in_id(sign_in_id)
    member = Member.objects.create(name=name, email=email or None, phone=phone or None)
    hub.set_password(member, password)
    return service.join(member)


def sign_in(client, sign_in_id, password='pw-for-tests'):
    """Through the hub's one door. Signs out first: the sign-in page redirects somebody who is
    already in rather than swapping accounts under them."""
    client.post(reverse('member_logout'))
    return client.post(reverse('member_login'),
                       {'sign_in_id': sign_in_id, 'password': password})


def make_folder(owner, name='Office'):
    return Folder.objects.create(owner=owner, name=name)


def make_person(folder, name='Mani', **fields):
    acc = service.access(folder.owner, folder)
    return service.add_person(acc, name, **fields)


def entry(folder, person, kind, amount, **fields):
    acc = service.access(folder.owner, folder)
    return service.add_ledger(acc, person, kind, amount, **fields)


class FakeSyncUp:
    """Stands in for SyncUp's partner endpoint and keeps what was posted to it."""

    def __init__(self, delivered=1):
        self.delivered = delivered
        self.payloads = []

    def __call__(self, request, timeout=None):
        self.payloads.append(json.loads(request.data))
        fake = self

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return json.dumps({'delivered': fake.delivered}).encode()

        return Response()

    @property
    def payload(self):
        return self.payloads[-1] if self.payloads else None


class RunInline:
    """A Thread that has already run by the time start() returns."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self.target, self.args, self.kwargs = target, args, kwargs or {}

    def start(self):
        self.target(*self.args, **self.kwargs)


def _ctx(kind='tick', seconds=8):
    import time
    return cronhub.Context(kind=kind, deadline=time.monotonic() + seconds)


# --------------------------------------------------------------------------- #
class JoiningTests(TestCase):
    """Identity moved to the hub, and core tests it. What is left here is what *joining* does."""

    def _member(self, name='Anand', email='anand@example.com', phone=None):
        member = Member.objects.create(name=name, email=email, phone=phone)
        hub.set_password(member, 'pw-for-tests')
        return member

    def test_joining_needs_nothing_and_brings_the_categories(self):
        member = self._member()
        account = service.join(member)
        self.assertEqual(account.member, member)
        self.assertEqual(account.name, 'Anand')
        self.assertTrue(account.categories.filter(flow=Flow.IN, name='Salary').exists())
        self.assertTrue(account.categories.filter(flow=Flow.LEDGER, name='Chit').exists())

    def test_joining_twice_gives_back_the_same_books(self):
        member = self._member()
        self.assertEqual(service.join(member), service.join(member))
        self.assertEqual(Account.objects.count(), 1)

    def test_the_module_keeps_no_password_of_its_own(self):
        account = service.join(self._member())
        self.assertEqual(account.password_hash, '')
        self.assertIsNone(account.email, 'the email lives on the member now')

    def test_one_person_one_set_of_books(self):
        member = self._member()
        service.join(member)
        with self.assertRaises(Exception):
            Account.objects.create(member=member, name='Second')


# --------------------------------------------------------------------------- #
class TrackerTests(TestCase):
    """Half one: in, out, and typed categories."""

    def setUp(self):
        self.account = make_account()
        self.food = self.account.categories.get(flow=Flow.OUT, name='Food')
        self.salary = self.account.categories.get(flow=Flow.IN, name='Salary')

    def test_in_and_out_and_what_is_left(self):
        service.add_tracker(self.account, Flow.IN, '18600', category=self.salary)
        service.add_tracker(self.account, Flow.OUT, '16200', category=self.food)
        today = Settings.load().today()
        month = service.tracker_month(self.account, today.year, today.month)
        self.assertEqual(month['in'], D('18600.00'))
        self.assertEqual(month['out'], D('16200.00'))
        self.assertEqual(month['kept'], D('2400.00'))

    def test_a_category_cannot_cross_the_flow_it_belongs_to(self):
        with self.assertRaises(ValidationError):
            service.add_tracker(self.account, Flow.OUT, '100', category=self.salary)

    def test_an_amount_takes_what_a_person_types(self):
        self.assertEqual(clean_amount('1,200.50'), D('1200.50'))
        self.assertEqual(clean_amount('₹300'), D('300.00'))
        self.assertEqual(clean_amount(' 1200 '), D('1200.00'))
        for bad in ('', 'abc', '0', '-5'):
            with self.assertRaises(ValidationError):
                clean_amount(bad)

    def test_a_voided_entry_leaves_the_sums_and_stays_visible(self):
        kept = service.add_tracker(self.account, Flow.OUT, '800', category=self.food)
        service.void_tracker(self.account, kept, 'typed twice')
        today = Settings.load().today()
        self.assertEqual(service.tracker_month(self.account, today.year, today.month)['out'],
                         D('0.00'))
        self.assertEqual(TrackerEntry.objects.count(), 1)
        self.assertTrue(TrackerEntry.objects.get().is_void)

    def test_the_breakdown_is_biggest_first(self):
        fuel = service.add_category(self.account, Flow.OUT, 'Fuel X')
        service.add_tracker(self.account, Flow.OUT, '500', category=fuel)
        service.add_tracker(self.account, Flow.OUT, '6840', category=self.food)
        today = Settings.load().today()
        bars = service.tracker_month(self.account, today.year, today.month)['out_bars']
        self.assertEqual(bars[0]['name'], 'Food')
        self.assertEqual(bars[0]['percent'], 100)
        self.assertLess(bars[1]['percent'], 100)

    def test_another_month_is_not_this_month(self):
        old = Settings.load().today().replace(day=1) - timedelta(days=20)
        service.add_tracker(self.account, Flow.IN, '1000', category=self.salary, date=old)
        today = Settings.load().today()
        self.assertEqual(service.tracker_month(self.account, today.year, today.month)['in'],
                         D('0.00'))
        self.assertEqual(service.tracker_month(self.account, old.year, old.month)['in'],
                         D('1000.00'))

    def test_a_category_in_use_is_archived_never_deleted(self):
        service.add_tracker(self.account, Flow.OUT, '100', category=self.food)
        self.food.is_archived = True
        self.food.save()
        self.assertNotIn(self.food, service.categories(self.account, Flow.OUT))
        self.assertEqual(TrackerEntry.objects.get().category, self.food)


# --------------------------------------------------------------------------- #
class LedgerArithmeticTests(TestCase):
    """The four kinds, the one signed number, and the mirror."""

    def setUp(self):
        self.anand = make_account()
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)

    def test_every_kind_moves_the_balance_the_way_the_table_says(self):
        self.assertEqual(PERSON_SIGN[Kind.LENT], 1)
        self.assertEqual(PERSON_SIGN[Kind.COLLECTED], -1)
        self.assertEqual(PERSON_SIGN[Kind.BORROWED], -1)
        self.assertEqual(PERSON_SIGN[Kind.REPAID], 1)

    def test_lending_and_collecting(self):
        entry(self.folder, self.mani, Kind.LENT, '2000')
        self.assertEqual(service.person_balance(self.mani), D('2000.00'))
        entry(self.folder, self.mani, Kind.COLLECTED, '500')
        self.assertEqual(service.person_balance(self.mani), D('1500.00'))

    def test_borrowing_points_the_other_way(self):
        entry(self.folder, self.mani, Kind.BORROWED, '5000')
        self.assertEqual(service.person_balance(self.mani), D('-5000.00'))
        entry(self.folder, self.mani, Kind.REPAID, '1000')
        self.assertEqual(service.person_balance(self.mani), D('-4000.00'))

    def test_the_mirror_is_its_own_opposite(self):
        for kind in Kind:
            self.assertEqual(MIRROR[MIRROR[kind]], kind)

    def test_a_voided_entry_is_out_of_the_balance_but_still_there(self):
        kept = entry(self.folder, self.mani, Kind.LENT, '2000')
        acc = service.access(self.anand, self.folder)
        service.void_ledger(acc, kept, 'wrong person')
        self.assertEqual(service.person_balance(self.mani), D('0.00'))
        self.assertEqual(LedgerEntry.objects.count(), 1)

    def test_folder_totals_keep_the_two_directions_apart(self):
        riya = make_person(self.folder, 'Riya')
        entry(self.folder, self.mani, Kind.LENT, '4500')
        entry(self.folder, riya, Kind.BORROWED, '2000')
        totals = service.folder_totals(self.folder)
        self.assertEqual(totals['owed_to_you'], D('4500.00'))
        self.assertEqual(totals['you_owe'], D('2000.00'))

    def test_a_position_counts_folders_you_own_and_no_others(self):
        entry(self.folder, self.mani, Kind.LENT, '1000')
        other = make_account('Suresh')
        their_folder = make_folder(other, 'Shop')
        their_person = make_person(their_folder, 'Vinod')
        entry(their_folder, their_person, Kind.LENT, '9999')
        self.assertEqual(service.account_position(self.anand)['owed_to_you'], D('1000.00'))

    def test_settling_up_takes_it_to_zero_either_way(self):
        acc = service.access(self.anand, self.folder)
        entry(self.folder, self.mani, Kind.LENT, '4500')
        service.settle_up(acc, self.mani)
        self.assertEqual(service.person_balance(self.mani), D('0.00'))

        riya = make_person(self.folder, 'Riya')
        entry(self.folder, riya, Kind.BORROWED, '2000')
        service.settle_up(acc, riya)
        self.assertEqual(service.person_balance(riya), D('0.00'))

    def test_settling_nothing_writes_nothing(self):
        acc = service.access(self.anand, self.folder)
        self.assertIsNone(service.settle_up(acc, self.mani))
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_the_statement_splits_by_which_way_the_cash_moved(self):
        entry(self.folder, self.mani, Kind.LENT, '2000')
        entry(self.folder, self.mani, Kind.COLLECTED, '500')
        page = service.statement(self.mani)
        self.assertEqual(page['gave'], D('2000.00'))
        self.assertEqual(page['got'], D('500.00'))
        self.assertTrue(page['they_owe'])

    def test_the_same_statement_mirrored_is_the_other_way_round(self):
        entry(self.folder, self.mani, Kind.LENT, '2000')
        entry(self.folder, self.mani, Kind.COLLECTED, '500')
        page = service.statement(self.mani, mirrored=True)
        self.assertEqual(page['gave'], D('500.00'))      # what Mani paid out
        self.assertEqual(page['got'], D('2000.00'))      # what Mani took in
        self.assertEqual(page['balance'], D('-1500.00'))
        self.assertTrue(page['you_owe'])

    def test_a_person_belongs_to_one_folder(self):
        second = make_folder(self.anand, 'Family')
        acc = service.access(self.anand, second)
        with self.assertRaises(ValidationError):
            service.add_ledger(acc, self.mani, Kind.LENT, '100')


# --------------------------------------------------------------------------- #
class AccessTests(TestCase):
    """The whole security surface, which is one function."""

    def setUp(self):
        self.anand = make_account()
        self.divya = make_account('Divya', 'divya@example.com')
        self.stranger = make_account('Stranger', 'stranger@example.com')
        self.folder = make_folder(self.anand)
        self.mani_account = make_account('Mani', 'mani@example.com')
        self.mani = make_person(self.folder)

    def test_the_owner_may_do_everything(self):
        acc = service.access(self.anand, self.folder)
        self.assertEqual(acc.role, 'owner')
        self.assertTrue(acc.can_write and acc.can_manage and acc.whole_folder)
        self.assertFalse(acc.mirrored)

    def test_a_stranger_gets_nothing(self):
        self.assertIsNone(service.access(self.stranger, self.folder))

    def test_a_share_is_only_real_once_it_is_accepted(self):
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        self.assertIsNone(service.access(self.divya, self.folder))
        service.accept_share(share, self.divya)
        acc = service.access(self.divya, self.folder)
        self.assertEqual(acc.role, 'editor')
        self.assertTrue(acc.can_write)
        self.assertFalse(acc.can_manage)

    def test_a_view_only_share_cannot_write(self):
        share = service.share_folder(self.folder, can_edit=False, by=self.anand)
        service.accept_share(share, self.divya)
        acc = service.access(self.divya, self.folder)
        self.assertEqual(acc.role, 'viewer')
        self.assertFalse(acc.can_write)
        with self.assertRaises(ValidationError):
            service.add_ledger(acc, self.mani, Kind.LENT, '100')

    def test_a_linked_person_reaches_their_own_line_and_no_other(self):
        riya = make_person(self.folder, 'Riya')
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)

        mine = service.access(self.mani_account, self.folder, self.mani)
        self.assertEqual(mine.role, 'person')
        self.assertTrue(mine.mirrored and mine.can_write)
        self.assertFalse(mine.whole_folder or mine.can_manage)
        self.assertIsNone(service.access(self.mani_account, self.folder, riya))

    def test_a_linked_person_without_permission_can_only_read(self):
        service.invite_person(self.mani, can_edit=False, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        acc = service.access(self.mani_account, self.folder, self.mani)
        self.assertFalse(acc.can_write)
        with self.assertRaises(ValidationError):
            service.add_ledger(acc, self.mani, Kind.REPAID, '100')

    def test_a_linked_person_may_add_but_never_void(self):
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        theirs = entry(self.folder, self.mani, Kind.LENT, '2000')
        acc = service.access(self.mani_account, self.folder, self.mani)
        with self.assertRaises(ValidationError):
            service.void_ledger(acc, theirs)

    def test_cutting_the_link_keeps_the_entries(self):
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        entry(self.folder, self.mani, Kind.LENT, '2000')
        service.unlink_person(self.mani, by=self.anand)
        self.assertIsNone(service.access(self.mani_account, self.folder, self.mani))
        self.assertEqual(LedgerEntry.objects.count(), 1)


class MirrorWriteTests(TestCase):
    """What a linked person taps, and what the owner's book ends up holding."""

    def setUp(self):
        self.anand = make_account()
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)
        self.mani_account = make_account('Mani', 'mani@example.com')
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        self.mine = service.access(self.mani_account, self.folder, self.mani)

    def test_paid_back_from_their_side_is_collected_in_the_owners_book(self):
        service.add_ledger(self.mine, self.mani, Kind.REPAID, '500')
        self.assertEqual(LedgerEntry.objects.get().kind, Kind.COLLECTED)

    def test_i_took_from_their_side_is_lent_in_the_owners_book(self):
        service.add_ledger(self.mine, self.mani, Kind.BORROWED, '2000')
        self.assertEqual(LedgerEntry.objects.get().kind, Kind.LENT)

    def test_both_sides_agree_about_the_figure(self):
        entry(self.folder, self.mani, Kind.LENT, '2000')
        service.add_ledger(self.mine, self.mani, Kind.REPAID, '500')
        owner_side = service.statement(self.mani)
        their_side = service.statement(self.mani, mirrored=True)
        self.assertEqual(owner_side['balance'], D('1500.00'))
        self.assertEqual(their_side['balance'], D('-1500.00'))

    def test_the_row_says_who_wrote_it(self):
        service.add_ledger(self.mine, self.mani, Kind.REPAID, '500')
        self.assertEqual(LedgerEntry.objects.get().created_by, self.mani_account)


# --------------------------------------------------------------------------- #
class PromotionTests(TestCase):
    """A person becomes a user. Two confirmations, always."""

    def setUp(self):
        self.anand = make_account()
        self.folder = make_folder(self.anand)

    def test_a_matching_phone_number_only_suggests(self):
        person = make_person(self.folder, 'Mani', phone='98765 43210')
        mani = make_account('Mani', '9876543210')
        person.refresh_from_db()
        self.assertEqual(person.suggested_account, mani)
        self.assertIsNone(person.linked_account)
        self.assertIsNone(service.access(mani, self.folder, person))

    def test_a_suggestion_never_appears_for_your_own_folder(self):
        make_person(self.folder, 'Me again', phone='9000000001')
        same = make_account('Other', '9000000001')
        self.assertFalse(service.suggestions_for(same).exists())

    def test_an_invitation_needs_the_other_side_to_accept(self):
        person = make_person(self.folder, 'Mani')
        code = service.invite_person(person, can_edit=True, by=self.anand)
        mani = make_account('Mani', 'mani@example.com')
        self.assertIsNone(service.access(mani, self.folder, person))
        service.accept_person_invite(service.person_by_code(code), mani)
        self.assertIsNotNone(service.access(mani, self.folder, person))

    def test_a_code_works_once(self):
        person = make_person(self.folder, 'Mani')
        code = service.invite_person(person, by=self.anand)
        service.accept_person_invite(service.person_by_code(code), make_account('Mani', 'm@e.com'))
        self.assertIsNone(service.person_by_code(code))

    def test_an_expired_code_is_refused(self):
        person = make_person(self.folder, 'Mani')
        code = service.invite_person(person, by=self.anand)
        Person.objects.filter(pk=person.pk).update(
            link_made_at=timezone.now() - timedelta(days=30))
        self.assertIsNone(service.person_by_code(code))

    def test_you_cannot_accept_your_own_folders_invitation(self):
        person = make_person(self.folder, 'Mani')
        service.invite_person(person, by=self.anand)
        with self.assertRaises(ValidationError):
            service.accept_person_invite(person, self.anand)

    def test_declining_closes_it_without_sharing_anything(self):
        person = make_person(self.folder, 'Mani')
        code = service.invite_person(person, by=self.anand)
        mani = make_account('Mani', 'mani@example.com')
        service.decline_person_invite(service.person_by_code(code), mani)
        person.refresh_from_db()
        self.assertIsNone(person.linked_account)
        self.assertIsNone(service.person_by_code(code))

    def test_the_two_records_of_the_same_human_stay_apart(self):
        """Mani is in Anand's folder, and Anand is in Mani's. Two rows, two balances."""
        person = make_person(self.folder, 'Mani')
        code = service.invite_person(person, by=self.anand)
        mani = make_account('Mani', 'mani@example.com')
        service.accept_person_invite(service.person_by_code(code), mani)
        entry(self.folder, person, Kind.LENT, '2000')

        their_folder = make_folder(mani, 'Friends')
        their_anand = make_person(their_folder, 'Anand')
        entry(their_folder, their_anand, Kind.LENT, '75')

        self.assertEqual(service.person_balance(person), D('2000.00'))
        self.assertEqual(service.person_balance(their_anand), D('75.00'))
        self.assertIsNone(service.access(self.anand, their_folder, their_anand))


class SharingTests(TestCase):
    def setUp(self):
        self.anand = make_account()
        self.divya = make_account('Divya', 'divya@example.com')
        self.folder = make_folder(self.anand)

    def test_a_share_link_works_before_the_other_person_has_an_account(self):
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        found = service.share_by_code(share.code)
        self.assertEqual(found, share)
        self.assertIsNone(found.account)

    def test_a_share_cannot_be_taken_twice(self):
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, self.divya)
        again = service.share_folder(self.folder, can_edit=True, by=self.anand)
        with self.assertRaises(ValidationError):
            service.accept_share(again, self.divya)

    def test_stopping_a_share_closes_the_door(self):
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, self.divya)
        service.stop_share(share, by=self.anand)
        self.assertIsNone(service.access(self.divya, self.folder))

    def test_an_editor_cannot_re_share_or_invite(self):
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, self.divya)
        self.assertFalse(service.access(self.divya, self.folder).can_manage)


# --------------------------------------------------------------------------- #
class UpiTests(TestCase):
    """A redirect, and a QR that gives up only its ID."""

    def test_the_link_carries_the_payee_and_no_amount(self):
        link = service.upi_link('riya@okaxis', 'Riya')
        self.assertIn('pa=riya%40okaxis', link)
        self.assertIn('pn=Riya', link)
        self.assertIn('cu=INR', link)
        self.assertNotIn('am=', link)
        self.assertNotIn('tr=', link)

    def test_no_id_no_link(self):
        self.assertEqual(service.upi_link('', 'Riya'), '')

    def test_a_scanned_qr_gives_up_its_id(self):
        upi, name = service.parse_upi('upi://pay?pa=riya@okaxis&pn=RIYA%20S&am=500&cu=INR')
        self.assertEqual(upi, 'riya@okaxis')
        self.assertEqual(name, 'RIYA S')

    def test_an_amount_printed_in_a_qr_is_dropped(self):
        upi, _ = service.parse_upi('upi://pay?pa=shop@okhdfcbank&am=499.00&cu=INR')
        self.assertEqual(upi, 'shop@okhdfcbank')
        self.assertNotIn('499', service.upi_link(upi, ''))

    def test_a_plain_vpa_in_a_qr_is_taken_as_is(self):
        self.assertEqual(service.parse_upi('riya@okaxis')[0], 'riya@okaxis')

    def test_something_that_is_not_a_upi_code_is_refused(self):
        with self.assertRaises(ValidationError):
            service.parse_upi('https://example.com/hello')

    def test_validation_is_light_but_not_absent(self):
        self.assertEqual(validate_upi(' anand@okicici '), 'anand@okicici')
        self.assertEqual(validate_upi(''), '')
        for bad in ('anand', 'anand@', '@bank', 'a b@bank'):
            with self.assertRaises(ValidationError):
                validate_upi(bad)


# --------------------------------------------------------------------------- #
class PushTests(TestCase):
    """Who hears about an entry, and how often."""

    def setUp(self):
        self.anand = make_account()
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)
        self.mani_account = make_account('Mani', 'mani@example.com')
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        Account.objects.filter(pk__in=[self.anand.pk, self.mani_account.pk]).update(
            syncup_token='tok')
        self.anand.refresh_from_db()
        self.mani_account.refresh_from_db()
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.site_base = 'https://synchub.example.com'
        hub.save()
        Settings.objects.update_or_create(pk=1, defaults={'notify_quiet_from': '00:00',
                                                          'notify_quiet_to': '00:00'})

    def _send(self, acc, kind='lent', amount='2000'):
        fake = FakeSyncUp()
        with patch('pocket.push.threading.Thread', RunInline), \
             patch('core.push.urllib.request.urlopen', fake):
            made = service.add_ledger(acc, self.mani, kind, amount)
            result = push.notify_entry(made, acc.account)
        return fake, result

    def test_the_other_side_hears_and_the_author_does_not(self):
        acc = service.access(self.anand, self.folder)
        fake, result = self._send(acc)
        self.assertIn('pushed', result)
        self.assertEqual(len(fake.payloads), 1)
        self.assertEqual(fake.payload['token'], 'tok')
        self.assertIn('Anand', fake.payload['title'])

    def test_a_linked_person_writing_reaches_the_owner(self):
        mine = service.access(self.mani_account, self.folder, self.mani)
        fake, result = self._send(mine, kind='repaid', amount='500')
        self.assertIn('pushed', result)
        self.assertIn('Mani', fake.payload['title'])

    def test_a_second_entry_a_moment_later_is_coalesced(self):
        acc = service.access(self.anand, self.folder)
        self._send(acc)
        fake, result = self._send(acc, amount='300')
        self.assertEqual(result, ['coalesced'])
        self.assertEqual(len(fake.payloads), 0)

    def test_the_owner_is_coalesced_too_when_somebody_else_writes(self):
        """The owner hears about everything in their own book, so they need the clock most."""
        divya = make_account('Divya', 'divya@example.com')
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, divya)
        theirs = service.access(divya, self.folder)
        first, _ = self._send(theirs)
        second, result = self._send(theirs, amount='300')
        self.assertEqual(len(first.payloads), 2)          # Anand and the linked Mani
        self.assertEqual(result, ['coalesced', 'coalesced'])
        self.assertEqual(len(second.payloads), 0)

    def test_quiet_hours_stop_everything(self):
        config = Settings.load()
        config.notify_quiet_from = '00:00'
        config.notify_quiet_to = '23:59'
        config.save()
        acc = service.access(self.anand, self.folder)
        fake, result = self._send(acc)
        self.assertEqual(result, ['quiet'])
        self.assertEqual(len(fake.payloads), 0)

    def test_without_a_token_nothing_is_attempted(self):
        Account.objects.filter(pk=self.mani_account.pk).update(syncup_token='')
        acc = service.access(self.anand, self.folder)
        fake, _ = self._send(acc)
        self.assertEqual(len(fake.payloads), 0)

    def test_a_folder_share_hears_too(self):
        divya = make_account('Divya', 'divya@example.com')
        Account.objects.filter(pk=divya.pk).update(syncup_token='tok')
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, divya)
        acc = service.access(self.anand, self.folder)
        fake, _ = self._send(acc)
        self.assertEqual(len(fake.payloads), 2)          # Divya, and the linked Mani

    def test_a_due_reminder_goes_once_a_day(self):
        acc = service.access(self.anand, self.folder)
        yesterday = Settings.load().today() - timedelta(days=1)
        service.add_ledger(acc, self.mani, Kind.LENT, '2000', due_on=yesterday)
        fake = FakeSyncUp()
        with patch('pocket.push.threading.Thread', RunInline), \
             patch('core.push.urllib.request.urlopen', fake):
            first = push.run_due_reminders()
            second = push.run_due_reminders()
        self.assertEqual(first['sent'], 1)
        self.assertEqual(second['sent'], 0)
        self.assertIn('late', fake.payload['body'])

    def test_a_settled_person_is_not_chased(self):
        acc = service.access(self.anand, self.folder)
        yesterday = Settings.load().today() - timedelta(days=1)
        service.add_ledger(acc, self.mani, Kind.LENT, '2000', due_on=yesterday)
        service.settle_up(acc, self.mani)
        fake = FakeSyncUp()
        with patch('pocket.push.threading.Thread', RunInline), \
             patch('core.push.urllib.request.urlopen', fake):
            self.assertEqual(push.run_due_reminders()['sent'], 0)


class CronTests(TestCase):
    def setUp(self):
        self.anand = make_account()
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)

    def test_the_tick_does_nothing_when_push_is_off(self):
        config = Settings.load()
        config.push_enabled = False
        config.save()
        self.assertEqual(notify(_ctx()), {'skipped': 'push off'})

    def test_the_tick_is_registered_with_the_hub(self):
        names = [hook.name for hook in cronhub.hooks('tick')]
        self.assertIn('pocket.notify', names)

    def test_cleanup_trims_the_audit_and_dead_links(self):
        service.log('ledger', 'Old thing', folder=self.folder)
        EntryLog.objects.update(at=timezone.now() - timedelta(days=400))
        service.invite_person(self.mani, by=self.anand)
        Person.objects.filter(pk=self.mani.pk).update(
            link_made_at=timezone.now() - timedelta(days=30))
        stale = EntryLog.objects.filter(at__lt=timezone.now() - timedelta(days=365)).count()
        out = retention(_ctx('cleanup'))
        self.assertEqual(out['audit_rows'], stale)
        self.assertEqual(out['dead_invites'], 1)
        self.assertTrue(EntryLog.objects.filter(action='Invited').exists(),
                        'a recent row is not swept up with the old ones')

    def test_cleanup_never_touches_an_entry(self):
        entry(self.folder, self.mani, Kind.LENT, '2000')
        LedgerEntry.objects.update(created_at=timezone.now() - timedelta(days=4000))
        retention(_ctx('cleanup'))
        self.assertEqual(LedgerEntry.objects.count(), 1)


# --------------------------------------------------------------------------- #
class ConsoleTests(TestCase):
    """What the admin can see and do."""

    def setUp(self):
        self.admin = User.objects.create_superuser('admin', 'a@example.com', 'pw-for-tests-1234')
        self.client.force_login(self.admin)
        self.anand = make_account()
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)
        entry(self.folder, self.mani, Kind.LENT, '2000')
        service.add_tracker(self.anand, Flow.IN, '5000')

    def test_the_landing_page_lists_accounts(self):
        r = self.client.get(reverse('pocket_accounts'))
        self.assertContains(r, 'Anand')
        self.assertContains(r, 'Pocket Money')

    def test_the_two_halves_are_shown_apart(self):
        r = self.client.get(reverse('pocket_admin_account', args=[self.anand.pk]))
        self.assertContains(r, 'Tracker')
        self.assertContains(r, 'Ledger')
        self.assertContains(r, 'Office')

    def test_a_folder_page_shows_the_people_and_the_entries(self):
        r = self.client.get(reverse('pocket_admin_folder', args=[self.folder.pk]))
        self.assertContains(r, 'Mani')
        self.assertContains(r, 'I gave')

    def test_the_admin_can_hard_delete_an_entry(self):
        target = LedgerEntry.objects.get()
        r = self.client.post(reverse('pocket_admin_entry_delete',
                                     args=[self.folder.pk, target.pk]), follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(LedgerEntry.objects.count(), 0)

    def test_archiving_blocks_sign_in_and_leaves_the_books(self):
        self.client.post(reverse('pocket_admin_state', args=[self.anand.pk, 'archive']))
        self.anand.refresh_from_db()
        self.assertTrue(self.anand.is_archived)
        self.assertEqual(LedgerEntry.objects.count(), 1)

    def test_deleting_can_hand_the_folders_to_somebody(self):
        divya = make_account('Divya', 'divya@example.com')
        self.client.post(reverse('pocket_admin_delete', args=[self.anand.pk]),
                         {'hand_to': divya.pk})
        self.assertFalse(Account.objects.filter(pk=self.anand.pk).exists())
        self.folder.refresh_from_db()
        self.assertEqual(self.folder.owner, divya)

    def test_the_settings_page_opens_and_states_the_rule_that_is_not_a_switch(self):
        r = self.client.get(reverse('pocket_settings'))
        self.assertContains(r, 'Link without both confirming')
        self.assertContains(r, 'never')
        self.assertContains(r, 'No factory reset here')

    def test_the_settings_page_saves(self):
        r = self.client.post(reverse('pocket_settings'), {
            'signup_open': 'on', 'invite_code': 'POCKET26', 'currency_symbol': '₹',
            'time_zone': 'Asia/Kolkata', 'push_enabled': 'on', 'notify_quiet_from': '22:00',
            'notify_quiet_to': '06:30', 'reminder_hour': '9', 'coalesce_minutes': '5',
            'link_days': '7', 'keep_audit_days': '365'}, follow=True)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Settings.load().invite_code, 'POCKET26')

    def test_the_console_needs_an_admin(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('pocket_accounts')).status_code, 302)


class ExportTests(TestCase):
    def setUp(self):
        self.anand = make_account()
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)

    def test_a_folder_leaves_as_csv(self):
        entry(self.folder, self.mani, Kind.LENT, '2000', note='for his sister')
        body = service.folder_csv(self.folder)
        self.assertIn('Mani', body)
        self.assertIn('2000.00', body)
        self.assertIn('for his sister', body)

    def test_the_tracker_leaves_as_csv(self):
        service.add_tracker(self.anand, Flow.OUT, '1240', note='Groceries')
        self.assertIn('Groceries', service.tracker_csv(self.anand))
