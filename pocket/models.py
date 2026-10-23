"""What Pocket Money stores.

Two halves that never share a balance:

* **Tracker** — your own money. Two flows, `in` and `out`, every entry with a category, and the
  categories are *typed*: an income category can never land on an expense. Nobody else is in it.
* **Ledger** — folders of people. Four kinds between you and each person, each with a category.
  A folder is the book: it is the thing that gets shared, and the thing a person belongs to.

The one piece of arithmetic worth staring at is `PERSON_SIGN` and `MIRROR` below. Everything
about who owes whom, on either side of a shared line, comes out of those two dictionaries.
"""
import re
import secrets
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

NAME_MIN, NAME_MAX = 2, 40
AMOUNT_MIN = Decimal('0.01')
AMOUNT_MAX = Decimal('99999999.99')
CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'      # no look-alikes: these get read aloud


# --------------------------------------------------------------------------- #
# The vocabulary
# --------------------------------------------------------------------------- #
class Flow(models.TextChoices):
    """What a Tracker entry — or the category it may carry — is for."""
    IN = 'in', 'Money in'
    OUT = 'out', 'Money out'
    LEDGER = 'ledger', 'Ledger'          # categories only: what a loan was *for*


class Kind(models.TextChoices):
    """The four things that happen between two people. Phrased from the folder owner's side."""
    LENT = 'lent', 'I gave'
    COLLECTED = 'collected', 'Got back'
    BORROWED = 'borrowed', 'I took'
    REPAID = 'repaid', 'Paid back'


#: What one entry does to *their* balance. Positive means they owe you.
PERSON_SIGN = {
    Kind.LENT: 1,
    Kind.COLLECTED: -1,
    Kind.BORROWED: -1,
    Kind.REPAID: 1,
}

#: The same row read from the other side. "I gave you" is "I took from you" to you.
#: An involution — apply it twice and you are back where you started — which is what makes a
#: shared line impossible to drift out of agreement.
MIRROR = {
    Kind.LENT: Kind.BORROWED,
    Kind.BORROWED: Kind.LENT,
    Kind.COLLECTED: Kind.REPAID,
    Kind.REPAID: Kind.COLLECTED,
}

#: Which way a kind points for the person writing it, used to label the two statement columns.
GIVEN_KINDS = {Kind.LENT, Kind.BORROWED}        # money moved *to* the other side's advantage


def mirrored(kind):
    return MIRROR[Kind(kind)]


# --------------------------------------------------------------------------- #
# Small validators
# --------------------------------------------------------------------------- #
UPI_RE = re.compile(r'^[A-Za-z0-9._-]{2,64}@[A-Za-z][A-Za-z0-9.-]{1,63}$')


def validate_name(value):
    name = (value or '').strip()
    if len(name) < NAME_MIN:
        raise ValidationError(f'A name needs at least {NAME_MIN} characters.')
    if len(name) > NAME_MAX:
        raise ValidationError(f'A name can be at most {NAME_MAX} characters.')
    return name


def validate_upi(value):
    """Light on purpose: handles differ between banks, and a strict rule rejects real IDs."""
    upi = (value or '').strip()
    if not upi:
        return ''
    if not UPI_RE.match(upi):
        raise ValidationError('That does not look like a UPI ID — it should read name@bank.')
    return upi


def clean_amount(value):
    """Accept what a person types: `1,200.50`, `1200`, `₹300`. Give back a Decimal of paise."""
    raw = str(value or '').strip().replace(',', '').replace('₹', '').strip()
    if not raw:
        raise ValidationError('How much?')
    try:
        amount = Decimal(raw).quantize(Decimal('0.01'))
    except (InvalidOperation, ValueError):
        raise ValidationError('That is not an amount.')
    if amount < AMOUNT_MIN:
        raise ValidationError('An amount has to be more than nothing.')
    if amount > AMOUNT_MAX:
        raise ValidationError('That is more than this will hold.')
    return amount


def new_code(length=8):
    """A short code somebody might read out: `4K7P2QMD`, shown as `4K7P-2QMD`."""
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(length))


def pretty_code(code):
    return f'{code[:4]}-{code[4:]}' if code and len(code) == 8 else (code or '')


