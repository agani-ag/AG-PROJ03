"""J6 — chat with no sign-up.

An account is a **name** and two generated IDs: an Account ID you hand out, and a Self-ID you
come back with. There is no password, no email and no recovery: lose the Self-ID and you make a
new account. That one decision shapes everything else here.

A chat is a pair of accounts, and nothing else — no rooms, no groups. Messages carry a per-chat
`seq`, which is what the poll asks against, and read receipts are one integer per side rather
than a row per message.

Media is deliberately absent from this file: a picture in a chat is a URL inside a message body,
rendered by the browser. Nothing is uploaded and nothing is stored but text.
"""
import secrets

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

# No look-alike characters: an ID gets copied, pasted and occasionally read aloud.
ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'
ACCOUNT_GROUPS = 4                      # j6-xxxx-xxxx-xxxx-xxxx → 16 chars, ~79 bits
SELF_GROUPS = 6                         # j6s-xxxx-…-xxxx       → 24 chars, ~119 bits

NAME_MIN = 2
NAME_MAX = 24
ONLINE_SECONDS = 70                     # a poll is a sign of life; this is two cadences of it
TYPING_SECONDS = 6                      # the flag expires on its own, so it cannot stick on


def _grouped(groups):
    return '-'.join(''.join(secrets.choice(ALPHABET) for _ in range(4)) for _ in range(groups))


def new_account_id():
    """Short enough to paste about, long enough that nobody finds you by guessing."""
    return 'j6-' + _grouped(ACCOUNT_GROUPS)


def new_self_id():
    """The credential. Longer than the Account ID because it is the whole way in."""
    return 'j6s-' + _grouped(SELF_GROUPS)


def validate_name(value):
    name = (value or '').strip()
    if len(name) < NAME_MIN:
        raise ValidationError(f'A name needs at least {NAME_MIN} characters.')
    if len(name) > NAME_MAX:
        raise ValidationError(f'A name can be at most {NAME_MAX} characters.')
    return name


class Settings(models.Model):
    """One row, pk=1. The numbers that would otherwise be constants in the code.

    SyncUp's address is kept here rather than read from another module: modules in SyncHub do not
    import one another, so J6 carries its own copy of the two values even though Hibiscus has the
    same ones. A duplicated setting is cheaper than a dependency between modules.
    """
    signup_open = models.BooleanField(default=True)
    keep_days = models.PositiveSmallIntegerField(
        default=90, help_text='Messages older than this are deleted by the nightly cleanup.')
    idle_account_days = models.PositiveSmallIntegerField(
        default=180, help_text='An account untouched for this long goes, with its chats.')

    msgs_per_minute = models.PositiveSmallIntegerField(default=20)
    new_chats_per_hour = models.PositiveSmallIntegerField(default=10)
    max_chars = models.PositiveSmallIntegerField(default=2000)

    render_media = models.BooleanField(
        default=True, help_text='Off means every link stays a link.')
    autoplay_seconds = models.PositiveSmallIntegerField(
        default=15, help_text='Clips shorter than this play muted and looping, like a GIF.')
    max_media_px = models.PositiveSmallIntegerField(default=240)
    allow_svg = models.BooleanField(default=True)

    site_base = models.CharField(max_length=120, blank=True,
                                 help_text="This site's address, used as the link a pushed "
                                           'notification opens.')
    syncup_base = models.CharField(max_length=120, blank=True,
                                   help_text='e.g. https://syncup.example.com')
    syncup_notify_key = models.CharField(max_length=80, blank=True)
    push_enabled = models.BooleanField(default=True)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')

    class Meta:
        verbose_name = verbose_name_plural = 'settings'

    def __str__(self):
        return 'J6 settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


