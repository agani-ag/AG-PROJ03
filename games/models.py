"""Games — the shared furniture, and one row per game in progress.

Nothing here knows the rules of UNO. `Room`, `Seat` and `Player` are what any game at this
table needs: who is sitting down, under what house rules, and what the score is. The rules
live in `uno/`, and the second game adds a folder beside it rather than a module.

`Game.state` is the whole of a game in progress, as JSON. One row to write and one `seq` to
compare and swap — which is the only concurrency control available here, since SQLite has no
row locks (`connection.features.has_select_for_update` is False). Two people tapping at once
is therefore the same case as one of them tapping twice, and both are already handled.
"""
import secrets

from django.db import models
from django.utils import timezone

#: No vowels, no 0/O, no 1/I/L: a code is read aloud and typed in by somebody else.
CODE_LETTERS = 'BCDFGHJKMNPQRSTVWXYZ23456789'
CODE_LENGTH = 5


def new_code():
    return ''.join(secrets.choice(CODE_LETTERS) for _ in range(CODE_LENGTH))


class Settings(models.Model):
    """One row, pk=1 — the numbers that would otherwise be constants.

    What is not here: where SyncUp lives and what this site is called. Those are the hub's
    (`core.models.HubSettings`), because every module sends to the same SyncUp.
    """
    keep_guest_days = models.PositiveSmallIntegerField(
        default=30,
        help_text='A guest untouched for this long is deleted outright by the nightly '
                  'cleanup, with every table, seat, round and move that is theirs. '
                  'Members are never touched by it.')
    keep_finished_days = models.PositiveSmallIntegerField(
        default=1,
        help_text='A finished table is kept this long and then deleted, with its rounds and '
                  'moves. Nothing reads a finished table — the score lives on the table and '
                  'the table is over. 0 removes them on the next nightly run.')
    keep_lobby_days = models.PositiveSmallIntegerField(
        default=7, help_text='A table nobody ever started, abandoned this long ago.')

    class Meta:
        verbose_name = verbose_name_plural = 'settings'

    def __str__(self):
        return f'guests kept {self.keep_guest_days} days'

    def save(self, *args, **kwargs):
        self.pk = 1                       # one row, always
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]


def new_guest_key():
    """What brings a guest back to their own name and score on the same phone.

    Long, because it is the whole of their identity here: there is no password to fall back
    on and nothing to recover with. Lose it and you are a new person, which is the trade J6
    makes too.
    """
    return secrets.token_urlsafe(24)


class Player(models.Model):
    """Somebody who plays. A hub member, or a guest who gave a name and nothing else.

    A guest is a Player with no member. Everything else about them is the same — they make
    tables, take chairs, keep a score — because a game is a poor place to meet a sign-up form.
    What they do not get is a push when it is their turn: that needs a device token, and a
    token lives on a member.
    """
    member = models.OneToOneField('core.Member', null=True, blank=True,
                                  on_delete=models.CASCADE, related_name='games')
    guest_key = models.CharField(
        max_length=64, unique=True, null=True, blank=True, default=None,
        help_text='Null for a member. For a guest it is in their session and nowhere else.')
    name = models.CharField(max_length=40, help_text='What the table shows. Theirs to change.')
    played = models.PositiveIntegerField(default=0)
    won = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    #: Not a column. `core.push.token_of` falls back to a module's own copy, and Games has
    #: none to fall back to: everybody here is a hub member, so the token is on the member.
    syncup_token = ''

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name

    @property
    def is_guest(self):
        """No member behind them. The console says so; the table does not — same rule as a
        chair this server plays."""
        return self.member_id is None

    @property
    def push_token(self):
        if self.is_guest:
            return ''              # no member, no device token, nothing to push to
        from core.push import token_of
        return token_of(self)


class Room(models.Model):
    """A table. It outlives a round: the score carries, and "play again" is a button."""

    class Phase(models.TextChoices):
        LOBBY = 'lobby', 'Waiting to start'
        PLAYING = 'playing', 'In play'
        CLOSED = 'closed', 'Finished'

    code = models.CharField(max_length=8, unique=True, default=new_code)
    game_key = models.CharField(max_length=16, default='uno')   # room for the next one
    host = models.ForeignKey(Player, on_delete=models.CASCADE, related_name='rooms_hosted')
    options = models.JSONField(default=dict, blank=True)
    phase = models.CharField(max_length=10, choices=Phase.choices, default=Phase.LOBBY)
    target = models.PositiveSmallIntegerField(
        default=0, help_text='Points to play to. 0 means a single round.')
    created_at = models.DateTimeField(default=timezone.now)
    last_seen_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['-last_seen_at']
        indexes = [models.Index(fields=['-last_seen_at'])]

    def __str__(self):
        return f'{self.code} · {self.get_phase_display()}'

    @property
    def is_open(self):
        return self.phase == self.Phase.LOBBY

    @property
    def leader(self):
        return max(self.seats.all(), key=lambda s: s.score, default=None)


class Seat(models.Model):
    """A chair at the table.

    `player` being null, or `away` being set, is the whole of what the server keeps to
    itself: either way it is a chair this server plays. They are database columns and
    nothing more — never put into a page, a payload or a log, and there is a test saying so.

    `name` is stored on the seat rather than read through the player, so that what the table
    calls somebody cannot change halfway through a round.
    """
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='seats')
    position = models.PositiveSmallIntegerField()
    player = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='seats')
    #: Set when somebody stopped answering and the chair is being played for them. The
    #: player stays on it, because it is still their chair and their score — this is the
    #: only reason they can walk back into the round they left.
    away = models.BooleanField(default=False)
    name = models.CharField(max_length=40)
    score = models.PositiveIntegerField(default=0)
    joined_at = models.DateTimeField(default=timezone.now)
    #: The seq this seat was last buzzed about, so a retry and the fifteen-minute backstop
    #: cannot both tell somebody it is their turn.
    #: -1 rather than 0, because seq 0 is a real turn: a table where nobody has moved yet
    #: is exactly when the first person needs telling.
    pushed_seq = models.IntegerField(default=-1)
    pushed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['position']
        constraints = [models.UniqueConstraint(fields=['room', 'position'],
                                               name='one_seat_per_position')]

    def __str__(self):
        return f'{self.room.code} · {self.position} · {self.name}'

    @property
    def is_held(self):
        """Whether this server is playing it — an empty chair or one somebody left."""
        return self.player_id is None or self.away


class Game(models.Model):
    """One round. The whole of it is `state`."""
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='games')
    number = models.PositiveSmallIntegerField(default=1)
    state = models.JSONField(default=dict)
    seq = models.PositiveIntegerField(default=0)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-number']

    def __str__(self):
        return f'{self.room.code} · round {self.number}'

    @property
    def is_live(self):
        return self.finished_at is None


class Move(models.Model):
    """Append-only, for replaying a round in the console and for working out what went wrong."""
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='moves')
    seq = models.PositiveIntegerField()
    position = models.PositiveSmallIntegerField()
    kind = models.CharField(max_length=16)
    detail = models.CharField(max_length=120, blank=True)
    at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ['seq']
        indexes = [models.Index(fields=['game', 'seq'])]

    def __str__(self):
        return f'{self.seq} · {self.kind}'
