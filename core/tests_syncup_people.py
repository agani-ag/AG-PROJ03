"""SyncUp's people, copied in — and the one rule that matters: never two accounts for one person.

Every row of the agreed no-duplicates table has a test here, plus the two failures that would
not look like duplicates at all: an empty email colliding on a unique index mid-batch, and a
password hash this site cannot check.

Nothing reaches the network; `_post` is replaced throughout.

Run: python manage.py test core.tests_syncup_people
"""
from unittest import mock

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from core import auth, syncup_people
from core.models import HubSettings, Member, SyncUpRefusal

POST = 'core.syncup_people._post'


# Whatever hasher this site is configured with — the suite runs on MD5 for speed, so a
# hardcoded PBKDF2 string would be exactly the "cannot check this" case below.
A_REAL_HASH = make_password('their-syncup-password')


def person(pk='su-1', name='Anand', email='anand@example.com', phone='+919876543210',
           password=A_REAL_HASH, token='su_tok_1'):
    return {'id': pk, 'name': name, 'email': email, 'phone': phone,
            'password': password, 'token': token}


def listing(*people, version='v1', host='hub.example.com', token_on=True):
    return {'success': True, 'version': version, 'host': host, 'token_on': token_on,
            'people': list(people)}


class _Synced(TestCase):
    def setUp(self):
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.syncup_links_key = 'a-key'
        hub.save()

    def run_with(self, payload, **kwargs):
        with mock.patch(POST, return_value=(payload, '')):
            return syncup_people.run(**kwargs)


class ReadingThemInTests(_Synced):

    def test_somebody_new_is_made_ahead_and_marked_unused(self):
        out = self.run_with(listing(person()))
        member = Member.objects.get(syncup_id='su-1')
        self.assertEqual(out['created'], 1)
        self.assertTrue(member.syncup_unused)
        self.assertEqual(member.email, 'anand@example.com')
        self.assertEqual(member.phone, '9876543210')     # +91 dropped, last ten kept

    def test_two_people_with_no_email_both_arrive(self):
        """'' is a value and `Member.email` is unique — the second would die on the index.

        It fails as an IntegrityError in the middle of a batch rather than as a visible
        duplicate, which is why it is worth its own test.
        """
        out = self.run_with(listing(person('su-1', email='', phone='+919000000001'),
                                    person('su-2', email='', phone='+919000000002')))
        self.assertEqual(out['created'], 2)
        self.assertEqual(list(Member.objects.values_list('email', flat=True)), [None, None])

    def test_two_people_with_no_phone_both_arrive(self):
        out = self.run_with(listing(person('su-1', phone='', email='a@example.com'),
                                    person('su-2', phone='', email='b@example.com')))
        self.assertEqual(out['created'], 2)

    def test_a_second_run_updates_rather_than_duplicates(self):
        self.run_with(listing(person()))
        out = self.run_with(listing(person(name='Anand Kumar'), version='v2'))
        self.assertEqual(Member.objects.filter(syncup_id='su-1').count(), 1)
        self.assertEqual(out['updated'], 1)
        self.assertEqual(Member.objects.get(syncup_id='su-1').name, 'Anand Kumar')

    def test_an_unchanged_list_does_no_work(self):
        with mock.patch(POST, return_value=({'success': True, 'unchanged': True,
                                             'version': 'v1'}, '')):
            out = syncup_people.run()
        self.assertTrue(out['unchanged'])
        self.assertEqual(Member.objects.count(), 0)

    def test_the_version_is_remembered_and_sent_back(self):
        self.run_with(listing(person(), version='v7'))
        self.assertEqual(HubSettings.load().syncup_people_version, 'v7')

    def test_a_failure_is_written_down_and_changes_nothing(self):
        with mock.patch(POST, return_value=(None, 'SyncUp said 403: Wrong key')):
            out = syncup_people.run()
        self.assertIn('403', out['error'])
        self.assertEqual(Member.objects.count(), 0)
        self.assertIn('403', HubSettings.load().syncup_people_note)


