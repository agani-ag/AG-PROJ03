"""Everything Pocket Money knows how to work out, with no request in sight.

Views stay thin and this gets tested on its own, because the sums are the product. Three ideas
run through the whole file:

* **`PERSON_SIGN`** turns four kinds into one signed number per person. Positive means they owe
  you.
* **`MIRROR`** reads the same row from the other side. An entry is stored once, in the folder
  owner's orientation; a linked person writes through the mirror and reads through it, so the
  two sides can never drift apart.
* **`access()`** is the only thing that decides who may see or write anything. One function, one
  place to get it right, one place to test.

A note on the two columns a statement has: the kinds split by which way the cash actually moved.
`LENT` and `REPAID` are money leaving your pocket (`gave`); `COLLECTED` and `BORROWED` are money
arriving (`got`). That split survives mirroring — which is exactly why the labels can flip
between "Given / Back" and "Taken / Paid" without the arithmetic noticing.
"""
import csv
import io
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Optional

from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.utils import timezone

from . import auth
from .models import (MIRROR, PERSON_SIGN, Account, Category, EntryLog, Flow, Folder, FolderShare,
                     Kind, LedgerEntry, Person, Settings, TrackerEntry, clean_amount, new_code,
                     validate_name, validate_upi)

PAGE = 50
ZERO = Decimal('0.00')

#: Money out of your pocket, and money into it. Mirroring swaps the two, as it should.
GAVE_KINDS = {Kind.LENT, Kind.REPAID}
GOT_KINDS = {Kind.COLLECTED, Kind.BORROWED}

STARTER_CATEGORIES = [
    (Flow.IN, 'Salary', '#15803d'), (Flow.IN, 'Business', '#0d9488'), (Flow.IN, 'Gift', '#4f46e5'),
    (Flow.OUT, 'Food', '#dc2626'), (Flow.OUT, 'Rent', '#ea580c'), (Flow.OUT, 'Fuel', '#b45309'),
    (Flow.OUT, 'Bills', '#7c3aed'), (Flow.OUT, 'Medical', '#0d9488'), (Flow.OUT, 'Other', '#64748b'),
    (Flow.LEDGER, 'Medical', '#0d9488'), (Flow.LEDGER, 'Fees', '#4f46e5'),
    (Flow.LEDGER, 'Chit', '#b45309'), (Flow.LEDGER, 'Personal', '#64748b'),
]


# --------------------------------------------------------------------------- #
# Audit
# --------------------------------------------------------------------------- #
def log(scope, action, *, account=None, folder=None, entry_id=None, detail='',
        before=None, after=None):
    """One row of who-did-what. Cheap, and the only answer to an argument about a figure."""
    return EntryLog.objects.create(
        scope=scope, folder=folder, entry_id=entry_id, account=account,
        actor_name=(account.name if account else ''), action=action[:60], detail=str(detail)[:200],
        before=before, after=after)


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def join(member, config=None):
    """Start Pocket Money for somebody who is already signed in to the hub.

    No name, no email, no password: the hub knows all three. Starting a module is one tap, and
    the row that gets made *is* the membership — there is no separate join table to keep in
    step with reality.
    """
    config = config or Settings.load()
    existing = Account.objects.filter(member=member).first()
    if existing:
        return existing
    account = Account.objects.create(member=member, name=member.name)
    seed_categories(account)
    note_possible_links(member)
    return account


def seed_categories(account):
    """A starting set, written in code at sign-up — migrations never reach the server."""
    Category.objects.bulk_create([
        Category(account=account, flow=flow, name=name, colour=colour, sort_order=order)
        for order, (flow, name, colour) in enumerate(STARTER_CATEGORIES)
    ])


def categories(account, flow):
    return account.categories.filter(flow=flow, is_archived=False)


def add_category(account, flow, name, colour=''):
    name = (name or '').strip()[:30]
    if not name:
        raise ValidationError('A category needs a name.')
    if account.categories.filter(flow=flow, name__iexact=name).exists():
        raise ValidationError(f'There is already a {dict(Flow.choices)[flow].lower()} category '
                              f'called {name}.')
    used = account.categories.filter(flow=flow).count()
    colour = colour or Category.COLOURS[used % len(Category.COLOURS)]
    return Category.objects.create(account=account, flow=flow, name=name, colour=colour,
                                   sort_order=used)


