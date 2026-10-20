"""Everything J6 actually does, in one file.

The browser views, the desktop API and the cron hooks are all thin: they work out who is asking
and then call something here. Keeping the rules in one place is what stops the web and the
desktop drifting apart — there is only one implementation of "may this be sent", "what has this
person got", "what counts as read".

Times go out as epoch milliseconds. The client formats them, so a person in another timezone
sees their own clock without the server knowing or caring.
"""
from datetime import timedelta

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from .models import (Account, Chat, Member, Message, Settings, TYPING_SECONDS, validate_name)


def _ms(moment):
    return int(moment.timestamp() * 1000) if moment else 0


# --------------------------------------------------------------------------- #
# Accounts
# --------------------------------------------------------------------------- #
def create_account(name, config=None):
    """A name and a tap. The only way an account comes into existence, from any client."""
    config = config or Settings.load()
    if not config.signup_open:
        raise ValidationError('New accounts are closed on this server just now.')
    clean = validate_name(name)
    return Account.objects.create(name=clean)


def rename(account, name):
    account.name = validate_name(name)
    account.save(update_fields=['name'])
    return account


# --------------------------------------------------------------------------- #
# Chats
# --------------------------------------------------------------------------- #
def start_chat(account, account_id, config=None):
    """Open a chat with whoever owns this Account ID.

    Nothing is created for a wrong ID — at 79 bits a typo resolves to nobody, so the honest
    answer is "no account with that ID" rather than an empty conversation.
    """
    config = config or Settings.load()
    other = Account.objects.filter(account_id=(account_id or '').strip().lower(),
                                   is_active=True).first()
    if not other:
        raise ValidationError('No account with that ID.')
    if other.pk == account.pk:
        raise ValidationError('That is your own ID.')

    existing = _chat_between(account, other)
    if existing:
        return existing, False

    since = timezone.now() - timedelta(hours=1)
    started = Chat.objects.filter(Q(low=account) | Q(high=account), created_at__gte=since).count()
    if started >= config.new_chats_per_hour:
        raise ValidationError('That is a lot of new chats in an hour. Try again later.')
    return Chat.between(account, other)


def _chat_between(one, other):
    low, high = sorted([one, other], key=lambda a: a.pk)
    return Chat.objects.filter(low=low, high=high).first()


def chats_for(account):
    """Every chat this account is in, newest first, with the other side resolved."""
    return (Chat.objects.filter(Q(low=account) | Q(high=account))
            .select_related('low', 'high')
            .prefetch_related('members'))