class NoDuplicatesTests(_Synced):
    """The table from the mockup, one test per row."""

    def mine(self, **fields):
        """An account somebody made here themselves — no syncup_id."""
        row = Member.objects.create(name='Mine', **fields)
        auth.set_password(row, 'my-own-password')
        return row

    def test_the_same_email_waits_rather_than_doubling(self):
        older = self.mine(email='anand@example.com')
        out = self.run_with(listing(person()))
        older.refresh_from_db()
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(out['waiting'], 1)
        self.assertEqual(older.waiting_for, 'su-1')
        self.assertEqual(older.waiting_why, Member.Waiting.EMAIL)

    def test_the_same_email_in_different_capitals_still_matches(self):
        self.mine(email='anand@example.com')
        self.run_with(listing(person(email='ANAND@Example.COM')))
        self.assertEqual(Member.objects.count(), 1)

    def test_the_same_phone_in_a_different_format_still_matches(self):
        older = self.mine(phone='9876543210')
        self.run_with(listing(person(email='other@example.com', phone='+91 98765 43210')))
        older.refresh_from_db()
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(older.waiting_why, Member.Waiting.PHONE)

    def test_signed_in_on_the_same_phone_matches_on_the_token(self):
        """Different email, different number, but their page token reached us while they
        were using their own account — so it is them."""
        older = self.mine(email='different@example.com', syncup_token='su_tok_1')
        self.run_with(listing(person()))
        older.refresh_from_db()
        self.assertEqual(Member.objects.count(), 1)
        self.assertEqual(older.waiting_why, Member.Waiting.TOKEN)

    def test_a_refused_pair_is_never_offered_again(self):
        older = self.mine(email='different@example.com', syncup_token='su_tok_1')
        SyncUpRefusal.objects.create(member=older, syncup_id='su-1')
        out = self.run_with(listing(person()))
        older.refresh_from_db()
        self.assertEqual(out['created'], 1)              # they get their own account
        self.assertEqual(older.waiting_for, '')
        self.assertEqual(Member.objects.count(), 2)      # two people, not a duplicate

    def test_a_member_already_linked_is_not_matched_as_an_older_account(self):
        """Somebody else's synced account must never be offered to a second SyncUp person."""
        self.run_with(listing(person()))
        out = self.run_with(listing(person(), person('su-2', email='anand@example.com',
                                                     phone='+919000000009', token='su_tok_2'),
                                    version='v2'))
        self.assertEqual(out['created'], 1)
        self.assertEqual(Member.objects.count(), 2)
        # The email belongs to su-1, so su-2 arrives without one rather than not at all.
        second = Member.objects.get(syncup_id='su-2')
        self.assertIsNone(second.email)
        self.assertIn('already another account', second.syncup_clash)

    def test_the_race_between_the_cron_and_use_token(self):
        """Both reach the same person at once; the unique id makes the second find the first."""
        real = syncup_people.take

        def racing(fields, created_by=None):
            Member.objects.create(syncup_id=fields['syncup_id'], name='From use token',
                                  email=fields['email'], phone=fields['phone'])
            return real(fields, created_by)

        with mock.patch.object(syncup_people, 'take', racing):
            self.run_with(listing(person()))
        self.assertEqual(Member.objects.filter(syncup_id='su-1').count(), 1)