# --------------------------------------------------------------------------- #
# Half one — the Tracker
# --------------------------------------------------------------------------- #
def add_tracker(account, flow, amount, *, category=None, date=None, source='', note='',
                config=None):
    config = config or Settings.load()
    flow = Flow(flow)
    if flow not in (Flow.IN, Flow.OUT):
        raise ValidationError('An entry is either money in or money out.')
    amount = clean_amount(amount)
    if category and (category.account_id != account.pk or category.flow != flow):
        raise ValidationError('That category belongs somewhere else.')
    entry = TrackerEntry.objects.create(
        account=account, flow=flow, amount=amount, category=category,
        date=date or config.today(), source=(source or '')[:20], note=(note or '')[:200])
    log('tracker', 'Added', account=account, entry_id=entry.pk, after=amount,
        detail=f'{flow} {amount}')
    return entry


def void_tracker(account, entry, reason=''):
    if entry.account_id != account.pk or entry.is_void:
        return entry
    entry.voided_at = timezone.now()
    entry.void_reason = (reason or '')[:120]
    entry.save(update_fields=['voided_at', 'void_reason'])
    log('tracker', 'Voided', account=account, entry_id=entry.pk, before=entry.amount,
        detail=reason)
    return entry


def tracker_month(account, year, month, config=None):
    """In, out, what is left, and where it went — the whole Tracker screen in one call."""
    config = config or Settings.load()
    rows = TrackerEntry.objects.filter(account=account, voided_at__isnull=True,
                                       date__year=year, date__month=month)
    totals = {flow: ZERO for flow in (Flow.IN, Flow.OUT)}
    for row in rows.values('flow').annotate(total=Sum('amount')):
        totals[row['flow']] = row['total'] or ZERO

    by_category = defaultdict(lambda: ZERO)
    colours, names = {}, {}
    for entry in rows.select_related('category'):
        key = entry.category_id or f'none-{entry.flow}'
        by_category[(entry.flow, key)] += entry.amount
        names[(entry.flow, key)] = entry.category.name if entry.category else 'Uncategorised'
        colours[(entry.flow, key)] = entry.category.colour if entry.category else '#94a3b8'

    def bars(flow):
        picked = [(names[k], colours[k], v) for k, v in by_category.items() if k[0] == flow]
        picked.sort(key=lambda row: row[2], reverse=True)
        top = picked[0][2] if picked else ZERO
        return [{'name': n, 'colour': c, 'amount': v,
                 'percent': int(v / top * 100) if top else 0} for n, c, v in picked]

    kept = totals[Flow.IN] - totals[Flow.OUT]
    share = int(kept / totals[Flow.IN] * 100) if totals[Flow.IN] else 0
    return {
        'year': year, 'month': month,
        'in': totals[Flow.IN], 'out': totals[Flow.OUT], 'kept': kept, 'kept_percent': share,
        'in_bars': bars(Flow.IN), 'out_bars': bars(Flow.OUT),
        'count': rows.count(),
    }


def tracker_entries(account, year=None, month=None, limit=PAGE, before=None):
    rows = TrackerEntry.objects.filter(account=account).select_related('category')
    if year and month:
        rows = rows.filter(date__year=year, date__month=month)
    if before:
        rows = rows.filter(pk__lt=before)
    page = list(rows[:limit + 1])
    return {'rows': page[:limit], 'has_more': len(page) > limit,
            'last_id': page[limit - 1].pk if len(page) >= limit else None}


# --------------------------------------------------------------------------- #
# Who may do what
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Access:
    """The answer to "may this account touch this folder, and how does it read it?"."""
    account: Account
    folder: Folder
    role: str                               # owner | editor | viewer | person
    person: Optional[Person] = None         # the line, when role == 'person'
    can_write: bool = False
    mirrored: bool = False                  # read and write through MIRROR

    @property
    def is_owner(self):
        return self.role == 'owner'

    @property
    def whole_folder(self):
        return self.role in ('owner', 'editor', 'viewer')

    @property
    def can_manage(self):
        """Rename, share, invite, delete — the owner's alone, deliberately."""
        return self.role == 'owner'


