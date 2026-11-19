"""The tables themselves — what somebody sees on their phone.

Two endpoints do the work: one poll and one move. Everything else is a page.

The poll is the same arrangement J6 uses, because this host cannot do WebSockets: an ETag so
that a table where nobody has done anything costs a request with no body. UNO suits it better
than chat does — you only need fresh state when it is *not* your turn, so the page polls every
two seconds while waiting and every ten while it is the one thinking.
"""
import hashlib
import json

from django.contrib import messages
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core import auth as hub

from . import auth, service
from .models import Game, Room
from .uno import deck, rules


def _no_store(response):
    response['Cache-Control'] = 'no-store, max-age=0'
    return response


def _context(request, tab=''):
    return {'player': getattr(request, 'player', None), 'tab': tab}


@never_cache
def join(request):
    """Starting Games, for somebody the hub already knows. One field, and even that is filled in."""
    member = hub.current(request)
    if not member:
        return redirect(f'{reverse("member_login")}?next={request.get_full_path()}')
    player = auth.profile_for(member)
    if player:
        return redirect('games_home')
    if request.method == 'POST':
        name = (request.POST.get('name') or member.name or 'Player').strip()[:40]
        player = auth.start(member)
        if name:
            player.name = name
            player.save(update_fields=['name'])
        return redirect('games_home')
    return _no_store(render(request, 'games/user/join.html',
                            {'member': member, 'suggested': (member.name or '')[:40]}))


@auth.player_required
@never_cache
def home(request):
    """Your tables, and the two ways to get to one."""
    rooms = (Room.objects.filter(seats__player=request.player)
             .exclude(phase=Room.Phase.CLOSED).distinct()[:10])
    return _no_store(render(request, 'games/user/home.html', {
        **_context(request, 'home'),
        'rooms': rooms,
        'error': request.GET.get('error', ''),
    }))


@auth.player_required
@require_POST
def new_room(request):
    """A table, with the toggles chosen there and then."""
    options = {name: bool(request.POST.get(name)) for name in rules.DEFAULTS}
    try:
        target = int(request.POST.get('target') or 0)
    except ValueError:
        target = 0
    room = service.make_room(request.player, options, 0 if target not in (200, 500) else target)
    return redirect('games_room', code=room.code)


@auth.player_required
@require_POST
def join_room(request):
    code = (request.POST.get('code') or '').strip().upper()
    room = Room.objects.filter(code=code).first()
    if not room:
        messages.error(request, 'No table with that code.')
        return redirect('games_home')
    seat, error = service.join(room, request.player)
    if error:
        messages.error(request, error)
        return redirect('games_home')
    return redirect('games_room', code=room.code)


def _room_for(request, code):
    room = get_object_or_404(Room, code=(code or '').upper())
    seat = service.seat_of(room, request.player)
    return room, seat


@auth.player_required
@never_cache
def room(request, code):
    """The table. Everything after this arrives through the poll."""
    room, seat = _room_for(request, code)
    if not seat and not room.is_open:
        raise Http404('That game has already started')
    game = room.games.filter(finished_at__isnull=True).first()
    if game:
        game = service.catch_up(game)
    payload = service.table(room, game, seat)
    return _no_store(render(request, 'games/user/room.html', {
        **_context(request, 'home'),
        'room': room, 'seat': seat, 'game': game,
        'boot': json.dumps(payload),
        'cards': json.dumps([{'c': deck.colour_of(i) or '', 'v': deck.value_of(i)}
                             for i in range(deck.SIZE)]),
        'can_start': bool(seat and room.host_id == request.player.pk and room.is_open),
        'seat_count': room.seats.count(),
        'max_seats': service.MAX_SEATS,
    }))


@auth.player_required
@require_POST
def seat_room(request, code):
    """Add a chair, start the round, or give a chair up — the lobby's three buttons."""
    room, seat = _room_for(request, code)
    what = request.POST.get('what')
    is_host = seat and room.host_id == request.player.pk

    if what == 'fill' and is_host and room.is_open:
        if not service.fill(room, room.seats.count() + 1):
            messages.error(request, 'No room for another.')
    elif what == 'start' and is_host:
        game, error = service.start(room)
        if error:
            messages.error(request, error)
    elif what == 'leave':
        if not service.leave(room, request.player):
            messages.error(request, 'You cannot leave this one.')
        return redirect('games_home')
    elif what == 'again' and is_host and room.phase == Room.Phase.LOBBY:
        game, error = service.start(room)
        if error:
            messages.error(request, error)
    return redirect('games_room', code=room.code)


@auth.player_required
@never_cache
def poll(request, code):
    """The one thing the table asks for, over and over. 304 when nothing has moved."""
    room, seat = _room_for(request, code)
    service.seen(request.player)
    game = room.games.filter(finished_at__isnull=True).first()
    if game:
        game = service.catch_up(game)
    elif room.phase == Room.Phase.PLAYING:
        game = room.games.first()
    payload = service.table(room, game, seat)
    body = json.dumps(payload, separators=(',', ':'), sort_keys=True)
    etag = '"' + hashlib.md5(body.encode()).hexdigest()[:16] + '"'
    if request.META.get('HTTP_IF_NONE_MATCH') == etag:
        response = JsonResponse({}, status=304)
    else:
        response = JsonResponse(payload)
    response['ETag'] = etag
    return _no_store(response)


@auth.player_required
@require_POST
@never_cache
def move(request, code):
    """Play, draw, pass, name a colour, call it, catch somebody. One door for all of them."""
    room, seat = _room_for(request, code)
    if not seat:
        raise Http404('Not your table')
    game = room.games.filter(finished_at__isnull=True).first()
    if not game:
        return _no_store(JsonResponse({'error': 'That round is over.'}, status=409))

    data = {}
    if request.content_type and 'json' in request.content_type:
        try:
            data = json.loads(request.body or b'{}')
        except ValueError:
            data = {}
    else:
        data = dict(request.POST.items())
    for key in ('card', 'seat'):
        if key in data and data[key] not in (None, ''):
            try:
                data[key] = int(data[key])
            except (TypeError, ValueError):
                return _no_store(JsonResponse({'error': 'That is not a card.'}, status=400))

    game, error = service.act(game, seat.position, data.get('what') or request.POST.get('what'),
                              data, timezone.now())
    payload = service.table(room, game, seat)
    if error:
        payload['error'] = error
    return _no_store(JsonResponse(payload))


@auth.player_required
@never_cache
def settings_page(request):
    """The one thing there is to change: the name the table shows."""
    player = request.player
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()[:40]
        if not name:
            messages.error(request, 'A name, however short.')
        else:
            player.name = name
            player.save(update_fields=['name'])
            messages.success(request, 'Saved.')
        return redirect('games_settings')
    return _no_store(render(request, 'games/user/settings.html', {
        **_context(request, 'settings'),
        'synced': bool(player.member and player.member.syncup_id),
    }))
