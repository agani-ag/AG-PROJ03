"""The API every non-browser client speaks — the Windows app, the terminal, anything later.

Four endpoints, no cookies, no CSRF, no HTML. The Self-ID travels in `X-J6-Self`, which is what
that ID was invented for: a credential a person can carry anywhere and paste into anything.

Everything here calls `service`, so a desktop client and a browser cannot disagree about what a
message is, what counts as read, or whether a cap has been hit.
"""
import json

from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from . import auth, service
from .models import Chat, Settings

VERSION = '1.0.0'          # of the desktop client this server expects; see desktop/j6desk


def _body(request):
    try:
        return json.loads(request.body or b'{}')
    except ValueError:
        return {}


def _nostore(response):
    response['Cache-Control'] = 'no-store'
    return response


@csrf_exempt
@require_POST
def start(request):
    """Make an account from a client — the same thing the welcome screen does.

    A desktop app must be able to create an account, not only sign one in; otherwise the first
    run would be "go and open a browser first", which defeats the point of a portable EXE.
    """
    try:
        account = service.create_account(_body(request).get('name'))
    except ValidationError as exc:
        return _nostore(JsonResponse({'error': exc.messages[0]}, status=403))
    return _nostore(JsonResponse({
        'account_id': account.account_id,
        'self_id': account.self_id,
        'name': account.name,
    }, status=201))


@csrf_exempt
@require_POST
@auth.token_required
def hello(request):
    """Who this Self-ID belongs to, and what they have. The client's first call."""
    account = request.account
    return _nostore(JsonResponse({
        'account_id': account.account_id,
        'name': account.name,
        'chats': service.chat_rows(account),
        'media': service.media_rules(account),
        'version': VERSION,
    }))


@csrf_exempt
@require_GET
@auth.token_required
def poll(request):
    """The same payload the browser polls for, so there is one shape to understand."""
    chat = None
    if request.GET.get('chat'):
        chat = Chat.objects.filter(pk=request.GET['chat']).first()
        if not chat or request.account.pk not in (chat.low_id, chat.high_id):
            return JsonResponse({'error': 'no such chat'}, status=404)
    since = int(request.GET.get('since') or 0)
    return _nostore(JsonResponse(service.poll_payload(request.account, chat, since)))


@csrf_exempt
@require_POST
@auth.token_required
def send(request):
    data = _body(request)
    chat = Chat.objects.filter(pk=data.get('chat')).first()
    if not chat or request.account.pk not in (chat.low_id, chat.high_id):
        return JsonResponse({'error': 'no such chat'}, status=404)
    try:
        message = service.send(request.account, chat, data.get('body'))
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=400)
    return _nostore(JsonResponse({'seq': message.seq, 'chat': chat.pk,
                                  'at': int(message.created_at.timestamp() * 1000)}, status=201))


@csrf_exempt
@require_POST
@auth.token_required
def new_chat(request):
    """Start a chat from an Account ID, for a client that has no page to post a form from."""
    try:
        chat, created = service.start_chat(request.account, _body(request).get('account_id'))
    except ValidationError as exc:
        return JsonResponse({'error': exc.messages[0]}, status=400)
    other = chat.other(request.account)
    return _nostore(JsonResponse({'chat': chat.pk, 'name': other.name, 'created': created},
                                 status=201 if created else 200))


@csrf_exempt
@require_POST
@auth.token_required
def read(request):
    data = _body(request)
    chat = Chat.objects.filter(pk=data.get('chat')).first()
    if not chat or request.account.pk not in (chat.low_id, chat.high_id):
        return JsonResponse({'error': 'no such chat'}, status=404)
    top = service.mark_read(request.account, chat, data.get('seq'))
    return _nostore(JsonResponse({'read_upto': top}))


@require_GET
def version(request):
    """What the client checks on launch to know whether a newer build exists."""
    config = Settings.load()
    base = (config.site_base or '').rstrip('/')
    return _nostore(JsonResponse({
        'version': VERSION,
        'download': 'https://github.com/agani-ag/AG-PROJ03/releases/latest/download/j6.exe',
        'site': base,
    }))
