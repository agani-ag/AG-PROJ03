"""Hibiscus — a cycle diary. One row per day, not one row per period.

The old Flask app (Red Hibiscus) stored one row per period episode with a start date, an end date
and a single flow level for the whole thing. Here flow is recorded **per day** and a period is a
*run* of days, worked out by `cycle.py`. Nothing stores a start or an end date, which is why
"is it still going?" needs no answer: tomorrow is just another day's row.

The rules that turn days into periods live in `cycle.py` and nowhere else.
"""
import zoneinfo

from django.contrib.auth.models import User
from django.core.validators import MinValueValidator
from django.db import models
from django.urls import reverse
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


class Settings(models.Model):
    """One row (pk=1) holding what used to be constants in the Flask file.

    `cycle.py` takes these as arguments rather than reading them, so the rules stay pure and the
    numbers stay visible on a settings page.
    """
    display_name = models.CharField(max_length=40, default='Hibiscus')
    time_zone = models.CharField(max_length=40, default='Asia/Kolkata',
                                 help_text='Whose "today" the diary follows.')
    week_starts_sunday = models.BooleanField(default=True)

    default_cycle = models.PositiveSmallIntegerField(default=28)
    default_period = models.PositiveSmallIntegerField(default=5)
    luteal_days = models.PositiveSmallIntegerField(default=14)
    bridge_days = models.PositiveSmallIntegerField(
        default=1, help_text='Missing days a period is allowed to span. 1 = one forgotten day.')

    signup_open = models.BooleanField(default=True)
    invite_code = models.CharField(max_length=24, blank=True,
                                   help_text='Blank = anyone may sign up.')
    keep_audit_days = models.PositiveSmallIntegerField(default=180)

    # Where SyncUp lives and what this site is called are *hub* settings now, not this module's:
    # see core.models.HubSettings and System → Settings. They were duplicated here and in J6,
    # which meant two places to set one value. What stays here is what only the diary knows.
    notify_quiet_from = models.TimeField(default='22:00')
    notify_quiet_to = models.TimeField(default='06:00')
    notify_late_hours = models.PositiveSmallIntegerField(
        default=2, help_text='Drop a slot that is later than this — a stale nudge is worse than none.')

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')

    class Meta:
        verbose_name = verbose_name_plural = 'settings'

    def __str__(self):
        return self.display_name

    def save(self, *args, **kwargs):
        self.pk = 1                       # one row, always
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def today(self):
        """The diary's local date. Never `date.today()`: on a UTC server that rolls over at
        05:30 IST, so anything logged before then landed on the previous day in the old app."""
        return timezone.localtime(timezone.now(), self.tzinfo).date()

    @property
    def tzinfo(self):
        try:
            return zoneinfo.ZoneInfo(self.time_zone)
        except Exception:                 # noqa: BLE001 — a bad name must not break every page
            return timezone.get_current_timezone()


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

    # A magic link the admin hands out: whoever opens it is signed in as this person, with no
    # expiry and no password. That is the point of it — one tap from a saved bookmark or a chat
    # message. The trade is that the link *is* the credential, so the admin can replace it (the
    # old one dies at once) or switch it off, every use is audited, and a suspended account is
    # refused like any other sign-in.
    login_key = models.CharField(max_length=64, unique=True, null=True, blank=True,
                                 help_text='Null means this person has no magic link.')
    login_key_made_at = models.DateTimeField(null=True, blank=True)
    login_key_used_at = models.DateTimeField(null=True, blank=True)
    login_key_uses = models.PositiveIntegerField(default=0)

    THEMES = (('system', 'Follow the phone'), ('light', 'Light'), ('dark', 'Dark'))
    theme = models.CharField(max_length=10, choices=THEMES, default='system')

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def sign_in_id(self):
        return self.email or self.phone or f'person-{self.pk}'

    @property
    def has_login_link(self):
        return bool(self.login_key)

    @property
    def login_path(self):
        """The magic link's path, or '' when there is none. The host is added by the view, which
        is the only place that knows what name the site is being reached by."""
        if not self.login_key:
            return ''
        return reverse('hibiscus_me_key', args=[self.login_key])


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


