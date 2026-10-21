"""Telling somebody their money moved.

Four things are worth a notification, and one of them is dangerous:

* **An entry landed on something you share** — the other side, never the author. This is the one
  with teeth: "push on every entry" in a busy shop book is a push every ninety seconds, so it is
  coalesced to one per recipient per folder per `coalesce_minutes`, counted off `last_push_at`
  on the share or the person row — the same shape as J6's `last_push_seq`.
* **An invitation was accepted or declined** — the person who sent it.
* **Money is due, or late** — whoever is owed, once a day, at the reminder hour.
* **The month turned** — each account, with both halves in one line because they are two
  separate figures and always will be.

Sent at write time, the way J6 learned to after a fifteen-minute tick plus a quiet window made a
chat push land seventeen minutes late. `cron.tick` is the backstop, not the path.

Delivery is `core.push.send`, which is SyncUp's partner endpoint and therefore FCM on a phone.
No token, no push: a page can only raise a notification while a tab is open.
"""
import logging
import threading
from datetime import timedelta

from django.db import connection
from django.urls import reverse
from django.utils import timezone

from core import push as core_push
from core.models import HubSettings

from .models import Kind, LedgerEntry, Settings

logger = logging.getLogger(__name__)

SEND_TIMEOUT = core_push.SEND_TIMEOUT
TEST_TIMEOUT = 12                 # somebody is watching the page for this one


def home_url():
    """Where a tapped notification lands. The address belongs to the hub, not to the module."""
    return HubSettings.load().url_for(reverse('pocket_ledger'))


def money(amount, config=None):
    config = config or Settings.load()
    return f'{config.currency_symbol}{amount:,.0f}'


def token_for(account):
    """One device token per person, held on the member — not one per module."""
    member = getattr(account, 'member', None)
    return (member.syncup_token if member else '') or account.syncup_token


def can_push(account, config=None):
    config = config or Settings.load()
    return bool(config.push_enabled and account and account.notify and account.is_active
                and token_for(account) and HubSettings.load().syncup_base)


def send_now(account, title, body, url=None, timeout=SEND_TIMEOUT):
    """One notification, synchronously. Returns (delivered, error)."""
    return core_push.send(token_for(account), title, body, url or home_url(), timeout)


def _deliver(account_pk, title, body, url):
    """In a thread, so writing an entry never waits on SyncUp being slow."""
    from .models import Account
    try:
        account = Account.objects.filter(pk=account_pk).select_related('member').first()
        if account:
            core_push.send(token_for(account), title, body, url)
    except Exception:                                    # pragma: no cover - belt and braces
        logger.exception('pocket: push failed')
    finally:
        connection.close()


def _spawn(account, title, body, url=None):
    threading.Thread(target=_deliver, args=(account.pk, title, body, url or home_url()),
                     daemon=True).start()


def _quiet_or_off(config):
    return not config.push_enabled or config.is_quiet()


# --------------------------------------------------------------------------- #
# Something changed in a book somebody else can see
# --------------------------------------------------------------------------- #
def notify_entry(entry, actor, config=None):
    """Tell whoever shares this book — or this one line — that a figure arrived.

    Returns a list of short reasons, which is what the tests read and what the cron logs.
    """
    config = config or Settings.load()
    folder = entry.folder
    results = []
    if _quiet_or_off(config):
        return ['quiet']

    now = timezone.now()
    gap = timedelta(minutes=config.coalesce_minutes)

    # ---- the folder's owner, when somebody else wrote in their book
    owner = folder.owner
    if actor and owner.pk != actor.pk and can_push(owner, config):
        results.append(_maybe(owner, folder, folder, entry, actor, now, gap, config))

    # ---- everybody the whole folder is shared with
    for share in folder.shares.select_related('account').filter(accepted_at__isnull=False,
                                                                declined_at__isnull=True,
                                                                notify=True):
        if not share.account or (actor and share.account.pk == actor.pk):
            continue
        if can_push(share.account, config):
            results.append(_maybe(share.account, share, folder, entry, actor, now, gap, config))

    # ---- the one person whose line this is, if they are a real user
    person = entry.person
    if (person.linked_account_id and person.notify and person.linked_at
            and (not actor or person.linked_account_id != actor.pk)
            and can_push(person.linked_account, config)):
        results.append(_maybe(person.linked_account, person, folder, entry, actor, now, gap,
                              config, line=True))
    return [r for r in results if r]


