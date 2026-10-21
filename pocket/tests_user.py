"""Pocket Money — the pages people actually touch.

`tests.py` pins the sums; this walks the routes: signing up, adding an entry, inviting somebody,
and the two sides of one line reading the same rows in opposite directions.
"""
import json
from datetime import timedelta
from decimal import Decimal

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import service
from core import auth as hub
from core.models import Member

from .models import Account, Flow, Kind, LedgerEntry, Person, Settings, TrackerEntry
from .tests import entry, make_account, make_folder, make_person, sign_in

D = Decimal


class GettingInTests(TestCase):
    """Pocket Money has no sign-in of its own any more — only "have you started it"."""

    def _member(self, name='Anand', email='anand@example.com'):
        member = Member.objects.create(name=name, email=email)
        hub.set_password(member, 'pw-for-tests')
        return member

    def test_a_stranger_is_sent_to_the_hub_carrying_where_they_were_going(self):
        r = self.client.get(reverse('pocket_tracker'))
        self.assertRedirects(r, f'{reverse("member_login")}?next={reverse("pocket_tracker")}',
                             target_status_code=200)

    def test_a_member_who_has_never_opened_it_is_offered_the_start(self):
        self._member()
        sign_in(self.client, 'anand@example.com')
        self.assertRedirects(self.client.get(reverse('pocket_tracker')), reverse('pocket_join'))
        self.assertContains(self.client.get(reverse('pocket_join')), 'Start Pocket Money')

    def test_starting_it_asks_for_nothing(self):
        """The hub already knows the name, the email and the password."""
        member = self._member()
        sign_in(self.client, 'anand@example.com')
        r = self.client.post(reverse('pocket_join'))
        self.assertRedirects(r, reverse('pocket_tracker'))
        account = Account.objects.get()
        self.assertEqual(account.member, member)
        self.assertEqual(account.name, 'Anand')
        self.assertTrue(account.categories.exists(), 'the starter categories come with it')

    def test_starting_twice_is_the_same_as_starting_once(self):
        self._member()
        sign_in(self.client, 'anand@example.com')
        self.client.post(reverse('pocket_join'))
        self.client.post(reverse('pocket_join'))
        self.assertEqual(Account.objects.count(), 1)

    def test_the_join_page_still_says_who_can_read_your_books(self):
        self._member()
        sign_in(self.client, 'anand@example.com')
        self.assertContains(self.client.get(reverse('pocket_join')),
                            "read by this server's admin")

    def test_the_old_sign_in_address_still_leads_somewhere_sensible(self):
        """Bookmarks and old notification links should not dead-end."""
        self.assertRedirects(self.client.get(reverse('pocket_login')),
                             f'{reverse("member_login")}?next={reverse("pocket_tracker")}',
                             target_status_code=200)

    def test_a_bad_password_is_refused_at_the_hub(self):
        make_account()
        r = sign_in(self.client, 'anand@example.com', 'nope')
        self.assertContains(r, 'do not go together')

    def test_signing_in_once_reaches_pocket_money(self):
        make_account()
        sign_in(self.client, 'anand@example.com')
        self.assertEqual(self.client.get(reverse('pocket_tracker')).status_code, 200)


