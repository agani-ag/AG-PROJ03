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
from .models import Account, Flow, Kind, LedgerEntry, Person, Settings, TrackerEntry
from .tests import entry, make_account, make_folder, make_person

D = Decimal


class GettingInTests(TestCase):
    def test_signing_up_and_landing_on_the_tracker(self):
        r = self.client.post(reverse('pocket_signup'), {
            'name': 'Anand', 'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})
        self.assertRedirects(r, reverse('pocket_tracker'))
        self.assertTrue(Account.objects.filter(email='anand@example.com').exists())

    def test_the_sign_up_page_says_who_can_read_your_books(self):
        r = self.client.get(reverse('pocket_signup'))
        self.assertContains(r, "read by this server's admin")

    def test_a_bad_password_is_refused_without_saying_which_half_was_wrong(self):
        make_account()
        r = self.client.post(reverse('pocket_login'),
                             {'sign_in_id': 'anand@example.com', 'password': 'nope'})
        self.assertContains(r, 'do not go together')

    def test_an_invite_code_is_enforced_when_one_is_set(self):
        config = Settings.load()
        config.invite_code = 'POCKET26'
        config.save()
        r = self.client.post(reverse('pocket_signup'), {
            'name': 'Anand', 'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})
        self.assertContains(r, 'invite code')
        self.assertFalse(Account.objects.exists())

    def test_closed_sign_ups_say_so(self):
        config = Settings.load()
        config.signup_open = False
        config.save()
        self.assertContains(self.client.get(reverse('pocket_signup')), 'closed')

    def test_a_magic_link_signs_you_in_once(self):
        from . import auth
        account = make_account()
        key = auth.new_login_key(account)
        self.assertRedirects(self.client.get(reverse('pocket_magic', args=[key])),
                             reverse('pocket_tracker'))
        self.client.get(reverse('pocket_logout'))
        self.assertEqual(self.client.get(reverse('pocket_magic', args=[key])).status_code, 404)

    def test_the_tracker_needs_a_session(self):
        self.assertRedirects(self.client.get(reverse('pocket_tracker')), reverse('pocket_login'))


class TrackerPageTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})
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
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})
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
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})
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
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': who, 'password': 'pw-for-tests'})

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
        service.create_account('Suresh', '9000012345', 'pw-for-tests')
        self._as_anand()
        r = self.client.get(reverse('pocket_ledger'))
        self.assertContains(r, 'Is this your Suresh?')
        person.refresh_from_db()
        self.assertIsNone(person.linked_account)

    def test_confirming_a_suggestion_only_sends_an_invitation(self):
        person = make_person(self.folder, 'Suresh', phone='9000012345')
        suresh = service.create_account('Suresh', '9000012345', 'pw-for-tests')
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
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})

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
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'divya@example.com', 'password': 'pw-for-tests'})
        self.client.post(reverse('pocket_accept_folder', args=[share.code]), {'answer': 'yes'})
        self.assertIsNotNone(service.access(self.divya, self.folder))

    def test_an_editor_sees_the_folder_but_not_the_share_page(self):
        share = service.share_folder(self.folder, can_edit=True, by=self.anand)
        service.accept_share(share, self.divya)
        self.client.get(reverse('pocket_logout'))
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'divya@example.com', 'password': 'pw-for-tests'})
        self.assertEqual(
            self.client.get(reverse('pocket_folder', args=[self.folder.pk])).status_code, 200)
        self.assertEqual(
            self.client.get(reverse('pocket_folder_share', args=[self.folder.pk])).status_code,
            404)

    def test_a_viewer_cannot_post_an_entry(self):
        share = service.share_folder(self.folder, can_edit=False, by=self.anand)
        service.accept_share(share, self.divya)
        self.client.get(reverse('pocket_logout'))
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'divya@example.com', 'password': 'pw-for-tests'})
        self.client.post(reverse('pocket_ledger_add', args=[self.folder.pk, self.mani.pk]),
                         {'kind': 'lent', 'amount': '500'})
        self.assertFalse(LedgerEntry.objects.exists())


class MePageTests(TestCase):
    def setUp(self):
        self.account = make_account()
        self.client.post(reverse('pocket_login'),
                         {'sign_in_id': 'anand@example.com', 'password': 'pw-for-tests'})

    def test_the_page_says_who_can_see_the_upi_id(self):
        r = self.client.get(reverse('pocket_me'))
        self.assertContains(r, 'Who sees this')
        self.assertContains(r, 'collect request')

    def test_the_theme_is_remembered(self):
        self.client.post(reverse('pocket_me'), {'what': 'theme', 'theme': 'dark'})
        self.account.refresh_from_db()
        self.assertEqual(self.account.theme, 'dark')

    def test_the_device_token_is_taken_once(self):
        r = self.client.post(reverse('pocket_token'), data=json.dumps({'token': 'abc'}),
                             content_type='application/json')
        self.assertTrue(r.json()['saved'])
        again = self.client.post(reverse('pocket_token'), data=json.dumps({'token': 'abc'}),
                                 content_type='application/json')
        self.assertFalse(again.json()['saved'])

    def test_signing_out_ends_the_session(self):
        self.client.get(reverse('pocket_logout'))
        self.assertRedirects(self.client.get(reverse('pocket_tracker')), reverse('pocket_login'))