class NotifySlot(models.Model):
    """A time of day at which one message may go out.

    More than one message a day only works if each time of day has a different job and its own
    words — so a slot owns both. "Only if there is news" lets a slot stay silent rather than
    inventing filler, which is what stops three a day from becoming noise.
    """
    class Key(models.TextChoices):
        MORNING = 'morning', 'Morning'
        AFTERNOON = 'afternoon', 'Afternoon'
        EVENING = 'evening', 'Evening'
        NIGHT = 'night', 'Night'

    class Mode(models.TextChoices):
        ALWAYS = 'always', 'Always'
        NEWS = 'news', 'Only if there is news'

    key = models.CharField(max_length=10, choices=Key.choices, unique=True)
    at = models.TimeField(help_text='Local time. Matched to the 15-minute cron.')
    mode = models.CharField(max_length=6, choices=Mode.choices, default=Mode.NEWS)
    topic = models.CharField(max_length=120, blank=True)
    is_enabled = models.BooleanField(default=True)
    order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ['order', 'at']

    def __str__(self):
        return f"{self.get_key_display()} · {self.at:%H:%M}"


class NotifyLine(models.Model):
    """One wording, for one slot, for one situation. Several per pair, rotated by the day."""

    class Situation(models.TextChoices):
        RUNNING_LOGGED = 'running_logged', 'Period running, today logged'
        RUNNING_UNLOGGED = 'running_unlogged', 'Period running, today not logged'
        DUE_TODAY = 'due_today', 'Expected today'
        DUE_SOON = 'due_soon', 'Expected in 1–2 days'
        QUIET = 'quiet', 'Quiet stretch'
        LATE_EARLY = 'late_early', 'Late 1–3 days'
        LATE_LONG = 'late_long', 'Late 4+ days'
        OVULATION = 'ovulation', 'Around ovulation'
        LOW_STOCK = 'low_stock', 'Stock low'

    slot = models.ForeignKey(NotifySlot, on_delete=models.CASCADE, related_name='lines')
    situation = models.CharField(max_length=20, choices=Situation.choices)
    text = models.CharField(max_length=200, help_text='Body of the message, 200 characters max.')
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['situation', 'id']
        indexes = [models.Index(fields=['slot', 'situation'])]

    def __str__(self):
        return f"{self.slot.key}/{self.situation}: {self.text[:40]}"


class NotifyMute(models.Model):
    """A person switching one time of day off. The admin owns the times; they own the reach."""
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='mutes')
    slot = models.ForeignKey(NotifySlot, on_delete=models.CASCADE, related_name='mutes')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['person', 'slot'], name='one_mute_per_slot')]


class NotifySent(models.Model):
    """What went out, and the guarantee that it goes out once.

    The unique row on (person, slot, date) is what stops two cron ticks sending the same slot
    twice — there is no "have I already?" question to get wrong.
    """
    person = models.ForeignKey(Person, on_delete=models.CASCADE, related_name='sent')
    slot = models.ForeignKey(NotifySlot, on_delete=models.CASCADE, related_name='sent')
    date = models.DateField()
    line = models.ForeignKey(NotifyLine, null=True, blank=True, on_delete=models.SET_NULL,
                             related_name='+')
    situation = models.CharField(max_length=20, blank=True)
    title = models.CharField(max_length=100, blank=True)
    body = models.CharField(max_length=200, blank=True)
    delivered = models.PositiveSmallIntegerField(default=0, help_text='Devices SyncUp accepted.')
    error = models.CharField(max_length=200, blank=True)
    sent_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['person', 'slot', 'date'],
                                               name='one_send_per_slot_per_day')]
        indexes = [models.Index(fields=['person', '-sent_at'])]
        ordering = ['-sent_at']

    def __str__(self):
        return f"{self.person.name} · {self.slot.key} · {self.date}"


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