class ChangesInSyncUpTests(_Synced):

    def test_a_changed_email_that_clashes_is_skipped_and_shown(self):
        self.run_with(listing(person()))
        Member.objects.create(name='Somebody else', email='taken@example.com')
        out = self.run_with(listing(person(email='taken@example.com', name='Anand K'),
                                    version='v2'))
        member = Member.objects.get(syncup_id='su-1')
        self.assertEqual(out['updated'], 1)
        self.assertEqual(member.email, 'anand@example.com')    # not forced
        self.assertEqual(member.name, 'Anand K')               # the rest still applied
        self.assertIn('already another account', member.syncup_clash)

    def test_the_clash_clears_once_it_resolves(self):
        self.run_with(listing(person()))
        other = Member.objects.create(name='Somebody else', email='taken@example.com')
        self.run_with(listing(person(email='taken@example.com'), version='v2'))
        other.delete()
        self.run_with(listing(person(email='taken@example.com'), version='v3'))
        member = Member.objects.get(syncup_id='su-1')
        self.assertEqual(member.email, 'taken@example.com')
        self.assertEqual(member.syncup_clash, '')

    def test_the_syncup_password_replaces_the_spare_from_the_old_merge(self):
        self.run_with(listing(person()))
        member = Member.objects.get(syncup_id='su-1')
        Member.objects.filter(pk=member.pk).update(legacy_password_hash='an-old-one')
        fresh = make_password('a-new-syncup-password')
        self.run_with(listing(person(password=fresh), version='v2'))
        member.refresh_from_db()
        self.assertEqual(member.password_hash, fresh)
        self.assertEqual(member.legacy_password_hash, '')

    def test_a_hash_this_site_cannot_check_is_refused_and_flagged(self):
        """Installing argon2-cffi on SyncUp alone would otherwise lock everybody out with no
        error anywhere — just "wrong password", to all of them."""
        out = self.run_with(listing(person(password='argon2$argon2id$v=19$m=102400$xyz')))
        member = Member.objects.get(syncup_id='su-1')
        self.assertEqual(out['created'], 1)              # the person still arrives
        self.assertEqual(member.password_hash, '')       # but nothing unusable is stored
        self.assertFalse(syncup_people.hash_is_usable('argon2$argon2id$v=19$m=102400$xyz'))
        self.assertTrue(syncup_people.hash_is_usable(A_REAL_HASH))

    def test_leaving_syncup_switches_them_off_and_keeps_everything(self):
        self.run_with(listing(person(), person('su-2', email='b@example.com',
                                               phone='+919000000002', token='t2')))
        out = self.run_with(listing(person(), version='v2'))
        self.assertEqual(out['switched_off'], 1)
        gone = Member.objects.get(syncup_id='su-2')
        self.assertFalse(gone.is_active)
        self.assertTrue(Member.objects.filter(pk=gone.pk).exists())   # nothing deleted

    def test_coming_back_switches_them_on_again(self):
        self.run_with(listing(person(), person('su-2', email='b@example.com',
                                               phone='+919000000002', token='t2')))
        self.run_with(listing(person(), version='v2'))
        self.run_with(listing(person(), person('su-2', email='b@example.com',
                                               phone='+919000000002', token='t2'),
                              version='v3'))
        self.assertTrue(Member.objects.get(syncup_id='su-2').is_active)

    def test_an_empty_list_switches_nobody_off(self):
        """A misconfigured key must not take the whole site's people out in one tick."""
        self.run_with(listing(person()))
        out = self.run_with(listing(version='v2'))
        self.assertTrue(out['refused_empty'])
        self.assertTrue(Member.objects.get(syncup_id='su-1').is_active)
        self.assertIn('Nobody was switched off', HubSettings.load().syncup_people_note)

    def test_an_empty_list_is_fine_when_nothing_is_synced_yet(self):
        out = self.run_with(listing(version='v2'))
        self.assertNotIn('refused_empty', out)


class BatchingTests(_Synced):
    """The first import is the big one; the cron budget is shared with every other hook."""

    class Ctx:
        def __init__(self, stop_after):
            self.calls, self.stop_after = 0, stop_after

        def out_of_time(self):
            self.calls += 1
            return self.calls >= self.stop_after

    def many(self, n):
        return listing(*[person(f'su-{i}', email=f'p{i}@example.com',
                                phone=f'+9190000{i:05d}', token=f'tok{i}')
                         for i in range(n)], version='big')

    def test_it_stops_when_the_budget_runs_out_and_says_so(self):
        out = self.run_with(self.many(60), ctx=self.Ctx(stop_after=1))
        self.assertTrue(out['more_to_do'])
        self.assertEqual(Member.objects.count(), 25)     # the batch it got through

    def test_a_half_done_run_does_not_store_the_version(self):
        """Storing it would make the next call answer 'unchanged' and strand the rest."""
        self.run_with(self.many(60), ctx=self.Ctx(stop_after=1))
        self.assertEqual(HubSettings.load().syncup_people_version, '')

    def test_the_next_run_finishes_the_job(self):
        self.run_with(self.many(60), ctx=self.Ctx(stop_after=1))
        out = self.run_with(self.many(60), ctx=self.Ctx(stop_after=99))
        self.assertEqual(Member.objects.count(), 60)
        self.assertEqual(out['created'], 35)
        self.assertEqual(HubSettings.load().syncup_people_version, 'big')

    def test_a_half_done_run_switches_nobody_off(self):
        """The people it did not reach are not missing — it simply has not got to them."""
        self.run_with(self.many(60), ctx=self.Ctx(stop_after=99))
        out = self.run_with(self.many(60), ctx=self.Ctx(stop_after=1))
        self.assertEqual(out['switched_off'], 0)
        self.assertEqual(Member.objects.filter(is_active=False).count(), 0)


class NotConfiguredTests(TestCase):
    def test_it_says_what_is_missing_rather_than_trying(self):
        self.assertIn('SyncUp address', syncup_people.problem())
        hub = HubSettings.load()
        hub.syncup_base = 'https://syncup.example.com'
        hub.save()
        self.assertIn('links key', syncup_people.problem())
