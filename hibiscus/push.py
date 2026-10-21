"""Working out what to say to whom, and saying it through SyncUp.

Hibiscus owns no cron URL. SyncHub's 15-minute job calls `cron.tick()`, which calls `run_slot`
for each slot whose time has passed today. The once-a-day guarantee is a unique row on
(person, slot, date) — there is no "have I already?" question to get wrong.

Delivery uses the token a page is handed inside the SyncUp app (`window.SyncUp.token`), posted to
SyncUp's partner endpoint. No token, or no SyncUp base configured, means nothing is sent and the
run says so — the person still sees the same line at the top of Today.
"""
import logging
from datetime import datetime, timedelta

from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone

from core import push as core_push
from core.models import HubSettings

from . import cycle
from .models import Flow, NotifyLine, NotifyMute, NotifySent, NotifySlot, Person, Settings

logger = logging.getLogger(__name__)

TITLE_MAX = core_push.TITLE_MAX
BODY_MAX = core_push.BODY_MAX
SEND_TIMEOUT = core_push.SEND_TIMEOUT
# A person pressed a button and is watching the page, so this one may wait longer than the cron.
TEST_TIMEOUT = 12

S = NotifyLine.Situation


def first_name(person):
    return (person.name or '').strip().split(' ')[0] or 'there'


def low_items(person):
    return [i for i in person.items.filter(is_active=True) if i.is_low]


def situation_for(person, config, today=None):
    """Which situation a person is in, and the numbers their wording may use.

    Returns (situation, context) or (None, context) when there is nothing at all to go on.
    """
    today = today or config.today
    days = list(person.days.values_list('date', 'flow'))
    stats = cycle.stats(days, today=today, default_cycle=config.default_cycle,
                        default_period=config.default_period, cycle_hint=person.cycle_hint)
    context = {
        'name': first_name(person),
        'day': stats.cycle_day or 0,
        'cycle': stats.cycle_len,
        'days': abs(stats.days_until) if stats.days_until is not None else 0,
        'late': abs(stats.days_until) if stats.is_overdue else 0,
        'items': ', '.join(f'{i.name} ({i.in_stock} left)' for i in low_items(person)[:2]),
    }

    periods = cycle.periods(days)
    running = bool(periods) and periods[-1].shown_to >= today - timedelta(days=1)
    logged_today = any(d == today for d, _ in days)

    if running and periods[-1].end >= today - timedelta(days=1):
        return (S.RUNNING_LOGGED if logged_today else S.RUNNING_UNLOGGED), context
    if stats.days_until is None:
        return None, context
    if stats.is_overdue:
        return (S.LATE_EARLY if context['late'] <= 3 else S.LATE_LONG), context
    if stats.days_until == 0:
        return S.DUE_TODAY, context
    if stats.days_until <= 2:
        return S.DUE_SOON, context
    if cycle.phase(stats, today=today) in ('ovulation',):
        return S.OVULATION, context
    return S.QUIET, context


def pick_line(slot, situation, person, today):
    """Pick one wording, the way the old app did: by the date and the person.

    Two people never get the same line on the same day, and one person does not get the same line
    two days running. Each slot has its own pool, so two messages in one day cannot match.
    """
    lines = list(slot.lines.filter(situation=situation, is_active=True).order_by('id'))
    if not lines:
        return None
    index = (today.toordinal() + person.pk + slot.order) % len(lines)
    return lines[index]


def compose(person, slot, config, today=None):
    """The title and body to send, or None when this slot should stay quiet.

    A slot in "only if there is news" mode speaks only when something needs doing; a low-stock
    sentence rides along with the morning line when there is room for it.
    """
    today = today or config.today
    situation, context = situation_for(person, config, today)
    lows = low_items(person)

    # Something worth saying out loud, for a slot that is only allowed to speak when there is.
    NEWS_WORTHY = (S.RUNNING_UNLOGGED, S.RUNNING_LOGGED, S.DUE_TODAY, S.DUE_SOON, S.LATE_EARLY)

    if slot.mode == NotifySlot.Mode.NEWS and situation not in NEWS_WORTHY:
        situation = None                       # a quiet day: this slot stays quiet

    line = pick_line(slot, situation, person, today) if situation else None
    if not line and lows:
        # Either nothing to report about the cycle, or this slot has no words for it — but
        # something is low, which is always worth a sentence.
        situation = S.LOW_STOCK
        line = pick_line(slot, situation, person, today)
    if not line:
        return None

    body = line.text.format(**context)[:BODY_MAX]

    # The low-stock sentence rides along on an "always" slot when it fits.
    if slot.mode == NotifySlot.Mode.ALWAYS and lows and situation != S.LOW_STOCK:
        extra = pick_line(slot, S.LOW_STOCK, person, today)
        if extra:
            tail = ' ' + extra.text.format(**context)
            if len(body) + len(tail) <= BODY_MAX:
                body += tail

    title = f'{config.display_name} · Hi {context["name"]}'[:TITLE_MAX]
    return {'situation': situation, 'line': line, 'title': title, 'body': body}


def send_via_syncup(config, person, title, body, url, timeout=SEND_TIMEOUT):
    """Post to SyncUp's partner endpoint. Returns (delivered, error).

    `timeout` is short for the cron — it has a whole run to get through and a slow host must not
    hold it up — and longer for the Test push, where somebody is watching the page. The transport
    itself lives in `core.push`, shared with every other module.
    """
    return core_push.send(person.syncup_token, title, body, url, timeout)


