"""The shell's own models: the admin's sign-in PIN and the two bits of cron bookkeeping.

The admin itself is a plain Django superuser (username, password, name on `first_name`).
Feature modules keep their models in their own app (hibiscus/models.py, chat/models.py, ...).
Same approach as AG-PROJ01's `AdminPin` and AG-PROJ02's passkey.
"""
import re
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import IntegrityError, models
from django.utils import timezone


class AdminPin(models.Model):
    """The admin's 5-character PIN, used by the PIN tab on the sign-in page.

    Kept as a salted hash, like a password, so a PIN sign-in checks each admin's hash rather
    than looking the PIN up directly.
    """
    PATTERN = re.compile(r'[A-Z0-9]{5}')

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='admin_pin')
    pin_hash = models.CharField(max_length=128)
    updated_at = models.DateTimeField(auto_now=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    def set_pin(self, raw_pin):
        self.pin_hash = make_password((raw_pin or '').upper())

    def check_pin(self, raw_pin):
        return check_password((raw_pin or '').upper(), self.pin_hash)

    def __str__(self):
        return f"PIN for {self.user.username}"


class CronLock(models.Model):
    """A named, self-expiring lock so two overlapping cron calls can't run the same job.

    Deliberately database-backed rather than cache-backed: there is no shared CACHES backend, so
    Django falls back to per-process memory and a second worker would see an empty cache and take
    the lock anyway. `select_for_update()` is no help either (a no-op on SQLite), so the lock is
    taken with a conditional UPDATE, which *is* atomic. Copied from AG-PROJ01.
    """
    name = models.CharField(max_length=64, unique=True)
    locked_until = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    @classmethod
    def acquire(cls, name, ttl_seconds):
        """Take the lock, stealing it if the previous holder's TTL has lapsed. True if we got it."""
        now = timezone.now()
        until = now + timedelta(seconds=ttl_seconds)
        if cls.objects.filter(name=name, locked_until__lt=now).update(locked_until=until):
            return True
        if cls.objects.filter(name=name).exists():
            return False                      # held by a run still inside its TTL
        try:
            cls.objects.create(name=name, locked_until=until)
            return True
        except IntegrityError:
            return False                      # another worker created it a moment ago

    @classmethod
    def release(cls, name):
        cls.objects.filter(name=name).update(locked_until=timezone.now())

    def __str__(self):
        return f"{self.name} → {self.locked_until:%Y-%m-%d %H:%M:%S}"


class CronRun(models.Model):
    """One row per cron run, so a job that quietly stopped working is visible on a page.

    Neither AG-PROJ01 nor AG-PROJ02 keeps this, and in both you have to read logs to find out
    whether last night's run happened. `detail` holds one entry per module hook.
    """
    JOBS = (('tick', 'Tick · every 15 minutes'), ('cleanup', 'Cleanup · nightly'))

    job = models.CharField(max_length=32, choices=JOBS)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    duration_ms = models.PositiveIntegerField(default=0)
    ok = models.BooleanField(default=True)
    items = models.PositiveIntegerField(default=0)
    detail = models.JSONField(default=dict, blank=True)
    error = models.CharField(max_length=500, blank=True)
    triggered_by = models.CharField(max_length=20, default='cron')   # cron | admin

    class Meta:
        indexes = [models.Index(fields=['job', '-started_at'])]
        ordering = ['-started_at']

    def __str__(self):
        return f"{self.job} at {self.started_at:%Y-%m-%d %H:%M} ({'ok' if self.ok else 'failed'})"