# --------------------------------------------------------------------------- #
# Settings — one row, pk=1, like every other module's
# --------------------------------------------------------------------------- #
class Settings(models.Model):
    signup_open = models.BooleanField(default=True)
    invite_code = models.CharField(max_length=24, blank=True,
                                   help_text='Blank = anyone may sign up.')
    currency_symbol = models.CharField(max_length=4, default='₹')
    time_zone = models.CharField(max_length=40, default='Asia/Kolkata',
                                 help_text='Whose "today" an entry is dated by.')

    push_enabled = models.BooleanField(default=True)
    notify_quiet_from = models.TimeField(default='22:00')
    notify_quiet_to = models.TimeField(default='06:30')
    reminder_hour = models.PositiveSmallIntegerField(
        default=9, help_text='When a due or overdue amount is mentioned.')
    coalesce_minutes = models.PositiveSmallIntegerField(
        default=5, help_text='One notification per person per folder in this many minutes. '
                             'Without it a busy shared book is a notification every ninety seconds.')
    link_days = models.PositiveSmallIntegerField(
        default=7, help_text='How long an unused invitation or share link lasts.')
    keep_audit_days = models.PositiveSmallIntegerField(default=365)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')

    class Meta:
        verbose_name = verbose_name_plural = 'pocket money settings'

    def __str__(self):
        return 'Pocket Money settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def tzinfo(self):
        try:
            return ZoneInfo(self.time_zone)
        except (ZoneInfoNotFoundError, ValueError):
            return timezone.get_current_timezone()

    def today(self):
        return timezone.now().astimezone(self.tzinfo).date()

    def now_local(self):
        return timezone.now().astimezone(self.tzinfo)

    def is_quiet(self, when=None):
        """Quiet hours wrap midnight, which is the only reason this is not a comparison."""
        now = (when or self.now_local()).time()
        start, end = self.notify_quiet_from, self.notify_quiet_to
        if start == end:
            return False
        if start < end:
            return start <= now < end
        return now >= start or now < end


# --------------------------------------------------------------------------- #
# Who signs in
# --------------------------------------------------------------------------- #
class Account(models.Model):
    """Somebody with their own Tracker and their own folders.

    Not a Django user — they never reach the console — so Pocket Money keeps its own hash and
    its own session key, exactly as Hibiscus does for the people who keep a diary.
    """
    THEMES = [('system', 'System'), ('light', 'Light'), ('dark', 'Dark')]

    member = models.OneToOneField('core.Member', null=True, blank=True,
                                  on_delete=models.CASCADE, related_name='pocket')
    name = models.CharField(max_length=NAME_MAX)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=15, unique=True, null=True, blank=True)
    password_hash = models.CharField(max_length=128)

    upi_id = models.CharField(max_length=80, blank=True,
                              help_text='Shown to the people you share a line or a folder with, '
                                        'so they can pay you back.')
    opening_balance = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('0.00'))
    opening_on = models.DateField(null=True, blank=True)

    syncup_token = models.TextField(blank=True)
    notify = models.BooleanField(default=True)
    theme = models.CharField(max_length=10, choices=THEMES, default='system')

    is_active = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    open_count = models.PositiveIntegerField(default=0)

    login_key = models.CharField(max_length=64, unique=True, null=True, blank=True)
    login_key_made_at = models.DateTimeField(null=True, blank=True)
    login_key_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def initials(self):
        parts = [p for p in (self.name or '?').split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return (self.name or '?')[:2].upper()

    @property
    def sign_in_id(self):
        return self.email or self.phone or ''

    @property
    def push_token(self):
        """One device token per person, on the member. See `core.push.token_of`."""
        from core.push import token_of
        return token_of(self)

    @property
    def can_be_pushed(self):
        return bool(self.push_token and self.notify and self.is_active)


class Category(models.Model):
    """A label, typed by flow. `Salary` is an income category and will never be offered on an
    expense — that rule is the only reason the month breakdown is worth opening."""
    COLOURS = ['#4f46e5', '#0d9488', '#15803d', '#b45309', '#dc2626', '#db2777', '#7c3aed',
               '#0ea5e9', '#64748b']

    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='categories')
    name = models.CharField(max_length=30)
    flow = models.CharField(max_length=8, choices=Flow.choices)
    colour = models.CharField(max_length=7, default='#64748b')
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_archived = models.BooleanField(default=False)

    class Meta:
        ordering = ['sort_order', 'name']
        constraints = [models.UniqueConstraint(fields=['account', 'flow', 'name'],
                                               name='pocket_category_unique')]

    def __str__(self):
        return self.name


class TrackerEntry(models.Model):
    """Half one. Your own money, and nobody else's."""
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='tracker')
    date = models.DateField()
    flow = models.CharField(max_length=4, choices=[(Flow.IN, 'In'), (Flow.OUT, 'Out')])
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='tracker_entries')
    source = models.CharField(max_length=20, blank=True)        # Cash · Bank · UPI
    note = models.CharField(max_length=200, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=120, blank=True)

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [models.Index(fields=['account', '-date'])]

    def __str__(self):
        return f'{self.flow} {self.amount}'

    @property
    def is_void(self):
        return self.voided_at is not None

    @property
    def signed(self):
        return self.amount if self.flow == Flow.IN else -self.amount


