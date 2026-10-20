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


class HubSettings(models.Model):
    """What every module needs and none of them should own: where this site lives, and how to
    reach SyncUp.

    These three were duplicated in Hibiscus and in J6, which meant two places to set the same
    value and two chances to set it differently. They belong to the hub: a module decides *what*
    to say and *when*, the hub knows *where to post it*.

    One row, pk=1, like a module's own settings.
    """
    site_base = models.CharField(
        max_length=120, blank=True,
        help_text="This site's address, e.g. https://synchub.example.com — used as the link a "
                  'pushed notification opens. The cron has no request to work it out from.')
    syncup_base = models.CharField(
        max_length=120, blank=True,
        help_text='Where SyncUp lives, e.g. https://syncup.example.com — a different machine '
                  'from this one.')
    syncup_notify_key = models.CharField(max_length=80, blank=True)

    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')

    class Meta:
        verbose_name = verbose_name_plural = 'hub settings'

    def __str__(self):
        return 'Hub settings'

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    @property
    def can_push(self):
        return bool(self.syncup_base)

    def points_at(self, host):
        """True when the SyncUp address somebody typed is this very site.

        Worth its own method because the mistake is silent: a server asked to call itself while
        it is busy answering that request has nothing left to answer with, so it waits out the
        timeout and the error reads like "SyncUp is down".
        """
        from urllib.parse import urlparse
        if not self.syncup_base:
            return False
        address = self.syncup_base
        netloc = urlparse(address if '//' in address else '//' + address).netloc.lower()
        return netloc.split(':')[0] == (host or '').split(':')[0].lower()

    def url_for(self, path='/'):
        """An absolute link to somewhere on this site, or '' when nobody has said where it is."""
        if not self.site_base:
            return ''
        return self.site_base.rstrip('/') + path


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
