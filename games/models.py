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


class Player(models.Model):
    """Somebody on the hub who plays. One row per member, made the first time they open it."""
    member = models.OneToOneField('core.Member', on_delete=models.CASCADE,
                                  related_name='games')
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
    def push_token(self):
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

    `player` being null is the whole of what the server keeps to itself: a seat with nobody
    behind it is one this server plays. It is a database column and nothing more — it is never
    put into a page, a payload or a log, and there is a test that says so.

    `name` is stored on the seat rather than read through the player, so that what the table
    calls somebody cannot change halfway through a round.
    """
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='seats')
    position = models.PositiveSmallIntegerField()
    player = models.ForeignKey(Player, null=True, blank=True, on_delete=models.SET_NULL,
                               related_name='seats')
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