# --------------------------------------------------------------------------- #
# Half two — the Ledger
# --------------------------------------------------------------------------- #
class Folder(models.Model):
    """The book. It owns the people in it, and it is the unit of sharing."""
    owner = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='folders')
    name = models.CharField(max_length=40)
    note = models.CharField(max_length=200, blank=True)
    colour = models.CharField(max_length=7, default='#4f46e5')
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)
    last_push_at = models.DateTimeField(null=True, blank=True,
                                        help_text="When the owner last heard about this folder. "
                                                  'Everyone else has the same clock on their '
                                                  'share or person row.')

    class Meta:
        ordering = ['is_archived', 'name']

    def __str__(self):
        return self.name


class FolderShare(models.Model):
    """A whole folder handed to another account: every person in it, every entry, the totals.

    The row exists from the moment the link is made, which is why `account` is empty until
    somebody accepts — the link has to work for a person who has not signed up yet.
    """
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name='shares')
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.CASCADE,
                                related_name='shared_folders')
    can_edit = models.BooleanField(default=False)
    code = models.CharField(max_length=16, unique=True, default=new_code)
    invited_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    accepted_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    notify = models.BooleanField(default=True)
    last_push_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['folder', 'account'],
                                               name='pocket_share_unique')]

    def __str__(self):
        return f'{self.folder} → {self.account or self.code}'

    @property
    def is_open(self):
        return self.accepted_at is not None and self.declined_at is None


class Person(models.Model):
    """Somebody you keep an account with, inside one folder.

    Typed in by the folder's owner. If they ever sign up for Pocket Money, `linked_account`
    turns this row into a line both sides can see — see `service.invite_person`.
    """
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name='people')
    name = models.CharField(max_length=NAME_MAX)
    phone = models.CharField(max_length=15, blank=True)
    upi_id = models.CharField(max_length=80, blank=True)
    note = models.CharField(max_length=200, blank=True)
    colour = models.CharField(max_length=7, default='#7c3aed')
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(default=timezone.now)

    linked_account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL,
                                       related_name='linked_as')
    can_edit = models.BooleanField(default=False,
                                   help_text='A linked person may add entries. Never edit or void.')
    link_code = models.CharField(max_length=16, unique=True, null=True, blank=True)
    link_made_at = models.DateTimeField(null=True, blank=True)
    linked_at = models.DateTimeField(null=True, blank=True)
    declined_at = models.DateTimeField(null=True, blank=True)
    notify = models.BooleanField(default=True)
    last_push_at = models.DateTimeField(null=True, blank=True)
    suggested_account = models.ForeignKey(Account, null=True, blank=True,
                                          on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['is_archived', 'name']

    def __str__(self):
        return self.name

    @property
    def initials(self):
        parts = [p for p in (self.name or '?').split() if p]
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return (self.name or '?')[:2].upper()

    @property
    def is_linked(self):
        return self.linked_account_id is not None

    @property
    def is_invited(self):
        return bool(self.link_code) and not self.is_linked and self.declined_at is None


class LedgerEntry(models.Model):
    """One of the four things, between the folder's owner and one person.

    Always stored in the **owner's** orientation. When a linked person writes one, the kind is
    mirrored on the way in; when they read it, it is mirrored on the way out. One row, one
    truth, two readings.
    """
    folder = models.ForeignKey(Folder, on_delete=models.CASCADE, related_name='entries')
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='entries')
    kind = models.CharField(max_length=10, choices=Kind.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField()
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name='ledger_entries')
    source = models.CharField(max_length=20, blank=True)
    note = models.CharField(max_length=200, blank=True)
    due_on = models.DateField(null=True, blank=True)

    created_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL,
                                  related_name='+')
    void_reason = models.CharField(max_length=120, blank=True)
    due_pushed_on = models.DateField(null=True, blank=True,
                                     help_text='Last date a reminder went out, so it goes once a day.')

    class Meta:
        ordering = ['-date', '-created_at']
        indexes = [models.Index(fields=['folder', '-date']), models.Index(fields=['person'])]

    def __str__(self):
        return f'{self.kind} {self.amount}'

    @property
    def is_void(self):
        return self.voided_at is not None

    @property
    def signed(self):
        """What this does to the person's balance, from the owner's side."""
        return self.amount * PERSON_SIGN[Kind(self.kind)]


class EntryLog(models.Model):
    """Who did what. J6 deliberately keeps no audit trail; money between two people is exactly
    where that argument flips — "who turned ₹500 into ₹5,000" has to have an answer."""
    SCOPES = [('tracker', 'Tracker'), ('ledger', 'Ledger'), ('link', 'Link')]

    scope = models.CharField(max_length=8, choices=SCOPES)
    folder = models.ForeignKey(Folder, null=True, blank=True, on_delete=models.CASCADE,
                               related_name='logs')
    entry_id = models.PositiveIntegerField(null=True, blank=True)
    account = models.ForeignKey(Account, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name='+')
    actor_name = models.CharField(max_length=NAME_MAX, blank=True)
    action = models.CharField(max_length=60)
    detail = models.CharField(max_length=200, blank=True)
    before = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    after = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-at']

    def __str__(self):
        return f'{self.actor_name}: {self.action}'
