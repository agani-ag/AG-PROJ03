"""Hibiscus's share of SyncHub's two cron jobs.

No endpoint of its own: `core/apps.py` imports this file at startup, which registers the hooks
with the hub. The daily-notification hook is added here when the push side is built — it will
send each enabled slot once a day, to anyone whose local time has passed that slot's time.
"""
import logging
from datetime import timedelta

from django.utils import timezone

from core import cronhub

from .models import Audit

logger = logging.getLogger(__name__)

# How long the "who looked at whose diary" trail is kept. Long enough to answer a question about
# last month, short enough that it is not a second copy of the diary for ever.
KEEP_AUDIT_DAYS = 180


@cronhub.cleanup('hibiscus.audit')
def trim_audit(ctx):
    """Drop audit rows past their retention."""
    cutoff = timezone.now() - timedelta(days=KEEP_AUDIT_DAYS)
    deleted, _ = Audit.objects.filter(at__lt=cutoff).delete()
    return {'items': deleted, 'audit_deleted': deleted}