def chat_rows(account):
    """The chat list as the client wants it: one dict per row."""
    rows = []
    for chat in chats_for(account):
        mine = next((m for m in chat.members.all() if m.account_id == account.pk), None)
        theirs = next((m for m in chat.members.all() if m.account_id != account.pk), None)
        other = chat.other(account)
        last = (chat.messages.order_by('-seq').first() if not (mine and mine.is_blocked)
                else chat.messages.filter(sender=account).order_by('-seq').first())
        unread = 0
        if mine and not mine.is_blocked:
            unread = chat.messages.filter(seq__gt=mine.last_read_seq).exclude(
                sender=account).count()
        rows.append({
            'id': chat.pk,
            'name': other.name,
            'account_id': other.account_id,
            'initials': other.initials,
            'colour': other.colour,
            'online': other.is_online,
            'blocked': bool(mine and mine.is_blocked),
            'last': last.body[:120] if last else '',
            'last_mine': bool(last and last.sender_id == account.pk),
            'last_at': _ms(last.created_at if last else chat.created_at),
            'unread': unread,
            'typing': bool(theirs and theirs.is_typing and not (mine and mine.is_blocked)),
        })
    rows.sort(key=lambda r: r['last_at'], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def visible_messages(account, chat):
    """What this side may see.

    Blocking is silent and one-sided: having blocked somebody, you stop seeing what they write.
    They are told nothing and their messages are not deleted — they simply never arrive here.
    """
    mine = chat.member_for(account)
    qs = chat.messages.select_related('sender')
    if mine and mine.is_blocked:
        qs = qs.filter(sender=account)
    return qs


PAGE = 40          # how much of a conversation a page or a poll hands over at once


def thread(account, chat, since=0, limit=PAGE):
    """Messages after `since`, plus everything the header needs.

    `limit` is what keeps a long conversation from arriving all at once: on a first load it is
    the *newest* `limit` messages, and `has_more` tells the page there is history above to fetch
    when somebody scrolls for it. A poll (`since` set) is already incremental, so the limit only
    bites if somebody has been away long enough for that many to pile up.
    """
    mine = chat.member_for(account)
    theirs = chat.members.exclude(account=account).first()
    other = chat.other(account)

    visible = visible_messages(account, chat).filter(seq__gt=since or 0)
    total = visible.count()
    rows = list(visible.order_by('-seq')[:limit][::-1]) if limit else list(visible)
    has_more = bool(limit and total > len(rows))
    now = timezone.now()
    # Anything of theirs we are handing over has reached a device: that is the second tick.
    arriving = [m for m in rows if m.sender_id != account.pk and m.delivered_at is None]
    if arriving:
        Message.objects.filter(pk__in=[m.pk for m in arriving]).update(delivered_at=now)
        for m in arriving:
            m.delivered_at = now

    return {
        'chat': chat.pk,
        'with': {
            'name': other.name, 'account_id': other.account_id, 'initials': other.initials,
            'colour': other.colour, 'online': other.is_online,
            'last_seen': _ms(other.last_seen_at),
        },
        'blocked': bool(mine and mine.is_blocked),
        'typing': bool(theirs and theirs.is_typing and not (mine and mine.is_blocked)),
        'read_upto': theirs.last_read_seq if theirs else 0,
        'has_more': has_more,
        'from_seq': rows[0].seq if rows else 0,
        'messages': [{
            'seq': m.seq,
            'mine': m.sender_id == account.pk,
            'sender': m.sender.name,
            'body': m.body,
            'at': _ms(m.created_at),
            'delivered': bool(m.delivered_at),
        } for m in rows],
    }


def earlier(account, chat, before_seq, limit=PAGE):
    """The messages immediately before `before_seq` — what "load earlier" asks for.

    Returned oldest-first so the client can prepend them as a block, with `has_more` so the
    button knows whether to stay.
    """
    visible = visible_messages(account, chat).filter(seq__lt=before_seq or 0)
    rows = list(visible.order_by('-seq')[:limit][::-1])
    has_more = rows and visible.filter(seq__lt=rows[0].seq).exists()
    return {
        'messages': [{
            'seq': m.seq,
            'mine': m.sender_id == account.pk,
            'sender': m.sender.name,
            'body': m.body,
            'at': _ms(m.created_at),
            'delivered': bool(m.delivered_at),
        } for m in rows],
        'has_more': bool(has_more),
        'from_seq': rows[0].seq if rows else 0,
    }


def send(account, chat, body, config=None):
    """Write one message, if the caps allow it.

    The caps exist because anybody with a link can post here. They are settings rather than
    constants so they can be tightened without a deploy.
    """
    config = config or Settings.load()
    text = (body or '').strip()
    if not text:
        raise ValidationError('Nothing to send.')
    if len(text) > config.max_chars:
        raise ValidationError(f'That is longer than {config.max_chars} characters.')

    minute_ago = timezone.now() - timedelta(seconds=60)
    recent = Message.objects.filter(sender=account, created_at__gte=minute_ago).count()
    if recent >= config.msgs_per_minute:
        raise ValidationError('Slow down a moment — that is a lot of messages in a minute.')

    with transaction.atomic():
        locked = Chat.objects.select_for_update().get(pk=chat.pk)
        seq = locked.next_seq
        locked.next_seq = seq + 1
        locked.last_message_at = timezone.now()
        locked.save(update_fields=['next_seq', 'last_message_at'])
        message = Message.objects.create(chat=locked, seq=seq, sender=account, body=text)

    # Sending is reading: you have obviously seen everything above your own line.
    Member.objects.filter(chat=chat, account=account).update(last_read_seq=seq,
                                                             typing_until=None)

    # And if the other side is not looking, push it now rather than on the next quarter hour.
    # Everything that can be decided without a network call is decided inside; only a real
    # delivery costs a thread, so this does not slow the send down.
    from . import push as pusher
    pusher.notify_new_message(chat, message)
    return message


def mark_read(account, chat, seq=None):
    """Move this side's read mark. Never backwards, so a stale client cannot un-read anything."""
    mine = chat.member_for(account)
    if not mine:
        return 0
    top = seq if seq is not None else (
        visible_messages(account, chat).aggregate(top=Max('seq'))['top'] or 0)
    if top > mine.last_read_seq:
        Member.objects.filter(pk=mine.pk).update(last_read_seq=top)
    return max(top, mine.last_read_seq)


def set_typing(account, chat):
    """A flag with an expiry rather than a start/stop pair, so 'typing…' cannot stick on."""
    Member.objects.filter(chat=chat, account=account).update(
        typing_until=timezone.now() + timedelta(seconds=TYPING_SECONDS))


def set_blocked(account, chat, blocked):
    Member.objects.filter(chat=chat, account=account).update(is_blocked=bool(blocked))


def unread_total(account):
    total = 0
    for chat in chats_for(account):
        mine = next((m for m in chat.members.all() if m.account_id == account.pk), None)
        if mine and not mine.is_blocked:
            total += chat.messages.filter(seq__gt=mine.last_read_seq).exclude(
                sender=account).count()
    return total


# --------------------------------------------------------------------------- #
# What a poll answers with
# --------------------------------------------------------------------------- #
def poll_payload(account, chat=None, since=0):
    """One shape for every client: the list always, a thread when one is open."""
    # Deliberately no timestamp in here: the payload has to be byte-identical when nothing has
    # happened, or the ETag never matches and every poll pays for a body it did not need.
    payload = {'chats': chat_rows(account)}
    if chat is not None:
        payload['thread'] = thread(account, chat, since)
    return payload


def media_rules(account, config=None):
    """What the renderer in the page is allowed to do — the module's settings and theirs."""
    config = config or Settings.load()
    return {
        'render': bool(config.render_media and account.load_media),
        'autoplay_seconds': config.autoplay_seconds,
        'max_px': config.max_media_px,
        'allow_svg': config.allow_svg,
    }
