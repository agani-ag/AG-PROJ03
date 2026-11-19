"""The console side of Games — inside SyncHub, behind SyncHub's admin login.

This is the one place that says which chairs the server is holding, and it says so plainly.
It is for three of us; a table that will not move has to be explicable when you are looking at
it, and a log that cannot tell a person from this server is not a log.
"""
from django.contrib import messages
from django.db.models import Count
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.views import admin_required

from .models import Game, Player, Room
from .uno import deck, rules


@admin_required
def rooms(request):
    rows = (Room.objects.annotate(seat_count=Count('seats', distinct=True),
                                  round_count=Count('games', distinct=True))
            .select_related('host')[:120])
    return render(request, 'games/admin/rooms.html', {
        'rows': rows,
        'live': Room.objects.filter(phase=Room.Phase.PLAYING).count(),
        'players': Player.objects.count(),
    })


@admin_required
def players(request):
    rows = Player.objects.select_related('member').order_by('-last_seen_at', 'name')[:200]
    return render(request, 'games/admin/players.html', {'rows': rows})


@admin_required
def room_detail(request, code):
    """One table, as it stands. Seats, options, the round, and the move log."""
    room = get_object_or_404(Room, code=(code or '').upper())
    game = room.games.first()
    seats = list(room.seats.order_by('position'))
    state = game.state if game else {}
    hands = []
    for seat in seats:
        cards = (state.get('hands') or [[]] * len(seats))
        held = cards[seat.position] if seat.position < len(cards) else []
        hands.append({
            'seat': seat,
            # Plainly, and only here: an empty chair, or one somebody walked away from.
            'is_house': seat.is_held,
            'walked_off': bool(seat.player_id and seat.away),
            'cards': [deck.label(c) for c in held],
            'count': len(held),
            'turn': state.get('turn') == seat.position,
        })
    return render(request, 'games/admin/room.html', {
        'room': room, 'game': game, 'hands': hands,
        'options': rules.options(room.options),
        'top': deck.label(state['discard'][-1]) if state.get('discard') else '—',
        'colour': state.get('colour') or '—',
        'log': list(reversed(state.get('log') or [])),
        'moves': game.moves.all()[:200] if game else [],
    })


@admin_required
@require_POST
def room_end(request, code):
    """Stop a table that has stopped on its own."""
    room = get_object_or_404(Room, code=(code or '').upper())
    Game.objects.filter(room=room, finished_at__isnull=True).update(finished_at=timezone.now())
    Room.objects.filter(pk=room.pk).update(phase=Room.Phase.CLOSED)
    messages.success(request, f'{room.code} closed.')
    return redirect('games_rooms')


@admin_required
def settings_page(request):
    """What the module is, and the one dangerous button, while it is being tested."""
    if request.method == 'POST' and request.POST.get('confirm') == 'ERASE GAMES':
        Room.objects.all().delete()
        Player.objects.all().delete()
        messages.success(request, 'Games is empty.')
        return redirect('games_settings_admin')
    if request.method == 'POST':
        messages.error(request, 'Type ERASE GAMES exactly.')
    return render(request, 'games/admin/settings.html', {
        'rooms': Room.objects.count(),
        'games': Game.objects.count(),
        'players': Player.objects.count(),
        'defaults': rules.DEFAULTS,
    })
