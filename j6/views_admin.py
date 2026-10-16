"""The console side of J6 — inside SyncHub, behind the admin login.

The route through it is deliberately one-way: **accounts → one account → one of their chats**.
You never browse a list of conversations belonging to nobody; you pick a person and read what
they have, shown the way they have it. A chat id on its own is not a page, so "whose messages
am I reading" is never a guess.

No audit trail here: unlike the diary there is no entering-on-their-behalf to account for, and
the admin reads rather than writes.
"""
import logging

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.views import admin_required

from . import service
from .forms import SettingsForm
from .models import Account, Chat, Member, Message, Settings

logger = logging.getLogger(__name__)


@admin_required
def accounts(request):
    """The module's landing page: who exists, and whether this thing is being used."""
    config = Settings.load()
    search = (request.GET.get('q') or '').strip()
    rows = Account.objects.all()
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(account_id__icontains=search))
    rows = rows.annotate(chat_count=Count('chats_low', distinct=True) +
                                    Count('chats_high', distinct=True),
                         sent_count=Count('sent', distinct=True)).order_by('-last_seen_at')

    week = timezone.now() - timezone.timedelta(days=7)
    return render(request, 'j6/admin/accounts.html', {
        'config': config,
        'rows': Paginator(rows, 100).get_page(request.GET.get('page')),
        'search': search,
        'total': Account.objects.count(),
        'new_week': Account.objects.filter(created_at__gte=week).count(),
        'chats': Chat.objects.count(),
        'chats_today': Chat.objects.filter(last_message_at__date=timezone.localdate()).count(),
        'messages_week': Message.objects.filter(created_at__gte=week).count(),
        'push_ready': Account.objects.exclude(syncup_token='').count(),
        'suspended': Account.objects.filter(is_active=False).count(),
    })


@admin_required
def account(request, pk):
    """One person's conversations, as they have them."""
    person = get_object_or_404(Account, pk=pk)
    rows = []
    for chat in service.chats_for(person):
        other = chat.other(person)
        mine = chat.member_for(person)
        theirs = chat.members.exclude(account=person).first()
        last = chat.messages.order_by('-seq').first()
        rows.append({
            'chat': chat, 'other': other, 'last': last,
            'count': chat.messages.count(),
            'blocked_by_them': bool(mine and mine.is_blocked),
            'blocked_by_other': bool(theirs and theirs.is_blocked),
        })
    return render(request, 'j6/admin/account.html', {
        'config': Settings.load(), 'person': person, 'rows': rows,
        'messages_sent': Message.objects.filter(sender=person).count(),
    })


@admin_required
def chat(request, pk, chat_pk):
    """One conversation, rendered from that account's side — their messages on the right."""
    person = get_object_or_404(Account, pk=pk)
    conversation = get_object_or_404(Chat.objects.select_related('low', 'high'), pk=chat_pk)
    if person.pk not in (conversation.low_id, conversation.high_id):
        raise Http404('That chat is not theirs')

    others = []
    for other_chat in service.chats_for(person):
        last = other_chat.messages.order_by('-seq').first()
        others.append({'chat': other_chat, 'other': other_chat.other(person), 'last': last,
                       'count': other_chat.messages.count(),
                       'on': other_chat.pk == conversation.pk})

    # The admin sees everything, including what a block hides from the person themselves —
    # otherwise "why has this conversation stopped" would be unanswerable.
    found = list(conversation.messages.select_related('sender').order_by('seq'))
    rows = [{'m': m, 'mine': m.sender_id == person.pk, 'is_url': _is_bare_url(m.body)}
            for m in found]
    return render(request, 'j6/admin/chat.html', {
        'config': Settings.load(), 'person': person, 'chat': conversation,
        'other': conversation.other(person), 'rows': rows, 'others': others,
        'count': len(found),
        'oldest': found[0].created_at if found else None,
    })


def _is_bare_url(body):
    """A message that is nothing but a link — the shape the console refuses to load for you.

    Worked out here rather than in the template, so the rule is one expression in one place and
    the page stays readable.
    """
    text = (body or '').strip()
    return text.startswith('https://') and ' ' not in text and len(text) < 300


@admin_required
@require_POST
def account_state(request, pk, what):
    person = get_object_or_404(Account, pk=pk)
    if what == 'suspend':
        person.is_active = False
        person.save(update_fields=['is_active'])
        messages.success(request, f'{person.name} is suspended — they cannot sign in and '
                                  f'nothing is delivered to them.')
    elif what == 'resume':
        person.is_active = True
        person.save(update_fields=['is_active'])
        messages.success(request, f'{person.name} can sign in again.')
    else:
        raise Http404
    return redirect('j6_account', pk=person.pk)