class TrackerPageTests(TestCase):
    def setUp(self):
        self.account = make_account()
        sign_in(self.client, 'anand@example.com', 'pw-for-tests')
        self.food = self.account.categories.get(flow=Flow.OUT, name='Food')

    def test_the_page_shows_the_month_and_the_categories(self):
        r = self.client.get(reverse('pocket_tracker'))
        self.assertContains(r, 'Kept this month')
        self.assertContains(r, 'Food')
        self.assertContains(r, 'Salary')

    def test_adding_an_entry_from_the_page(self):
        self.client.post(reverse('pocket_tracker_add'),
                         {'flow': 'out', 'amount': '1,240', 'category': self.food.pk,
                          'source': 'Cash', 'note': 'Groceries'})
        made = TrackerEntry.objects.get()
        self.assertEqual(made.amount, D('1240.00'))
        self.assertEqual(made.note, 'Groceries')

    def test_a_category_from_the_wrong_side_is_refused(self):
        salary = self.account.categories.get(flow=Flow.IN, name='Salary')
        r = self.client.post(reverse('pocket_tracker_add'),
                             {'flow': 'out', 'amount': '100', 'category': salary.pk}, follow=True)
        self.assertContains(r, 'belongs somewhere else')
        self.assertFalse(TrackerEntry.objects.exists())

    def test_voiding_from_the_page_keeps_the_row(self):
        service.add_tracker(self.account, Flow.OUT, '800', category=self.food)
        made = TrackerEntry.objects.get()
        self.client.post(reverse('pocket_tracker_void', args=[made.pk]))
        made.refresh_from_db()
        self.assertTrue(made.is_void)

    def test_somebody_elses_entry_is_not_yours_to_void(self):
        other = make_account('Divya', 'divya@example.com')
        theirs = service.add_tracker(other, Flow.OUT, '500')
        self.assertEqual(
            self.client.post(reverse('pocket_tracker_void', args=[theirs.pk])).status_code, 404)

    def test_an_entry_with_no_category_still_renders(self):
        """A dotted lookup used as a *filter argument* raises when it resolves to None, where
        the same lookup in output position quietly gives ''. That crashed this page."""
        service.add_tracker(self.account, Flow.OUT, '1240', note='Groceries')
        service.add_tracker(self.account, Flow.IN, '500')          # no note, no category
        r = self.client.get(reverse('pocket_tracker'))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, 'Groceries')
        self.assertContains(r, 'Entry')

    def test_the_month_downloads_as_csv(self):
        service.add_tracker(self.account, Flow.OUT, '1240', note='Groceries')
        r = self.client.get(reverse('pocket_tracker_csv'))
        self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn('Groceries', r.content.decode())

    def test_a_new_category_can_be_made_where_it_is_missed(self):
        self.client.post(reverse('pocket_categories'), {'flow': 'out', 'name': 'School'})
        self.assertTrue(self.account.categories.filter(flow=Flow.OUT, name='School').exists())


class LedgerPageTests(TestCase):
    def setUp(self):
        self.anand = make_account()
        sign_in(self.client, 'anand@example.com', 'pw-for-tests')
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)

    def test_the_ledger_lists_folders_with_their_two_totals(self):
        entry(self.folder, self.mani, Kind.LENT, '4500')
        r = self.client.get(reverse('pocket_ledger'))
        self.assertContains(r, 'Office')
        self.assertContains(r, 'Owed to you')
        self.assertContains(r, '4500')

    def test_a_folder_is_made_from_the_page(self):
        self.client.post(reverse('pocket_folder_new'), {'name': 'Chit'})
        self.assertTrue(self.anand.folders.filter(name='Chit').exists())

    def test_a_person_is_added_with_a_phone_and_a_upi_id(self):
        self.client.post(reverse('pocket_person_add', args=[self.folder.pk]),
                         {'name': 'Riya', 'phone': '98765 77310', 'upi_id': 'riya@okaxis'})
        riya = Person.objects.get(name='Riya')
        self.assertEqual(riya.phone, '9876577310')
        self.assertEqual(riya.upi_id, 'riya@okaxis')

    def test_a_bad_upi_id_is_refused_with_a_sentence(self):
        r = self.client.post(reverse('pocket_person_add', args=[self.folder.pk]),
                             {'name': 'Riya', 'upi_id': 'not-a-upi'}, follow=True)
        self.assertContains(r, 'name@bank')
        self.assertFalse(Person.objects.filter(name='Riya').exists())

    def test_the_person_page_shows_the_statement_in_the_right_direction(self):
        entry(self.folder, self.mani, Kind.LENT, '2000')
        r = self.client.get(reverse('pocket_person', args=[self.folder.pk, self.mani.pk]))
        self.assertContains(r, 'Mani owes you')
        self.assertContains(r, 'Given')

    def test_the_labels_flip_when_you_are_the_one_who_owes(self):
        entry(self.folder, self.mani, Kind.BORROWED, '2000')
        r = self.client.get(reverse('pocket_person', args=[self.folder.pk, self.mani.pk]))
        self.assertContains(r, 'You owe Mani')
        self.assertContains(r, 'Taken')

    def test_adding_to_the_ledger_from_the_page(self):
        self.client.post(reverse('pocket_ledger_add', args=[self.folder.pk, self.mani.pk]),
                         {'kind': 'lent', 'amount': '2000', 'source': 'Cash'})
        self.assertEqual(LedgerEntry.objects.get().kind, Kind.LENT)

    def test_settling_up_from_the_page(self):
        entry(self.folder, self.mani, Kind.LENT, '4500')
        self.client.post(reverse('pocket_settle', args=[self.folder.pk, self.mani.pk]))
        self.assertEqual(service.person_balance(self.mani), D('0.00'))

    def test_somebody_elses_folder_is_a_404(self):
        other = make_folder(make_account('Divya', 'divya@example.com'), 'Theirs')
        self.assertEqual(self.client.get(reverse('pocket_folder', args=[other.pk])).status_code,
                         404)

    def test_a_person_can_be_edited(self):
        r = self.client.get(reverse('pocket_person_edit', args=[self.folder.pk, self.mani.pk]))
        self.assertContains(r, 'Edit Mani')
        self.client.post(reverse('pocket_person_edit', args=[self.folder.pk, self.mani.pk]),
                         {'name': 'Mani K', 'upi_id': 'mani@okhdfcbank', 'phone': '', 'note': ''})
        self.mani.refresh_from_db()
        self.assertEqual(self.mani.name, 'Mani K')
        self.assertEqual(self.mani.upi_id, 'mani@okhdfcbank')

    def test_the_categories_page_lists_one_side_at_a_time(self):
        r = self.client.get(reverse('pocket_categories') + '?flow=in')
        self.assertContains(r, 'Salary')
        self.assertNotContains(r, '>Food<')

    def test_the_folder_downloads_as_csv(self):
        entry(self.folder, self.mani, Kind.LENT, '2000')
        r = self.client.get(reverse('pocket_folder_csv', args=[self.folder.pk]))
        self.assertEqual(r['Content-Type'], 'text/csv')
        self.assertIn('Mani', r.content.decode())