def access(account, folder, person=None):
    """What `account` may do with `folder`, or None. The module's whole security surface.

    Pass `person` when the view is about one line: a linked person may only ever reach their own.
    """
    if not account or not folder:
        return None

    if folder.owner_id == account.pk:
        return Access(account, folder, 'owner', person=person, can_write=True)

    share = FolderShare.objects.filter(folder=folder, account=account,
                                       accepted_at__isnull=False,
                                       declined_at__isnull=True).first()
    if share:
        return Access(account, folder, 'editor' if share.can_edit else 'viewer', person=person,
                      can_write=share.can_edit)

    mine = Person.objects.filter(folder=folder, linked_account=account,
                                 linked_at__isnull=False, declined_at__isnull=True).first()
    if mine and (person is None or person.pk == mine.pk):
        return Access(account, folder, 'person', person=mine, can_write=mine.can_edit,
                      mirrored=True)
    return None


def visible_folders(account):
    """Folders they own, and folders shared with them — kept apart on purpose."""
    own = Folder.objects.filter(owner=account).order_by('is_archived', 'name')
    shared = Folder.objects.filter(shares__account=account, shares__accepted_at__isnull=False,
                                   shares__declined_at__isnull=True).order_by('name')
    return own, shared


# --------------------------------------------------------------------------- #
# Half two — balances
# --------------------------------------------------------------------------- #
def person_balance(person):
    """Signed, in the owner's orientation: positive means this person owes the owner."""
    rows = (LedgerEntry.objects.filter(person=person, voided_at__isnull=True)
            .values('kind').annotate(total=Sum('amount')))
    return sum((row['total'] * PERSON_SIGN[Kind(row['kind'])] for row in rows), ZERO)


def folder_balances(folder):
    """`{person_id: balance}` for every person in the folder, in one query."""
    rows = (LedgerEntry.objects.filter(folder=folder, voided_at__isnull=True)
            .values('person', 'kind').annotate(total=Sum('amount')))
    out = defaultdict(lambda: ZERO)
    for row in rows:
        out[row['person']] += row['total'] * PERSON_SIGN[Kind(row['kind'])]
    return out


def folder_people(folder):
    """People with their balances attached, newest activity first among the unsettled."""
    balances = folder_balances(folder)
    people = list(folder.people.all())
    for person in people:
        person.balance = balances.get(person.pk, ZERO)
    people.sort(key=lambda p: (p.is_archived, p.balance == ZERO, -abs(p.balance), p.name))
    return people


def folder_totals(folder):
    balances = folder_balances(folder)
    owed_to_you = sum((b for b in balances.values() if b > 0), ZERO)
    you_owe = -sum((b for b in balances.values() if b < 0), ZERO)
    return {'owed_to_you': owed_to_you, 'you_owe': you_owe,
            'people': folder.people.count(),
            'open': len([b for b in balances.values() if b != ZERO])}


def account_position(account):
    """Across the folders they own. Books shared *with* them are somebody else's money."""
    owed_to_you = you_owe = ZERO
    for folder in Folder.objects.filter(owner=account, is_archived=False):
        totals = folder_totals(folder)
        owed_to_you += totals['owed_to_you']
        you_owe += totals['you_owe']
    return {'owed_to_you': owed_to_you, 'you_owe': you_owe, 'net': owed_to_you - you_owe}


# --------------------------------------------------------------------------- #
# Half two — writing
# --------------------------------------------------------------------------- #
def add_ledger(acc, person, kind, amount, *, category=None, date=None, source='', note='',
               due_on=None, config=None):
    """Write one entry, always in the folder owner's orientation.

    `acc` has already answered whether this account may write here. When it reads the folder
    through the mirror — a linked person — the kind they chose is flipped on the way in, which
    is what makes "Mani taps Paid back" land as `COLLECTED` in Anand's book.
    """
    config = config or Settings.load()
    if not acc or not acc.can_write:
        raise ValidationError('You can read this, not change it.')
    if person.folder_id != acc.folder.pk:
        raise ValidationError('That person is in another folder.')
    if acc.role == 'person' and acc.person and person.pk != acc.person.pk:
        raise ValidationError('That is not your line.')

    kind = Kind(kind)
    stored = MIRROR[kind] if acc.mirrored else kind
    amount = clean_amount(amount)
    if category and (category.flow != Flow.LEDGER or category.account_id != acc.folder.owner_id):
        category = None                      # a shared writer's own categories are not the book's

    entry = LedgerEntry.objects.create(
        folder=acc.folder, person=person, kind=stored, amount=amount, category=category,
        date=date or config.today(), source=(source or '')[:20], note=(note or '')[:200],
        due_on=due_on, created_by=acc.account)
    log('ledger', 'Added', account=acc.account, folder=acc.folder, entry_id=entry.pk,
        after=amount, detail=f'{stored} {amount} · {person.name}')
    return entry