@admin_required
@require_POST
def account_delete(request, pk):
    person = get_object_or_404(Account, pk=pk)
    name = person.name
    person.delete()                     # takes their chats and messages with them
    messages.success(request, f'{name} and everything of theirs is deleted.')
    return redirect('j6_accounts')


@admin_required
@require_POST
def chat_delete(request, pk, chat_pk):
    person = get_object_or_404(Account, pk=pk)
    conversation = get_object_or_404(Chat, pk=chat_pk)
    if person.pk not in (conversation.low_id, conversation.high_id):
        raise Http404
    other = conversation.other(person).name
    conversation.delete()
    messages.success(request, f'The conversation between {person.name} and {other} is deleted.')
    return redirect('j6_account', pk=person.pk)


@admin_required
@require_POST
def message_delete(request, pk, chat_pk, seq):
    """Delete one message outright.

    No tombstone, no "this message was deleted" — the row goes. A placeholder would be a second
    kind of message to render on every client and in every export, for no benefit: the admin is
    removing something that should not be there, not editing a conversation.

    The `seq` it leaves behind is a gap, which costs nothing: the poll asks for "anything after
    42", not "the next one".
    """
    person = get_object_or_404(Account, pk=pk)
    conversation = get_object_or_404(Chat, pk=chat_pk)
    if person.pk not in (conversation.low_id, conversation.high_id):
        raise Http404
    message = get_object_or_404(Message, chat=conversation, seq=seq)
    sender = message.sender.name
    message.delete()
    messages.success(request, f"Deleted {sender}'s message. It is gone for both of them — "
                              f'anyone with the chat open sees it disappear on their next poll.')
    return redirect('j6_admin_chat', pk=person.pk, chat_pk=conversation.pk)


@admin_required
def factory_reset(request):
    """Empty J6 completely — every account, chat and message — and start again.

    This exists because the first weeks of a thing like this are testing: accounts made to try a
    feature, chats full of "hello hello", nothing worth keeping. Rather than deleting forty rows
    by hand, you empty the module once and hand out fresh IDs.

    Guarded three ways: it is its own page, it needs the word RESET typed, and it says exactly
    what is about to go. The hub's settings are not touched — they belong to SyncHub, not to J6.
    """
    counts = {
        'accounts': Account.objects.count(),
        'chats': Chat.objects.count(),
        'messages': Message.objects.count(),
    }
    if request.method == 'POST':
        if (request.POST.get('confirm') or '').strip().upper() != 'RESET':
            messages.error(request, 'Nothing was deleted — type RESET to confirm.')
            return redirect('j6_reset')

        # Order does not matter (every foreign key cascades), but deleting accounts last keeps
        # the counts honest if something goes wrong halfway.
        Message.objects.all().delete()
        Member.objects.all().delete()
        Chat.objects.all().delete()
        Account.objects.all().delete()

        if request.POST.get('settings') == 'on':
            Settings.objects.all().delete()
            Settings.load()                      # recreated with the defaults
            note = ' Module settings are back to their defaults.'
        else:
            note = ' Module settings were kept.'

        logger.warning('J6 factory reset by %s: %s accounts, %s chats, %s messages deleted',
                       request.user.username, counts['accounts'], counts['chats'],
                       counts['messages'])
        messages.success(request, f"J6 is empty: {counts['accounts']} account"
                                  f"{'' if counts['accounts'] == 1 else 's'}, "
                                  f"{counts['chats']} chat{'' if counts['chats'] == 1 else 's'} "
                                  f"and {counts['messages']} message"
                                  f"{'' if counts['messages'] == 1 else 's'} deleted.{note}")
        return redirect('j6_accounts')

    return render(request, 'j6/admin/reset.html', {
        'config': Settings.load(),
        'counts': counts,
        'oldest': Message.objects.order_by('created_at').values_list('created_at', flat=True)
                                 .first(),
    })


@admin_required
def settings_page(request):
    config = Settings.load()
    form = SettingsForm(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        messages.success(request, 'Settings saved.')
        return redirect('j6_settings')

    last = Message.objects.aggregate(oldest=Max('created_at'))
    return render(request, 'j6/admin/settings.html', {
        'config': config, 'form': form,
        'accounts': Account.objects.count(),
        'messages_held': Message.objects.count(),
        'oldest': Message.objects.order_by('created_at').values_list('created_at', flat=True)
                                 .first(),
        'newest': last['oldest'],
    })