class PayTests(TestCase):
    """The UPI hand-off: a redirect, offered only when it makes sense."""

    def setUp(self):
        self.anand = make_account()
        sign_in(self.client, 'anand@example.com', 'pw-for-tests')
        self.folder = make_folder(self.anand)
        self.riya = make_person(self.folder, 'Riya', upi_id='riya@okaxis')

    def test_pay_is_offered_when_you_owe_them(self):
        entry(self.folder, self.riya, Kind.BORROWED, '2000')
        r = self.client.get(reverse('pocket_person', args=[self.folder.pk, self.riya.pk]))
        self.assertContains(r, 'Pay Riya by UPI')
        self.assertContains(r, 'upi://pay?pa=riya%40okaxis')

    def test_the_link_carries_no_amount(self):
        entry(self.folder, self.riya, Kind.BORROWED, '2000')
        body = self.client.get(reverse('pocket_person',
                                       args=[self.folder.pk, self.riya.pk])).content.decode()
        link = body.split('upi://pay?', 1)[1].split('"', 1)[0]
        self.assertNotIn('am=', link)
        self.assertIn('cu=INR', link)

    def test_pay_is_not_offered_to_somebody_who_owes_you(self):
        entry(self.folder, self.riya, Kind.LENT, '2000')
        r = self.client.get(reverse('pocket_person', args=[self.folder.pk, self.riya.pk]))
        self.assertNotContains(r, 'Pay Riya by UPI')

    def test_a_scanned_qr_gives_back_the_id_without_the_amount(self):
        r = self.client.post(reverse('pocket_scan'),
                             data=json.dumps({'text': 'upi://pay?pa=riya@okaxis&am=500'}),
                             content_type='application/json')
        self.assertEqual(r.json(), {'ok': True, 'upi': 'riya@okaxis', 'name': ''})

    def test_a_qr_that_is_not_upi_is_refused(self):
        r = self.client.post(reverse('pocket_scan'),
                             data=json.dumps({'text': 'https://example.com'}),
                             content_type='application/json')
        self.assertFalse(r.json()['ok'])

    def test_your_own_upi_id_is_saved_from_the_me_page(self):
        self.client.post(reverse('pocket_upi'), {'upi_id': 'anand@okicici'})
        self.anand.refresh_from_db()
        self.assertEqual(self.anand.upi_id, 'anand@okicici')


