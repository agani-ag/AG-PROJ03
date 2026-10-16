"""J6's share of SyncHub's two cron jobs.

No endpoint of its own — `apps.py` imports this at startup, which registers the hooks with the
hub. The 15-minute job pushes unread messages to people who are not looking; the nightly one
deletes what has aged out.
"""
import json
import logging
import urllib.error
import urllib.request
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core import cronhub
from core.models import HubSettings

from . import service
from .models import Account, Chat, Member, Message, Settings

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 5
TITLE_MAX = 100
BODY_MAX = 200
# Somebody actively polling does not need a buzz: the page is already showing it.
QUIET_AFTER_SECONDS = 120


def _chat_url():
    """Where a tapped notification lands. The address is the hub's — every module opens on it."""
    return HubSettings.load().url_for(reverse('j6_inbox'))


def _send_via_syncup(account, title, body, url):
    """Post to SyncUp's partner endpoint. Returns (delivered, error)."""
    hub = HubSettings.load()
    if not hub.syncup_base or not account.syncup_token:
        return 0, 'no syncup token' if hub.syncup_base else 'syncup not configured'
    payload = json.dumps({'token': account.syncup_token, 'title': title, 'body': body,
                          'url': url, 'key': hub.syncup_notify_key}).encode()
    endpoint = hub.syncup_base.rstrip('/') + '/app/v1/partner/notify'
    request = urllib.request.Request(endpoint, data=payload,
                                     headers={'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT) as response:
            data = json.loads(response.read() or b'{}')
        return int(data.get('delivered') or 0), ''
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        return 0, f'{type(exc).__name__}: {exc}'[:200]


@cronhub.tick('j6.push', budget=8)
def push(ctx):
    """One summary per account per run — never one buzz per message.

    `last_push_seq` on the member row is what stops a second tick repeating itself, and anyone
    whose page has polled in the last couple of minutes is skipped: they can already see it.
    """
    config = Settings.load()
    if not config.push_enabled:
        return {'items': 0, 'note': 'push is switched off in module settings'}
    if not HubSettings.load().syncup_base:
        return {'items': 0,
                'note': 'SyncUp address not set at System → Settings, so nothing can be sent'}

    url = _chat_url()
    quiet_before = timezone.now() - timedelta(seconds=QUIET_AFTER_SECONDS)
    out = {'items': 0, 'sent': 0, 'failed': 0, 'skipped': 0}

    candidates = (Account.objects.filter(is_active=True, notify=True)
                  .exclude(syncup_token='').filter(last_seen_at__lt=quiet_before))
    for account in candidates:
        if ctx.out_of_time():
            out['remaining'] = True
            break
        pending = []                      # (other name, how many, newest body, member)
        for chat in service.chats_for(account):
            mine = chat.member_for(account)
            if not mine or mine.is_blocked:
                continue
            fresh = chat.messages.filter(seq__gt=max(mine.last_read_seq, mine.last_push_seq)) \
                                 .exclude(sender=account).order_by('seq')
            count = fresh.count()
            if count:
                pending.append((chat.other(account).name, count, fresh.last(), mine))
        if not pending:
            out['skipped'] += 1
            continue

        total = sum(p[1] for p in pending)
        if len(pending) == 1:
            name, count, newest, _ = pending[0]
            title = name[:TITLE_MAX]
            body = newest.body if count == 1 else f'{count} new messages'
        else:
            title = 'J6'
            body = f'{total} new messages from ' + ', '.join(p[0] for p in pending[:3])

        delivered, error = _send_via_syncup(account, title, body[:BODY_MAX], url)
        for _, _, newest, member in pending:
            Member.objects.filter(pk=member.pk).update(last_push_seq=newest.seq)
        out['items'] += total
        out['sent' if not error else 'failed'] += 1
        if error:
            out.setdefault('errors', []).append(error)
    return out


@cronhub.cleanup('j6.retention')
def retention(ctx):
    """Messages past their keep-days, then accounts nobody has touched for months."""
    config = Settings.load()
    now = timezone.now()

    old = now - timedelta(days=config.keep_days)
    messages_deleted, _ = Message.objects.filter(created_at__lt=old).delete()

    idle = now - timedelta(days=config.idle_account_days)
    stale = Account.objects.filter(last_seen_at__lt=idle, created_at__lt=idle)
    accounts_deleted = stale.count()
    stale.delete()                        # cascades to their chats and messages

    # A chat whose every message has aged out is an empty shell; it goes with them.
    empty = Chat.objects.filter(~Q(messages__isnull=False), last_message_at__lt=old)
    chats_deleted = empty.count()
    empty.delete()

    return {'items': messages_deleted + accounts_deleted + chats_deleted,
            'messages_deleted': messages_deleted,
            'accounts_deleted': accounts_deleted,
            'chats_deleted': chats_deleted}
