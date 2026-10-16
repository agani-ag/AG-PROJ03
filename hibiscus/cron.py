"""Hibiscus's share of SyncHub's two cron jobs.

No endpoint of its own: `core/apps.py` imports this file at startup, which registers the hooks
with the hub. The 15-minute job sends whichever notification slots are due; the nightly one
trims what has aged out.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core import cronhub
from core.models import HubSettings

from . import defaults, push
from .models import Audit, NotifySent, NotifySlot, Settings

logger = logging.getLogger(__name__)

# How long the record of sent messages is kept. Long enough to answer "did it go out?", not a
# second copy of the diary.
KEEP_SENT_DAYS = 90


@cronhub.tick('hibiscus.notify', budget=8)
def notify(ctx):
    """Send every slot whose time has passed today, to everyone who has not had it yet.

    Safe at any interval: a slot's due-ness is "its time has passed and it is not stale", and the
    unique row on (person, slot, date) means a second tick in the same minute sends nothing.
    """
    config = Settings.load()
    # Seed the slots if nobody has opened the notifications page yet — otherwise this hook would
    # find no slots and quietly send nothing for ever.
    defaults.ensure_slots()
    now_local = timezone.localtime(timezone.now(), config.tzinfo)
    result = {'items': 0, 'slots': {}}

    for slot in NotifySlot.objects.filter(is_enabled=True):
        if not push.slot_is_due(slot, config, now_local):
            continue
        if ctx.out_of_time():
            result['remaining'] = True
            break
        summary = push.run_slot(slot, config, now_local=now_local,
                                base_url=push.diary_url(config))
        result['slots'][slot.key] = summary
        result['items'] += summary['sent']

    if not HubSettings.load().syncup_base:
        result['note'] = ('SyncUp address not set at System → Settings, so nothing can be '
                          'delivered yet')
    return result


@cronhub.cleanup('hibiscus.retention')
def retention(ctx):
    """Trim the audit trail and the record of sent messages."""
    config = Settings.load()
    audit_cutoff = timezone.now() - timedelta(days=config.keep_audit_days)
    sent_cutoff = (timezone.localtime(timezone.now(), config.tzinfo).date()
                   - timedelta(days=KEEP_SENT_DAYS))

    audit_deleted, _ = Audit.objects.filter(at__lt=audit_cutoff).delete()
    sent_deleted, _ = NotifySent.objects.filter(date__lt=sent_cutoff).delete()
    return {'items': audit_deleted + sent_deleted,
            'audit_deleted': audit_deleted, 'sent_deleted': sent_deleted}