def arrival_message(person, config, today=None):
    """One line about where the cycle stands, for someone who has just arrived.

    The old Flask app did exactly this and nothing more: a status sentence, plus a word about
    anything running low. It is deliberately not one of the slot wordings — those are written for
    a time of day ("log before bed"), and this fires whenever somebody signs in.
    """
    today = today or config.today
    situation, context = situation_for(person, config, today)
    days = list(person.days.values_list('date', 'flow'))
    stats = cycle.stats(days, today=today, default_cycle=config.default_cycle,
                        default_period=config.default_period, cycle_hint=person.cycle_hint)

    if stats.predicted_next is None:
        body = 'Log your first period and the predictions start from there.'
    elif situation in (S.RUNNING_LOGGED, S.RUNNING_UNLOGGED):
        body = f'You are on day {stats.cycle_day} of your period.'
        if situation == S.RUNNING_UNLOGGED:
            body += ' Today is not logged yet.'
    elif stats.is_overdue:
        late = abs(stats.days_until)
        body = (f'Your period is {late} day{"" if late == 1 else "s"} late — it was expected '
                f'{stats.predicted_next:%a %d %b}.')
    elif stats.days_until == 0:
        body = f'Your period may start today ({stats.predicted_next:%a %d %b}).'
    elif stats.days_until == 1:
        body = f'Your period is expected tomorrow, {stats.predicted_next:%a %d %b}.'
    else:
        body = (f'Your next period is expected around {stats.predicted_next:%a %d %b} — '
                f'about {stats.days_until} days away.')

    lows = low_items(person)
    if lows:
        tail = ' Running low: ' + ', '.join(f'{i.name} ({i.in_stock} left)' for i in lows[:2]) + '.'
        if len(body) + len(tail) <= BODY_MAX:
            body += tail

    return {'title': f'Hi {context["name"]}'[:TITLE_MAX], 'body': body[:BODY_MAX]}


def diary_url(config=None):
    """Where a tapped notification should land: the diary, on this site.

    The address belongs to the hub (System → Settings), because every module opens on the same
    site. Empty until somebody sets it, and an empty link is harmless — SyncUp just opens its
    own app.
    """
    return HubSettings.load().url_for(reverse('hibiscus_me_home'))


def send_test(config, person, url=''):
    """One message, now, to one person — the admin pressing "Test push".

    Deliberately not a composed line: it says what it is, so whoever gets it on their phone knows
    why. Nothing is written to NotifySent, because that table is the once-a-day guarantee for
    real slots and a test is not one of those; the audit row is the record.
    """
    when = timezone.localtime(timezone.now(), config.tzinfo)
    title = f'{config.display_name} · test message'[:TITLE_MAX]
    body = (f'Sent from the admin console at {when:%H:%M}. '
            f'If this reached you, notifications are working.')[:BODY_MAX]
    delivered, error = send_via_syncup(config, person, title, body, url or diary_url(config),
                                       timeout=TEST_TIMEOUT)
    return {'delivered': delivered, 'error': error, 'title': title, 'body': body}


def slot_is_due(slot, config, now_local):
    """True when the slot's time has passed today and is not yet stale.

    The cron runs every 15 minutes, so a slot fires on the first tick at or after its time —
    never more than 15 minutes late. Later than `notify_late_hours` and it is dropped: after an
    outage, yesterday evening's nudge at lunchtime is worse than nothing.
    """
    due_at = datetime.combine(now_local.date(), slot.at, tzinfo=now_local.tzinfo)
    if now_local < due_at:
        return False
    return (now_local - due_at) <= timedelta(hours=config.notify_late_hours)


def run_slot(slot, config, now_local=None, base_url='', dry_run=False):
    """Send this slot to everyone who has not had it today. Returns a small summary."""
    now_local = now_local or timezone.localtime(timezone.now(), config.tzinfo)
    today = now_local.date()
    out = {'slot': slot.key, 'sent': 0, 'quiet': 0, 'muted': 0, 'failed': 0}

    muted = set(NotifyMute.objects.filter(slot=slot).values_list('person_id', flat=True))
    already = set(NotifySent.objects.filter(slot=slot, date=today)
                  .values_list('person_id', flat=True))

    for person in Person.objects.filter(is_active=True, is_archived=False):
        if person.pk in already:
            continue
        if person.pk in muted:
            out['muted'] += 1
            continue
        message = compose(person, slot, config, today)
        if not message:
            out['quiet'] += 1
            continue
        if dry_run:
            out.setdefault('preview', []).append(
                {'person': person.name, 'situation': message['situation'], 'body': message['body']})
            continue

        delivered, error = send_via_syncup(config, person, message['title'], message['body'],
                                           base_url)
        try:
            NotifySent.objects.create(
                person=person, slot=slot, date=today, line=message['line'],
                situation=message['situation'], title=message['title'], body=message['body'],
                delivered=delivered, error=error,
            )
        except IntegrityError:
            continue                       # another tick got there first; that is the guarantee
        if error:
            out['failed'] += 1
        else:
            out['sent'] += 1
    return out
