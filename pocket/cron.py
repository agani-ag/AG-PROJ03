"""Pocket Money's two hooks on the hub's cron.

No endpoint of its own — `apps.py` imports this at startup, which registers the hooks with
`core.cronhub`. SyncHub's quarter-hour job calls `tick`, and its nightly job calls `cleanup`.

The tick is a **backstop**, not the delivery path: an entry pushes the moment it is written.
What is genuinely the tick's own work is the two things that are about a date rather than an
action — money that has come due, and the turn of the month.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core import cronhub

from . import push
from .models import EntryLog, FolderShare, Person, Settings

logger = logging.getLogger(__name__)


@cronhub.tick('pocket.notify', budget=8)
def notify(ctx):
    """Due reminders and, on the first of the month, the summary."""
    config = Settings.load()
    if not config.push_enabled:
        return {'skipped': 'push off'}

    due = push.run_due_reminders(config)
    if ctx.out_of_time():
        return {'due': due, 'month': {'skipped': 'out of time'}}
    month = push.run_month_summary(config)
    return {'due': due, 'month': month}


@cronhub.cleanup('pocket.retention')
def retention(ctx):
    """Trim the audit log and drop links nobody used.

    Entries themselves are never pruned, in either half — the history is the point of the
    module, and a row of money is a few dozen bytes. That is also why no photographs are
    allowed anywhere near it.
    """
    config = Settings.load()
    now = timezone.now()

    cutoff = now - timedelta(days=config.keep_audit_days)
    logs = EntryLog.objects.filter(at__lt=cutoff).delete()[0]

    stale = now - timedelta(days=config.link_days)
    shares = FolderShare.objects.filter(account__isnull=True, created_at__lt=stale).delete()[0]
    invites = Person.objects.filter(link_code__isnull=False, linked_account__isnull=True,
                                    link_made_at__lt=stale).update(link_code=None)
    return {'audit_rows': logs, 'dead_share_links': shares, 'dead_invites': invites}
