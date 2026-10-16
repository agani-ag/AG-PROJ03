"""The chat itself — what people see at /j6/.

Server-rendered pages with one JSON endpoint behind them. The inbox is a single template used
twice: at /j6/ it is a list, at /j6/c/<id> it is a list *and* a thread, and CSS decides
which of the two a phone shows. That is AG-PROJ01's support-chat layout, and it is why there is
one stylesheet rather than two.

Every page sends `Cache-Control: no-store`: a cached chat is a wrong chat.
"""
import json

from django.core.exceptions import ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import auth, service
from .models import Account, Chat, Settings


def _no_store(response):
    """No caching, and a referrer policy that does not break form posts.

    `no-referrer` makes browsers serialise the Origin header as "null", which Django's CSRF
    check refuses — the mistake that broke every form in Hibiscus once. `same-origin` keeps the
    referrer inside the site and sends none to anyone else, which is what was wanted.
    """
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'same-origin'
    return response


def _page(request, template, context, status=200):
    config = Settings.load()
    account = getattr(request, 'account', None) or auth.current(request)
    base = {'config': config, 'account': account}
    if account:
        base['media'] = service.media_rules(account, config)
    base.update(context)
    return _no_store(render(request, template, base, status=status))


# --------------------------------------------------------------------------- #
# Getting in
# --------------------------------------------------------------------------- #
@never_cache
def welcome(request):
    """One field, one button. The account is made here or nowhere."""
    if auth.current(request):
        return redirect('j6_inbox')
    config = Settings.load()
    error = ''
    if request.method == 'POST':
        try:
            account = service.create_account(request.POST.get('name'), config)
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            auth.sign_in(request, account)
            request.session['j6_fresh'] = True       # so /ids shows them once, deliberately
            return redirect('j6_ids')
    return _page(request, 'j6/user/welcome.html', {'error': error, 'config': config})


@never_cache
@auth.account_required
def ids(request):
    """The two IDs, shown straight after sign-up and readable for ever from the profile."""
    fresh = request.session.pop('j6_fresh', False)
    link = request.build_absolute_uri(reverse('j6_magic', args=[request.account.self_id]))
    return _page(request, 'j6/user/ids.html', {'fresh': fresh, 'link': link})


@never_cache
def sign_in(request):
    """Paste a Self-ID. The whole sign-in, because there is nothing else to ask for."""
    if auth.current(request):
        return redirect('j6_inbox')
    error = ''
    if request.method == 'POST':
        account = auth.by_self_id(request.POST.get('self_id'))
        if account:
            auth.sign_in(request, account)
            return redirect('j6_inbox')
        error = 'That Self-ID does not work. Check it, or start a new account.'
    return _page(request, 'j6/user/login.html', {'error': error})


@never_cache
def magic(request, self_id):
    """The one-tap link: sign in, then redirect so the key leaves the address bar.

    Also how the desktop window hands its account to the web UI it is wrapping — it navigates
    here once after creating or restoring an account.
    """
    account = auth.by_self_id(self_id)
    if not account:
        return _no_store(redirect('j6_login'))
    auth.sign_in(request, account)
    return _no_store(redirect('j6_inbox'))


def sign_out(request):
    auth.sign_out(request)
    return redirect('j6_welcome')


# --------------------------------------------------------------------------- #
# The inbox
# --------------------------------------------------------------------------- #
@never_cache
@auth.account_required
def inbox(request, pk=None):
    """List, or list plus thread. One template, one stylesheet, two layouts."""
    account = request.account
    chat = None
    if pk is not None:
        chat = get_object_or_404(Chat.objects.select_related('low', 'high'), pk=pk)
        if account.pk not in (chat.low_id, chat.high_id):
            raise Http404('Not your chat')
        service.mark_read(account, chat)

    rows = service.chat_rows(account)
    thread = service.thread(account, chat) if chat else None
    return _page(request, 'j6/user/inbox.html', {
        'rows': rows,
        'rows_json': json.dumps(rows),
        'chat': chat,
        'thread_json': json.dumps(thread) if thread else 'null',
        'has_thread': bool(chat),
    })


@never_cache
@auth.account_required
def new_chat(request):
    """Paste their Account ID, or hand over yours. Both halves of starting a conversation."""
    error = ''
    if request.method == 'POST':
        try:
            chat, _ = service.start_chat(request.account, request.POST.get('account_id'))
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            return redirect('j6_chat', pk=chat.pk)
    return _page(request, 'j6/user/new.html', {'error': error})


