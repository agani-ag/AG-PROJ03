"""Getting a message to somebody who is not looking at the page.

Two paths, and a chat needs both:

* **At send time.** If the other side has been away for a minute and has a device token, the
  message is pushed the moment it is written. This is the one that matters — a chat where the
  notification arrives a quarter of an hour later is not a chat, and waiting for the hub's tick
  meant up to seventeen minutes (fifteen for the tick, two for the quiet window).
* **On the tick, as a backstop.** Anything the first path missed — the process died, SyncUp was
  down, the server restarted mid-send — is caught by `cron.push` on the next quarter hour, which
  is also what stops a failed send being lost silently.

`last_push_seq` on the member row is what keeps the two from buzzing twice for one message.

Delivery itself is SyncUp's partner endpoint, which is what turns this into an FCM notification
on the person's phone. No token means no push: a browser on its own can only be told while a tab
is open, which is what the Notification API in the page is for.
"""
import logging
import threading

from django.db import connection
from django.urls import reverse
from django.utils import timezone

from core import push as core_push
from core.models import HubSettings

from .models import Member, Settings

logger = logging.getLogger(__name__)

SEND_TIMEOUT = core_push.SEND_TIMEOUT
TITLE_MAX = core_push.TITLE_MAX
BODY_MAX = core_push.BODY_MAX
# Somebody whose page polled this recently is reading it; a buzz would be noise.
AWAY_SECONDS = 60


def chat_url():
    """Where a tapped notification lands. The address belongs to the hub, not to J6."""
    return HubSettings.load().url_for(reverse('j6_inbox'))


def send_via_syncup(account, title, body, url, timeout=SEND_TIMEOUT):
    """Post to SyncUp's partner endpoint. Returns (delivered, error).

    The transport itself lives in `core.push` — three modules were about to own a copy of it.
    """
    return core_push.send(account.syncup_token, title, body, url, timeout)


def can_push_to(account, config=None):
    """Whether there is any point trying: switched on, a token, and an account in good standing."""
    config = config or Settings.load()
    return bool(config.push_enabled and account.is_active and account.notify
                and account.syncup_token and HubSettings.load().syncup_base)


def is_away(account, seconds=AWAY_SECONDS):
    return (timezone.now() - account.last_seen_at).total_seconds() >= seconds


def notify_new_message(chat, message):
    """Push this message to the other side, now, if they are not looking at it.

    Everything that can be decided without a network call is decided here, on the request's own
    thread: no token, no address, they are reading, they blocked the sender — all of those end
    the matter with nothing spawned. Only a real delivery gets a thread, so a send never waits on
    SyncUp and a test never quietly starts one.
    """
    # Read the other side fresh rather than through the chat's cached relation: this decision
    # turns on `last_seen_at` and `syncup_token`, both of which move while a chat object is held.
    from .models import Account
    other_pk = chat.high_id if message.sender_id == chat.low_id else chat.low_id
    other = Account.objects.filter(pk=other_pk).first()
    if not other:
        return 'gone'
    theirs = chat.members.filter(account=other).first()
    config = Settings.load()

    if not theirs or theirs.is_blocked:
        return 'blocked'
    if not can_push_to(other, config):
        return 'no token' if not other.syncup_token else 'off'
    if not is_away(other):
        return 'reading'
    if theirs.last_push_seq >= message.seq:
        return 'already pushed'

    # Claim it before the thread starts: two quick messages must not race into two buzzes, and
    # the tick must not repeat what is about to go.
    Member.objects.filter(pk=theirs.pk).update(last_push_seq=message.seq)

    threading.Thread(target=_deliver, args=(other.pk, message.sender.name, message.body),
                     daemon=True).start()
    return 'pushing'


def _deliver(account_pk, sender_name, body):
    """The part that talks to SyncUp, off the request's thread."""
    from .models import Account
    try:
        account = Account.objects.filter(pk=account_pk).first()
        if not account:
            return
        delivered, error = send_via_syncup(account, sender_name, body, chat_url())
        if error:
            # Not fatal: the tick will try again, because a failure leaves the message unread.
            logger.info('j6 push to %s failed: %s', account.account_id, error)
    except Exception:                                  # noqa: BLE001 - a thread must not crash
        logger.exception('j6 push thread failed')
    finally:
        # A thread gets its own connection; leaving it open leaks one per message.
        connection.close()