def void_ledger(acc, entry, reason=''):
    """Only the owner and a folder editor may void. A linked person adds and nothing else."""
    if not acc or acc.role == 'person' or not acc.can_write:
        raise ValidationError('Only the people who keep this book can take an entry back.')
    if entry.is_void:
        return entry
    entry.voided_at = timezone.now()
    entry.voided_by = acc.account
    entry.void_reason = (reason or '')[:120]
    entry.save(update_fields=['voided_at', 'voided_by', 'void_reason'])
    log('ledger', 'Voided', account=acc.account, folder=acc.folder, entry_id=entry.pk,
        before=entry.amount, detail=reason)
    return entry


def settle_up(acc, person, config=None):
    """The one entry that takes a balance to zero — the only moment the figure must be exact."""
    balance = person_balance(person)
    if balance == ZERO:
        return None
    # The balance is already in the owner's orientation, so the entry is written straight
    # through — whichever side tapped the button.
    kind = Kind.COLLECTED if balance > 0 else Kind.REPAID
    return add_ledger(_unmirrored(acc), person, kind, abs(balance), note='Settled up',
                      config=config)


def _unmirrored(acc):
    """A copy of this access that writes in the owner's orientation, for a computed entry."""
    return Access(acc.account, acc.folder, acc.role, person=acc.person,
                  can_write=acc.can_write, mirrored=False)


# --------------------------------------------------------------------------- #
# Half two — reading one line
# --------------------------------------------------------------------------- #
def statement(person, *, mirrored=False, limit=PAGE, before=None):
    """One person's rows, as the reader should see them.

    `gave` is money out of the reader's pocket and `got` is money in, which is why mirroring
    needs no special case here: flipping the kind moves the amount to the other column by
    itself.
    """
    rows = (LedgerEntry.objects.filter(person=person)
            .select_related('category', 'created_by').order_by('-date', '-created_at'))
    if before:
        rows = rows.filter(pk__lt=before)
    page = list(rows[:limit + 1])
    has_more, page = len(page) > limit, page[:limit]

    out = []
    gave_total = got_total = ZERO
    for entry in page:
        kind = Kind(MIRROR[Kind(entry.kind)] if mirrored else entry.kind)
        gave = entry.amount if kind in GAVE_KINDS else None
        got = entry.amount if kind in GOT_KINDS else None
        if not entry.is_void:
            gave_total += gave or ZERO
            got_total += got or ZERO
        out.append({'entry': entry, 'kind': kind, 'label': Kind(kind).label,
                    'gave': gave, 'got': got, 'who': entry.created_by})

    balance = person_balance(person)
    if mirrored:
        balance = -balance
    return {'rows': out, 'has_more': has_more,
            'last_id': page[-1].pk if page else None,
            'gave': gave_total, 'got': got_total, 'balance': balance,
            'they_owe': balance > 0, 'you_owe': balance < 0}


def folder_entries(folder, *, limit=PAGE, before=None):
    rows = (LedgerEntry.objects.filter(folder=folder)
            .select_related('person', 'category', 'created_by', 'voided_by'))
    if before:
        rows = rows.filter(pk__lt=before)
    page = list(rows[:limit + 1])
    return {'rows': page[:limit], 'has_more': len(page) > limit,
            'last_id': page[limit - 1].pk if len(page) >= limit else None}


def due_entries(account, config=None):
    """What is owed to this account with a date on it, soonest first."""
    config = config or Settings.load()
    today = config.today()
    rows = (LedgerEntry.objects.filter(folder__owner=account, voided_at__isnull=True,
                                       due_on__isnull=False, kind=Kind.LENT)
            .select_related('person', 'folder').order_by('due_on'))
    out = []
    for entry in rows:
        if person_balance(entry.person) <= ZERO:
            continue                                  # already squared up
        out.append({'entry': entry, 'late': (today - entry.due_on).days})
    return out