def _maybe(account, row, folder, entry, actor, now, gap, config, line=False):
    """Push unless this recipient heard about this folder a moment ago.

    The clock is read back out of the database rather than off `row`: the object in hand came
    from the entry that was just written and can easily be older than the last notification.
    """
    last = type(row).objects.filter(pk=row.pk).values_list('last_push_at', flat=True).first()
    if last and now - last < gap:
        return 'coalesced'
    type(row).objects.filter(pk=row.pk).update(last_push_at=now)

    who = actor.name if actor else 'Somebody'
    if line:
        title = f'{who} added an entry'
        body = f'{money(entry.amount, config)} · {folder.name}'
        url = HubSettings.load().url_for(reverse('pocket_line', args=[entry.person_id]))
    else:
        title = f'{folder.name} · {who} added an entry'
        body = f'{money(entry.amount, config)} · {entry.person.name}'
        url = HubSettings.load().url_for(reverse('pocket_person',
                                                 args=[folder.pk, entry.person_id]))
    _spawn(account, title, body, url)
    return 'pushed'


def notify_invite_answer(person, accepted, config=None):
    """The owner asked; somebody answered. Worth one line either way."""
    config = config or Settings.load()
    owner = person.folder.owner
    if _quiet_or_off(config) or not can_push(owner, config):
        return 'no push'
    if accepted:
        title = f'{person.name} accepted your invitation'
        body = f'Your line in {person.folder.name} is now two-way'
    else:
        title = f'{person.name} is not that account'
        body = f'Nothing was shared from {person.folder.name}'
    _spawn(owner, title, body,
           HubSettings.load().url_for(reverse('pocket_person',
                                              args=[person.folder_id, person.pk])))
    return 'pushed'


def notify_suggestion(person, config=None):
    """Somebody signed up with a number in this folder. One mention, then never again."""
    config = config or Settings.load()
    owner = person.folder.owner
    if _quiet_or_off(config) or not can_push(owner, config):
        return 'no push'
    _spawn(owner, f'Is this your {person.name}?',
           f'Somebody signed up with the number on {person.name} in {person.folder.name}',
           HubSettings.load().url_for(reverse('pocket_ledger')))
    return 'pushed'


# --------------------------------------------------------------------------- #
# The cron's two jobs
# --------------------------------------------------------------------------- #
def run_due_reminders(config=None, now=None, limit=200):
    """Money that is due today, or was due and is still outstanding. Once a day each."""
    from . import service
    config = config or Settings.load()
    now = now or config.now_local()
    if not config.push_enabled or config.is_quiet(now) or now.hour < config.reminder_hour:
        return {'sent': 0, 'skipped': 'not yet'}

    today = config.today()
    sent = 0
    rows = (LedgerEntry.objects.filter(voided_at__isnull=True, kind=Kind.LENT,
                                       due_on__isnull=False, due_on__lte=today)
            .exclude(due_pushed_on=today)
            .select_related('person', 'folder', 'folder__owner')[:limit])
    for entry in rows:
        owner = entry.folder.owner
        balance = service.person_balance(entry.person)
        LedgerEntry.objects.filter(pk=entry.pk).update(due_pushed_on=today)
        if balance <= 0 or not can_push(owner, config):
            continue
        late = (today - entry.due_on).days
        when = 'due today' if late == 0 else f'{late} day{"s" if late != 1 else ""} late'
        _spawn(owner, f'{entry.person.name} owes you {money(balance, config)}',
               f'{entry.folder.name} · {when}',
               HubSettings.load().url_for(reverse('pocket_person',
                                                  args=[entry.folder_id, entry.person_id])))
        sent += 1
    return {'sent': sent}


def run_month_summary(config=None, now=None):
    """On the first of the month: what the Tracker saw, and where the Ledger stands."""
    from . import service
    from .models import Account
    config = config or Settings.load()
    now = now or config.now_local()
    if not config.push_enabled or now.day != 1 or now.hour < config.reminder_hour:
        return {'sent': 0, 'skipped': 'not the 1st'}

    last = (now.date().replace(day=1) - timedelta(days=1))
    sent = 0
    for account in Account.objects.filter(is_active=True, is_archived=False):
        if not can_push(account, config):
            continue
        month = service.tracker_month(account, last.year, last.month, config)
        position = service.account_position(account)
        if not month['count'] and not position['owed_to_you'] and not position['you_owe']:
            continue
        body = (f"Tracker: {money(month['in'], config)} in, {money(month['out'], config)} out. "
                f"Ledger: {money(position['owed_to_you'], config)} owed to you.")
        _spawn(account, last.strftime('%B'), body)
        sent += 1
    return {'sent': sent}