class Account(models.Model):
    """Somebody with a name and two IDs. Not a Django user, and never reaches the console."""
    account_id = models.CharField(max_length=32, unique=True, default=new_account_id)
    self_id = models.CharField(max_length=40, unique=True, default=new_self_id,
                               help_text='Stored readable on purpose: the owner can look it up '
                                         'any time, which is what makes it a way back in.')
    name = models.CharField(max_length=NAME_MAX)

    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)
    is_active = models.BooleanField(default=True)

    syncup_token = models.TextField(blank=True)
    load_media = models.BooleanField(
        default=True, help_text='Their own switch: off and every link stays a link.')
    notify = models.BooleanField(default=True)
    THEMES = (('system', 'Follow the device'), ('light', 'Light'), ('dark', 'Dark'))
    theme = models.CharField(max_length=10, choices=THEMES, default='system')

    class Meta:
        ordering = ['name']
        indexes = [models.Index(fields=['-last_seen_at'])]

    def __str__(self):
        return self.name

    @property
    def initials(self):
        parts = (self.name or '').split()
        if len(parts) > 1:
            return (parts[0][:1] + parts[1][:1]).upper()
        return (self.name or '?')[:2].title()

    @property
    def colour(self):
        """A stable colour per account, seeded from the ID so both sides see the same one."""
        palette = ['#7c3aed', '#0d9488', '#db2777', '#ea580c', '#2563eb', '#059669', '#c026d3',
                   '#4f46e5']
        return palette[sum(self.account_id.encode()) % len(palette)]

    @property
    def is_online(self):
        return (timezone.now() - self.last_seen_at).total_seconds() < ONLINE_SECONDS

    def touch(self):
        Account.objects.filter(pk=self.pk).update(last_seen_at=timezone.now())


class Chat(models.Model):
    """One row per pair. The two sides are stored in a fixed order so A→B and B→A are one chat."""
    low = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='chats_low')
    high = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='chats_high')
    created_at = models.DateTimeField(default=timezone.now)
    last_message_at = models.DateTimeField(default=timezone.now)
    next_seq = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ['-last_message_at']
        constraints = [models.UniqueConstraint(fields=['low', 'high'], name='one_chat_per_pair')]
        indexes = [models.Index(fields=['-last_message_at'])]

    def __str__(self):
        return f'{self.low.name} · {self.high.name}'

    @classmethod
    def between(cls, one, other):
        """The chat for these two, created if this is the first word between them."""
        if one.pk == other.pk:
            raise ValidationError('You cannot start a chat with yourself.')
        low, high = sorted([one, other], key=lambda a: a.pk)
        chat, created = cls.objects.get_or_create(low=low, high=high)
        if created:
            Member.objects.bulk_create([Member(chat=chat, account=low),
                                        Member(chat=chat, account=high)])
        return chat, created

    def other(self, account):
        return self.high if account.pk == self.low.pk else self.low

    def member_for(self, account):
        return self.members.filter(account=account).first()


class Member(models.Model):
    """One side of a chat: what they have read, whether they have blocked the other."""
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='members')
    account = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='memberships')
    last_read_seq = models.PositiveIntegerField(default=0)
    last_push_seq = models.PositiveIntegerField(
        default=0, help_text='Highest seq already pushed to their device, so the cron does not '
                             'buzz twice for the same message.')
    is_blocked = models.BooleanField(
        default=False, help_text='This side has blocked the other. Silent: the other is told '
                                 'nothing and their messages are simply never shown.')
    typing_until = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['chat', 'account'], name='one_side_once')]

    def __str__(self):
        return f'{self.account.name} in {self.chat_id}'

    @property
    def is_typing(self):
        return bool(self.typing_until and self.typing_until > timezone.now())


class Message(models.Model):
    """One line in one chat. `seq` is per chat, which is what the poll asks against."""
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name='messages')
    seq = models.PositiveIntegerField()
    sender = models.ForeignKey(Account, on_delete=models.CASCADE, related_name='sent')
    body = models.TextField()
    created_at = models.DateTimeField(default=timezone.now)
    delivered_at = models.DateTimeField(
        null=True, blank=True, help_text='Set the first time the other side carries it away — '
                                         'the single tick becoming a double.')

    class Meta:
        ordering = ['seq']
        constraints = [models.UniqueConstraint(fields=['chat', 'seq'], name='one_seq_per_chat')]
        indexes = [models.Index(fields=['chat', 'seq'])]

    def __str__(self):
        return f'{self.chat_id}#{self.seq}'