# --------------------------------------------------------------------------- #
# People, and turning one into a user
# --------------------------------------------------------------------------- #
def add_person(acc, name, *, phone='', upi_id='', note=''):
    if not acc or not acc.can_write or acc.role == 'person':
        raise ValidationError('Only the people who keep this book can add to it.')
    name = validate_name(name)
    person = Person.objects.create(
        folder=acc.folder, name=name, phone=auth.normalise_phone(phone) if phone else '',
        upi_id=validate_upi(upi_id), note=(note or '')[:200], colour=_colour_for(name))
    log('link', 'Added a person', account=acc.account, folder=acc.folder, detail=name)
    return person


def _colour_for(name):
    palette = ['#7c3aed', '#0d9488', '#db2777', '#ea580c', '#4f46e5', '#0ea5e9', '#b45309']
    return palette[sum(ord(c) for c in (name or 'x')) % len(palette)]


def invite_person(person, *, can_edit=False, by=None):
    """Offer this person their own line. The code is the whole credential, used once."""
    person.link_code = new_code()
    person.link_made_at = timezone.now()
    person.can_edit = bool(can_edit)
    person.declined_at = None
    person.save(update_fields=['link_code', 'link_made_at', 'can_edit', 'declined_at'])
    log('link', 'Invited', account=by, folder=person.folder, detail=person.name)
    return person.link_code


def person_by_code(code, config=None):
    """The invitation behind a code, if it is real and still alive."""
    config = config or Settings.load()
    code = (code or '').replace('-', '').strip().upper()
    if not code:
        return None
    person = Person.objects.filter(link_code=code, linked_account__isnull=True,
                                   declined_at__isnull=True).first()
    if not person or not person.link_made_at:
        return None
    if timezone.now() - person.link_made_at > timedelta(days=config.link_days):
        return None
    return person


def accept_person_invite(person, account):
    """Both sides have now said yes; the row becomes a line."""
    if person.linked_account_id:
        raise ValidationError('Somebody has already taken that invitation.')
    if person.folder.owner_id == account.pk:
        raise ValidationError('That is your own folder.')
    person.linked_account = account
    person.linked_at = timezone.now()
    person.link_code = None
    person.suggested_account = None
    person.save(update_fields=['linked_account', 'linked_at', 'link_code', 'suggested_account'])
    log('link', 'Accepted', account=account, folder=person.folder, detail=person.name)
    return person


def decline_person_invite(person, account=None):
    person.declined_at = timezone.now()
    person.link_code = None
    person.suggested_account = None
    person.save(update_fields=['declined_at', 'link_code', 'suggested_account'])
    log('link', 'Declined', account=account, folder=person.folder, detail=person.name)
    return person


def unlink_person(person, by=None):
    """The entries stay — they are the owner's records. Only the seeing stops."""
    person.linked_account = None
    person.linked_at = None
    person.can_edit = False
    person.link_code = None
    person.save(update_fields=['linked_account', 'linked_at', 'can_edit', 'link_code'])
    log('link', 'Cut the link', account=by, folder=person.folder, detail=person.name)
    return person


def note_possible_links(member):
    """Somebody just started Pocket Money. Flag the rows that might be them — and stop there.

    A phone number match is a suggestion, never a link: numbers get recycled, and linking on a
    match alone would hand a stranger somebody's debt record. The owner has to confirm, and then
    the new account has to accept.
    """
    account = Account.objects.filter(member=member).first()
    tail = auth.normalise_phone(getattr(member, 'phone', '') or '')
    if not account or len(tail) < 10:
        return 0
    rows = Person.objects.filter(phone__endswith=tail, linked_account__isnull=True,
                                 declined_at__isnull=True, suggested_account__isnull=True)
    return rows.exclude(folder__owner=account).update(suggested_account=account)


def suggestions_for(account):
    """Rows in this account's own folders that look like somebody who has just signed up."""
    return (Person.objects.filter(folder__owner=account, suggested_account__isnull=False,
                                  linked_account__isnull=True, declined_at__isnull=True)
            .select_related('folder', 'suggested_account'))


def dismiss_suggestion(person, by=None):
    person.suggested_account = None
    person.save(update_fields=['suggested_account'])
    log('link', 'Dismissed a suggestion', account=by, folder=person.folder, detail=person.name)


def pending_person_invites(account):
    """Invitations addressed to this account — the ones a confirmed suggestion creates."""
    return (Person.objects.filter(suggested_account=account, link_code__isnull=False,
                                  linked_account__isnull=True, declined_at__isnull=True)
            .select_related('folder', 'folder__owner'))


