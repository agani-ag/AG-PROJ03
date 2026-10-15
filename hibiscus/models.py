"""Hibiscus — a cycle diary. One row per day, not one row per period.

The old Flask app (Red Hibiscus) stored one row per period episode with a start date, an end date
and a single flow level for the whole thing. Here flow is recorded **per day** and a period is a
*run* of days, worked out by `cycle.py`. Nothing stores a start or an end date, which is why
"is it still going?" needs no answer: tomorrow is just another day's row.

The rules that turn days into periods live in `cycle.py` and nowhere else.
"""
from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone


class Flow(models.IntegerChoices):
    """How heavy a day was.

    `NOT_RECORDED` is deliberate rather than null: it means "this was a period day, the flow was
    never written down", which is what back-filled history honestly is. It counts as a period day
    and is left out of any flow breakdown. Clearing a day deletes its row instead.
    """
    NOT_RECORDED = 0, 'Not recorded'
    SPOTTING = 1, 'Spotting'
    LIGHT = 2, 'Light'
    MEDIUM = 3, 'Medium'
    HEAVY = 4, 'Heavy'

    @classmethod
    def counts_as_period(cls, value):
        """Spotting shows on the calendar but never starts or extends a period (the usual
        medical convention: day 1 is the first day of real flow)."""
        return value is not None and value != cls.SPOTTING


class Symptom(models.TextChoices):
    """The ten chips the Flask app had, unchanged — people recognise their own vocabulary."""
    CRAMPS = 'cramps', 'Cramps'
    HEADACHE = 'headache', 'Headache'
    BLOATING = 'bloating', 'Bloating'
    FATIGUE = 'fatigue', 'Fatigue'
    BACKACHE = 'backache', 'Backache'
    NAUSEA = 'nausea', 'Nausea'
    TENDER = 'tender', 'Tender breasts'
    MOODS = 'moods', 'Mood swings'
    ACNE = 'acne', 'Acne'
    CRAVINGS = 'cravings', 'Cravings'


class Person(models.Model):
    """Someone who keeps a diary here.

    Not a Django user: these people never reach the admin console, and the admin never appears in
    this table. They sign themselves up with an **email or a phone number** — one of the two.
    """
    name = models.CharField(max_length=80)
    email = models.EmailField(unique=True, null=True, blank=True)
    phone = models.CharField(max_length=15, unique=True, null=True, blank=True)
    password_hash = models.CharField(max_length=128)

    cycle_hint = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(15)],
        help_text='Average cycle they told us at sign-up, used until two periods are logged.')

    is_active = models.BooleanField(default=True)
    is_archived = models.BooleanField(default=False)
    signed_up_with = models.CharField(max_length=10, default='email')   # email | phone | admin
    created_at = models.DateTimeField(default=timezone.now)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    last_opened_at = models.DateTimeField(null=True, blank=True)
    open_count = models.PositiveIntegerField(default=0)

    # Captured from `window.SyncUp.token` when a page is opened inside the SyncUp app; without it
    # there is nothing to push to (see the notification plan).
    syncup_token = models.TextField(blank=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def sign_in_id(self):
        return self.email or self.phone or f'person-{self.pk}'


class PeriodDay(models.Model):
    """One day of a period. A run of these is a period — see `cycle.py`."""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='days')
    date = models.DateField()
    flow = models.IntegerField(choices=Flow.choices, default=Flow.MEDIUM)
    note = models.CharField(max_length=300, blank=True)

    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+',
                                   help_text='Set when the admin entered this on their behalf.')
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['person', 'date'], name='one_day_per_person')]
        indexes = [models.Index(fields=['person', 'date'])]
        ordering = ['-date']

    def __str__(self):
        return f"{self.person.name} · {self.date} · {self.get_flow_display()}"

    @property
    def counts(self):
        return Flow.counts_as_period(self.flow)


class DaySymptom(models.Model):
    """Symptoms belong to the day, like flow.

    The Flask app joined chips and free text into one comma-separated string, so a note containing
    a comma silently became extra "symptoms". This is that string, normalised.
    """
    day = models.ForeignKey(PeriodDay, on_delete=models.CASCADE, related_name='symptoms')
    symptom = models.CharField(max_length=20, choices=Symptom.choices)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['day', 'symptom'], name='one_symptom_per_day')]

    def __str__(self):
        return f"{self.day.date} · {self.get_symptom_display()}"


class StockItem(models.Model):
    """A consumable someone keeps track of — "Pads", "Sofy", whatever they call it.

    There is **no stored quantity**: `in_stock` is the sum of the movements. In the old app the
    running total was clamped at zero while the ledger kept the full change, so the two could
    drift apart for ever.
    """
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='items')
    name = models.CharField(max_length=60)
    low_at = models.PositiveSmallIntegerField(default=5,
                                              help_text='Low when the count reaches this. 0 turns the alert off.')
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['person', 'name'], name='one_item_name_per_person')]
        ordering = ['name']

    def __str__(self):
        return f"{self.person.name} · {self.name}"

    @property
    def in_stock(self):
        return self.moves.aggregate(n=models.Sum('change'))['n'] or 0

    @property
    def is_low(self):
        return bool(self.low_at) and self.in_stock <= self.low_at


class StockMove(models.Model):
    """A change to one item's count, with its own date so usage can be back-dated."""

    class Reason(models.TextChoices):
        USED = 'used', 'Used'
        RESTOCKED = 'restocked', 'Restocked'
        OPENING = 'opening', 'Opening stock'
        CORRECTION = 'correction', 'Correction'

    item = models.ForeignKey(StockItem, on_delete=models.CASCADE, related_name='moves')
    date = models.DateField()
    change = models.IntegerField(help_text='Negative for used, positive for restocked.')
    reason = models.CharField(max_length=12, choices=Reason.choices, default=Reason.USED)
    note = models.CharField(max_length=120, blank=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['item', '-date'])]
        ordering = ['-date', '-id']

    def __str__(self):
        return f"{self.item.name} {self.change:+d} on {self.date}"


class Audit(models.Model):
    """Who looked at or changed whose diary.

    The admin can see everything, notes included — that is how support and data entry work here.
    This table is the other side of that bargain.
    """
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name='+', help_text='Blank when the person acted themselves.')
    person = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='audit')
    action = models.CharField(max_length=40)          # viewed | added_day | deleted_day | ...
    detail = models.CharField(max_length=200, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['person', '-at'])]
        ordering = ['-at']

    def __str__(self):
        return f"{self.action} · {self.at:%Y-%m-%d %H:%M}"
