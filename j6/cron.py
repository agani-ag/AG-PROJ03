"""J6's share of SyncHub's two cron jobs.

No endpoint of its own — `apps.py` imports this at startup, which registers the hooks with the
hub. The 15-minute job pushes unread messages to people who are not looking; the nightly one
deletes what has aged out.
"""
import logging
from datetime import timedelta

from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core import cronhub
from core.models import HubSettings

from . import push as pusher
from . import service
from .models import Account, Chat, Member, Message, Settings

logger = logging.getLogger(__name__)

SEND_TIMEOUT = 5
TITLE_MAX = 100
BODY_MAX = 200
# Somebody actively polling does not need a buzz: the page is already showing it.
QUIET_AFTER_SECONDS = 120


@cronhub.tick('j6.push', budget=8)
def push(ctx):
    """The backstop, not the main path.

    A message is pushed the moment it is sent (see `push.notify_new_message`) — this hook exists
    for what that missed: a send that failed, a process that died, a server restarted mid-flight.
    `last_push_seq` on the member row is what keeps the two from buzzing twice for one message,
    and anyone whose page has polled in the last couple of minutes is skipped because they can
    already see it.
    """
    config = Settings.load()
    if not config.push_enabled:
        return {'items': 0, 'note': 'push is switched off in module settings'}
    if not HubSettings.load().syncup_base:
        return {'items': 0,
                'note': 'SyncUp address not set at System → Settings, so nothing can be sent'}

    url = pusher.chat_url()
    quiet_before = timezone.now() - timedelta(seconds=QUIET_AFTER_SECONDS)
    out = {'items': 0, 'sent': 0, 'failed': 0, 'skipped': 0}

    # A token on the member reaches every module, so an account linked to somebody who
    # registered their phone in the diary is pushable here without J6 ever seeing a token.
    # An unlinked account has no member, and keeps its own.
    with_token = (Account.objects.filter(is_active=True, notify=True)
                  .filter(Q(member__syncup_token__gt='') | Q(syncup_token__gt=''))
                  .select_related('member'))
    if not with_token.exists():
        return {'items': 0, 'note': 'no account has a device token yet — J6 has to be opened '
                                    'once inside the SyncUp app before anything can be pushed'}

    candidates = with_token.filter(last_seen_at__lt=quiet_before)
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

        delivered, error = pusher.send_via_syncup(account, title, body, url)
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