@never_cache
@auth.account_required
def profile(request):
    """Name, both IDs, the media switch, theme, blocked list, sign out."""
    account = request.account
    saved = ''
    if request.method == 'POST':
        what = request.POST.get('what')
        try:
            if what == 'name':
                service.rename(account, request.POST.get('name'))
                saved = 'Name saved.'
            elif what == 'media':
                account.load_media = request.POST.get('load_media') == 'on'
                account.save(update_fields=['load_media'])
                saved = 'Saved.'
            elif what == 'theme':
                theme = request.POST.get('theme', 'system')
                if theme in dict(Account.THEMES):
                    account.theme = theme
                    account.save(update_fields=['theme'])
                saved = 'Saved.'
        except ValidationError as exc:
            saved = exc.messages[0]

    link = request.build_absolute_uri(reverse('j6_magic', args=[account.self_id]))
    blocked = [c for c in service.chats_for(account)
               if (c.member_for(account) and c.member_for(account).is_blocked)]
    return _page(request, 'j6/user/profile.html', {
        'saved': saved, 'link': link,
        'blocked': [{'chat': c, 'other': c.other(account)} for c in blocked],
    })


# --------------------------------------------------------------------------- #
# The one endpoint everything polls
# --------------------------------------------------------------------------- #
@never_cache
@auth.account_required
def poll(request):
    """List rows always, a thread when one is open. 304 when nothing has changed.

    The ETag is the point: a quiet chat costs a request with no body at all, which is what makes
    a three-second cadence affordable on a small host.
    """
    chat = None
    if request.GET.get('chat'):
        chat = Chat.objects.filter(pk=request.GET['chat']).first()
        if not chat or request.account.pk not in (chat.low_id, chat.high_id):
            return JsonResponse({'error': 'no such chat'}, status=404)
    since = int(request.GET.get('since') or 0)
    # A poll from a tab that is actually in front *is* reading. Doing it here rather than in a
    # second endpoint means one rule: if you can see it, you have read it.
    if chat and request.GET.get('seen'):
        service.mark_read(request.account, chat)
    payload = service.poll_payload(request.account, chat, since)
    return _conditional(request, payload)


def _conditional(request, payload):
    import hashlib
    body = json.dumps(payload, separators=(',', ':'))
    etag = '"' + hashlib.md5(body.encode()).hexdigest()[:16] + '"'
    if request.META.get('HTTP_IF_NONE_MATCH') == etag:
        response = JsonResponse({}, status=304)
    else:
        response = JsonResponse(payload)
    response['ETag'] = etag
    return _no_store(response)


@require_POST
@auth.account_required
def send(request):
    chat = get_object_or_404(Chat, pk=request.POST.get('chat') or _json(request).get('chat'))
    if request.account.pk not in (chat.low_id, chat.high_id):
        raise Http404('Not your chat')
    body = request.POST.get('body') or _json(request).get('body')
    try:
        message = service.send(request.account, chat, body)
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=400)
    return _no_store(JsonResponse({'message': {
        'seq': message.seq, 'mine': True, 'sender': request.account.name,
        'body': message.body, 'at': int(message.created_at.timestamp() * 1000),
        'delivered': False,
    }}))


@require_POST
@auth.account_required
def typing(request):
    chat = get_object_or_404(Chat, pk=request.POST.get('chat') or _json(request).get('chat'))
    if request.account.pk in (chat.low_id, chat.high_id):
        service.set_typing(request.account, chat)
    return _no_store(JsonResponse({'ok': True}))


@require_POST
@auth.account_required
def block(request, pk):
    chat = get_object_or_404(Chat, pk=pk)
    if request.account.pk not in (chat.low_id, chat.high_id):
        raise Http404('Not your chat')
    member = chat.member_for(request.account)
    service.set_blocked(request.account, chat, not (member and member.is_blocked))
    return redirect('j6_chat', pk=chat.pk)


@require_POST
@auth.account_required
def save_syncup_token(request):
    """Inside the SyncUp app a page is handed `window.SyncUp.token`; this stores it.

    Without it the 15-minute cron has nothing to push to, and the person simply gets their
    messages when they open the page — which is why it fails quietly.
    """
    token = (_json(request).get('token') or '')[:4000]
    if token and token != request.account.syncup_token:
        request.account.syncup_token = token
        request.account.save(update_fields=['syncup_token'])
    return _no_store(JsonResponse({'ok': True}))


def _json(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}

def moved_from_me(request, rest=''):
    """Old `/j6/me/…` addresses, kept working.

    The diary's magic links and the chat's sign-in links were handed out with `me/` in them, and
    a link somebody saved in a note has to keep working — so the old shape answers a permanent
    redirect to the new one rather than a 404.
    """
    target = reverse('j6_inbox') + (rest or '')
    if request.META.get('QUERY_STRING'):
        target += '?' + request.META['QUERY_STRING']
    return redirect(target, permanent=True)