class PromotionPageTests(TestCase):
    """Both halves of the handshake, walked through the browser."""

    def setUp(self):
        self.anand = make_account()
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)
        self.mani_account = make_account('Mani', 'mani@example.com')

    def _sign_in(self, who):
        # Sign out first: the sign-in page redirects an already-signed-in visitor rather than
        # swapping accounts under them, which is right for people and a trap for tests.
        self.client.get(reverse('pocket_logout'))
        sign_in(self.client, who, 'pw-for-tests')

    def _as_anand(self):
        self._sign_in('anand@example.com')

    def _as_mani(self):
        self._sign_in('mani@example.com')

    def test_the_invite_page_says_exactly_what_will_be_shared(self):
        self._as_anand()
        make_person(self.folder, 'Karthik')
        r = self.client.get(reverse('pocket_person_invite', args=[self.folder.pk, self.mani.pk]))
        self.assertContains(r, 'Only your line with them')
        self.assertContains(r, 'Not</b> the other 1 people')

    def test_a_suggestion_is_shown_but_nothing_is_shared(self):
        person = make_person(self.folder, 'Suresh', phone='9000012345')
        make_account('Suresh', '9000012345')
        self._as_anand()
        r = self.client.get(reverse('pocket_ledger'))
        self.assertContains(r, 'Is this your Suresh?')
        person.refresh_from_db()
        self.assertIsNone(person.linked_account)

    def test_confirming_a_suggestion_only_sends_an_invitation(self):
        person = make_person(self.folder, 'Suresh', phone='9000012345')
        suresh = make_account('Suresh', '9000012345')
        self._as_anand()
        self.client.post(reverse('pocket_suggestion', args=[person.pk, 'yes']))
        person.refresh_from_db()
        self.assertIsNotNone(person.link_code)
        self.assertIsNone(person.linked_account)
        self.assertIn(person, list(service.pending_person_invites(suresh)))

    def test_the_whole_handshake(self):
        self._as_anand()
        self.client.post(reverse('pocket_person_invite', args=[self.folder.pk, self.mani.pk]),
                         {'what': 'invite', 'can_edit': '1'})
        self.mani.refresh_from_db()
        code = self.mani.link_code
        entry(self.folder, self.mani, Kind.LENT, '2000')

        self._as_mani()
        page = self.client.get(reverse('pocket_accept_person', args=[code]))
        self.assertContains(page, 'wants to share a ledger with you')
        self.assertContains(page, '2000')
        r = self.client.post(reverse('pocket_accept_person', args=[code]), {'answer': 'yes'})
        self.mani.refresh_from_db()
        self.assertRedirects(r, reverse('pocket_line', args=[self.mani.pk]))
        self.assertEqual(self.mani.linked_account, self.mani_account)

    def test_the_line_reads_the_other_way_round(self):
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        entry(self.folder, self.mani, Kind.LENT, '2000')
        self._as_mani()
        r = self.client.get(reverse('pocket_line', args=[self.mani.pk]))
        self.assertContains(r, 'You owe Anand')
        self.assertContains(r, 'Taken')

    def test_a_linked_person_writes_through_the_mirror(self):
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        entry(self.folder, self.mani, Kind.LENT, '2000')
        self._as_mani()
        self.client.post(reverse('pocket_line_add', args=[self.mani.pk]),
                         {'kind': 'repaid', 'amount': '500', 'source': 'UPI'})
        self.assertEqual(LedgerEntry.objects.latest('pk').kind, Kind.COLLECTED)
        self.assertEqual(service.person_balance(self.mani), D('1500.00'))

    def test_a_linked_person_cannot_reach_the_folder_or_anybody_else(self):
        other = make_person(self.folder, 'Karthik')
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        self._as_mani()
        self.assertEqual(
            self.client.get(reverse('pocket_folder', args=[self.folder.pk])).status_code, 404)
        self.assertEqual(
            self.client.get(reverse('pocket_person',
                                    args=[self.folder.pk, other.pk])).status_code, 404)

    def test_the_shared_tab_gathers_the_three_kinds_of_thing(self):
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        entry(self.folder, self.mani, Kind.LENT, '2000')
        divya_folder = make_folder(make_account('Divya', 'divya@example.com'), 'Household')
        share = service.share_folder(divya_folder, can_edit=True)
        service.accept_share(share, self.mani_account)
        self._as_mani()
        r = self.client.get(reverse('pocket_shared'))
        self.assertContains(r, 'Anand')
        self.assertContains(r, 'you owe')
        self.assertContains(r, 'Household')

    def test_leaving_a_line_keeps_the_owners_records(self):
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, self.mani_account)
        entry(self.folder, self.mani, Kind.LENT, '2000')
        self._as_mani()
        self.client.post(reverse('pocket_line_leave', args=[self.mani.pk]))
        self.assertEqual(LedgerEntry.objects.count(), 1)
        self.assertEqual(self.client.get(reverse('pocket_line',
                                                 args=[self.mani.pk])).status_code, 404)

    def test_an_expired_invitation_says_so_rather_than_failing(self):
        code = service.invite_person(self.mani, by=self.anand)
        Person.objects.filter(pk=self.mani.pk).update(
            link_made_at=timezone.now() - timedelta(days=30))
        self._as_mani()
        r = self.client.get(reverse('pocket_accept_person', args=[code]))
        self.assertEqual(r.status_code, 404)
        self.assertContains(r, 'expired', status_code=404)