def linked_lines(account):
    """Lines where this account is the person in somebody else's folder."""
    return (Person.objects.filter(linked_account=account, linked_at__isnull=False,
                                  declined_at__isnull=True)
            .select_related('folder', 'folder__owner'))


# --------------------------------------------------------------------------- #
# Sharing a whole folder
# --------------------------------------------------------------------------- #
def share_folder(folder, *, can_edit=False, by=None):
    share = FolderShare.objects.create(folder=folder, can_edit=can_edit, invited_by=by)
    log('link', 'Shared the folder', account=by, folder=folder,
        detail='can edit' if can_edit else 'view only')
    return share


def share_by_code(code, config=None):
    config = config or Settings.load()
    code = (code or '').replace('-', '').strip().upper()
    if not code:
        return None
    share = FolderShare.objects.filter(code=code, account__isnull=True,
                                       declined_at__isnull=True).first()
    if not share:
        return None
    if timezone.now() - share.created_at > timedelta(days=config.link_days):
        return None
    return share


def accept_share(share, account):
    if share.folder.owner_id == account.pk:
        raise ValidationError('That is your own folder.')
    if FolderShare.objects.filter(folder=share.folder, account=account).exists():
        raise ValidationError('You already have that folder.')
    share.account = account
    share.accepted_at = timezone.now()
    share.save(update_fields=['account', 'accepted_at'])
    log('link', 'Joined the folder', account=account, folder=share.folder)
    return share


def stop_share(share, by=None):
    folder, who = share.folder, share.account
    share.delete()
    log('link', 'Stopped sharing', account=by, folder=folder,
        detail=who.name if who else 'an unused link')


# --------------------------------------------------------------------------- #
# UPI
# --------------------------------------------------------------------------- #
def upi_link(vpa, name=''):
    """A redirect and nothing more: payee, name, currency.

    No `am`, so the amount is typed inside the UPI app where the PIN is typed; no `tr`, because
    nothing is expected back. SyncUp's own spec calls the result it can hand back information
    and never proof, and that half of the app is not built — so the ledger entry is a separate,
    deliberate tap.
    """
    vpa = (vpa or '').strip()
    if not vpa:
        return ''
    query = {'pa': vpa, 'cu': 'INR'}
    if name:
        query['pn'] = name
    return 'upi://pay?' + urllib.parse.urlencode(query)


def parse_upi(text):
    """Pull the UPI ID out of a scanned QR. Anything else is refused.

    An amount printed in the QR is dropped on purpose: a shop's code often carries a fixed
    figure that has nothing to do with what you owe.
    """
    raw = (text or '').strip()
    if not raw:
        return '', ''
    if raw.lower().startswith('upi://'):
        parts = urllib.parse.urlparse(raw)
        query = urllib.parse.parse_qs(parts.query)
        vpa = (query.get('pa') or [''])[0].strip()
        name = (query.get('pn') or [''])[0].strip()
        return validate_upi(vpa), name[:40]
    return validate_upi(raw), ''


# --------------------------------------------------------------------------- #
# Taking it away with you
# --------------------------------------------------------------------------- #
def tracker_csv(account, year=None, month=None):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Date', 'In or out', 'Amount', 'Category', 'Source', 'Note', 'Voided'])
    rows = TrackerEntry.objects.filter(account=account).select_related('category')
    if year and month:
        rows = rows.filter(date__year=year, date__month=month)
    for entry in rows:
        writer.writerow([entry.date, entry.get_flow_display(), entry.amount,
                         entry.category.name if entry.category else '', entry.source,
                         entry.note, 'yes' if entry.is_void else ''])
    return buffer.getvalue()


def folder_csv(folder):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(['Date', 'Person', 'What happened', 'Amount', 'What for', 'Source', 'Note',
                     'Due', 'Entered by', 'Voided'])
    for entry in (LedgerEntry.objects.filter(folder=folder)
                  .select_related('person', 'category', 'created_by')):
        writer.writerow([entry.date, entry.person.name, Kind(entry.kind).label, entry.amount,
                         entry.category.name if entry.category else '', entry.source, entry.note,
                         entry.due_on or '', entry.created_by.name if entry.created_by else '',
                         'yes' if entry.is_void else ''])
    return buffer.getvalue()