class FolderSharePageTests(TestCase):
    def setUp(self):
        self.anand = make_account()
        self.divya = make_account('Divya', 'divya@example.com')
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)
        sign_in(self.client, 'anand@example.com', 'pw-for-tests')

    def test_the_share_page_warns_about_people_who_are_in_the_folder(self):
        mani_account = make_account('Mani', 'mani@example.com')
        service.invite_person(self.mani, by=self.anand)
        service.accept_person_invite(self.mani, mani_account)
        r = self.client.get(reverse('pocket_folder_share', args=[self.folder.pk]))
        self.assertContains(r, 'Careful with people who are in it')
        self.assertContains(r, 'Mani')

    def test_a_share_link_is_made_and_taken(self):
        self.client.post(reverse('pocket_folder_share', args=[self.folder.pk]), {'can_edit': '1'})
        share = self.folder.shares.get()
        self.client.get(reverse('pocket_logout'))
        sign_in(self.client, 'divya@example.com', 'pw-for-tests')
        self.client.post(reverse('pocket_accept_folder', args=[share.code]), {'answer': 'yes'})
        self.assertIsNotNone(service.access(self.divya, self.folder))

    def test_an_editor_sees_the_folder_but_not_the_share_page(self):
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, self.divya)
        self.client.get(reverse('pocket_logout'))
        sign_in(self.client, 'divya@example.com', 'pw-for-tests')
        self.assertEqual(
            self.client.get(reverse('pocket_folder', args=[self.folder.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('pocket_folder_share', args=[self.folder.pk])).status_code,
            404)

    def test_a_viewer_cannot_post_an_entry(self):
        share = service.share_folder(self.folder, can_edit=False, by=self.anand)
        service.accept_share(share, self.divya)
        self.client.get(reverse('pocket_logout'))
        sign_in(self.client, 'divya@example.com', 'pw-for-tests')
        self.client.post(reverse('pocket_ledger_add', args=[self.folder.pk, self.mani.pk]),
                         {'kind': 'lent', 'amount': '500'})
        self.assertFalse(LedgerEntry.objects.exists())


class MePageTests(TestCase):
    def setUp(self):
        self.account = make_account()
        sign_in(self.client, 'anand@example.com', 'pw-for-tests')

    def test_the_page_says_who_can_see_the_upi_id(self):
        r = self.client.get(reverse('pocket_me'))
        self.assertContains(r, 'Who sees this')
        self.assertContains(r, 'collect request')

    def test_the_name_and_theme_belong_to_the_hub(self):
        """They are the same in every app here, so the Me page points at the hub profile."""
        r = self.client.get(reverse('pocket_me'))
        self.assertContains(r, reverse('member_you'))
        self.assertContains(r, 'your SyncHub profile')

    def test_the_device_token_is_taken_once(self):
        r = self.client.post(reverse('pocket_token'), data=json.dumps({'token': 'abc'}),
                             content_type='application/json')
        self.assertTrue(r.json()['saved'])
        again = self.client.post(reverse('pocket_token'), data=json.dumps({'token': 'abc'}),
                                 content_type='application/json')
        self.assertFalse(again.json()['saved'])

    def test_signing_out_ends_the_session(self):
        self.client.post(reverse('member_logout'))
        self.assertRedirects(
            self.client.get(reverse('pocket_tracker')),
            f'{reverse("member_login")}?next={reverse("pocket_tracker")}', target_status_code=200)


class FixingAMistakeTests(TestCase):
    """A wrong figure is the commonest thing that happens to a money app.

    Correcting one has to be ordinary. What differs is whether anybody else has seen it:
    your own Tracker is yours, a shared book is not.
    """

    def setUp(self):
        self.anand = make_account()
        sign_in(self.client, 'anand@example.com')
        self.folder = make_folder(self.anand)
        self.mani = make_person(self.folder)

    # ---- the Tracker: yours alone
    def test_a_mistyped_amount_can_be_corrected(self):
        entry = service.add_tracker(self.anand, Flow.OUT, '12000', note='Groceries')
        self.client.post(reverse('pocket_tracker_edit', args=[entry.pk]),
                         {'flow': 'out', 'amount': '1200', 'note': 'Groceries',
                          'date': entry.date.isoformat()})
        entry.refresh_from_db()
        self.assertEqual(entry.amount, D('1200.00'))
        self.assertFalse(entry.is_void, 'a correction is not a void')

    def test_and_it_can_simply_be_deleted(self):
        entry = service.add_tracker(self.anand, Flow.OUT, '12000')
        self.client.post(reverse('pocket_tracker_edit', args=[entry.pk]), {'what': 'delete'})
        self.assertFalse(TrackerEntry.objects.filter(pk=entry.pk).exists())

    def test_the_fix_is_one_tap_from_the_row(self):
        service.add_tracker(self.anand, Flow.OUT, '1240', note='Groceries')
        entry = TrackerEntry.objects.get()
        self.assertContains(self.client.get(reverse('pocket_tracker')),
                            reverse('pocket_tracker_edit', args=[entry.pk]))

    def test_somebody_elses_entry_is_not_yours_to_touch(self):
        other = make_account('Divya', 'divya@example.com')
        theirs = service.add_tracker(other, Flow.OUT, '500')
        self.assertEqual(self.client.get(
            reverse('pocket_tracker_edit', args=[theirs.pk])).status_code, 404)

    def test_the_correction_is_recorded(self):
        from .models import EntryLog
        entry = service.add_tracker(self.anand, Flow.OUT, '12000')
        self.client.post(reverse('pocket_tracker_edit', args=[entry.pk]),
                         {'flow': 'out', 'amount': '1200', 'date': entry.date.isoformat()})
        row = EntryLog.objects.filter(action='Edited').get()
        self.assertEqual(row.before, D('12000.00'))
        self.assertEqual(row.after, D('1200.00'))

    # ---- the Ledger: it depends who is watching
    def test_an_entry_in_a_private_book_can_be_deleted(self):
        made = entry(self.folder, self.mani, Kind.LENT, '20000')
        r = self.client.get(reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk]))
        self.assertContains(r, 'Delete it instead')
        self.client.post(reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk]),
                         {'what': 'delete'})
        self.assertFalse(LedgerEntry.objects.filter(pk=made.pk).exists())

    def test_but_a_shared_book_takes_it_back_instead(self):
        divya = make_account('Divya', 'divya@example.com')
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, divya)
        made = entry(self.folder, self.mani, Kind.LENT, '20000')
        r = self.client.get(reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk]))
        self.assertContains(r, 'Take it back')
        self.assertNotContains(r, 'Delete it instead')
        self.client.post(reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk]),
                         {'what': 'delete'})
        self.assertTrue(LedgerEntry.objects.filter(pk=made.pk).exists(),
                        'the other side has seen this figure')

    def test_a_line_somebody_reads_is_the_same(self):
        mani_account = make_account('Mani', 'mani@example.com')
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, mani_account)
        made = entry(self.folder, self.mani, Kind.LENT, '20000')
        r = self.client.get(reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk]))
        self.assertContains(r, 'reads this line')
        self.assertNotContains(r, 'Delete it instead')

    def test_taking_it_back_keeps_it_visible_with_the_reason(self):
        divya = make_account('Divya', 'divya@example.com')
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, divya)
        made = entry(self.folder, self.mani, Kind.LENT, '20000')
        self.client.post(reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk]),
                         {'what': 'void', 'reason': 'typed twice'})
        made.refresh_from_db()
        self.assertTrue(made.is_void)
        self.assertEqual(made.void_reason, 'typed twice')
        self.assertEqual(service.person_balance(self.mani), D('0.00'))

    def test_correcting_a_ledger_amount_moves_the_balance(self):
        made = entry(self.folder, self.mani, Kind.LENT, '20000')
        self.client.post(reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk]),
                         {'kind': 'lent', 'amount': '2000', 'date': made.date.isoformat()})
        self.assertEqual(service.person_balance(self.mani), D('2000.00'))

    def test_a_linked_person_cannot_reach_the_fix_page(self):
        """They may add to their line, never edit or void — corrections stay with the owner."""
        mani_account = make_account('Mani', 'mani@example.com')
        service.invite_person(self.mani, can_edit=True, by=self.anand)
        service.accept_person_invite(self.mani, mani_account)
        made = entry(self.folder, self.mani, Kind.LENT, '2000')
        sign_in(self.client, 'mani@example.com')
        self.assertEqual(self.client.get(
            reverse('pocket_ledger_edit', args=[self.folder.pk, made.pk])).status_code, 404)
