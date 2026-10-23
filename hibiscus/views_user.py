"""The diary itself — what Divya and Meena see.

A separate route (`/hibiscus/`), its own session, its own small stylesheet. Nothing of
SyncHub's console chrome. Four tabs: Today, Calendar, Log, Stocks.

Every page is server-rendered and sends `Cache-Control: no-store`, because inside the SyncUp app
a cached page could show yesterday's balance. The only JavaScript is the day sheet, the stock
buttons and the theme switch.
"""
import calendar as pycal
import json
from datetime import date, timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import F
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from core import auth as hub
from core import push as core_push

from . import auth, cycle, push
from .forms import DayRangeForm, MySettingsForm, StockItemForm, StockMoveForm
from .models import (DaySymptom, Flow, NotifyMute, NotifySlot, Person, PeriodDay, Settings,
                     StockItem, StockMove, Symptom)

PHASE_WORDS = {
    'menstrual': ('Your period', 'Rest where you can. This is the part that asks the most of you.'),
    'follicular': ('Building up', 'Energy usually climbs through this stretch.'),
    'fertile': ('Fertile window', 'By your average — a few days either side of ovulation.'),
    'ovulation': ('Ovulation', 'Around the middle of your cycle, going by your average.'),
    'luteal': ('Winding down', 'Cramps and tiredness are common now.'),
    'late': ('Running late', 'Later than your average. Often nothing, but worth an eye on it.'),
}


def _no_store(response):
    """No caching, and a referrer policy that does not break form posts.

    `no-referrer` looks stricter and was the first thing here, but under it a browser serialises
    the Origin header of a form POST as the string "null" — which Django's CSRF check refuses,
    so every form on this side answered 403 in a real browser (the test client sends no Origin at
    all, so nothing caught it). `same-origin` keeps the referrer inside the site and sends none to
    anyone else, which is what was actually wanted.
    """
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'same-origin'
    return response


def _context(request, config, tab=''):
    return {'person': getattr(request, 'person', None), 'config': config, 'tab': tab}


def _stats_for(person, config, today=None):
    days = list(person.days.values_list('date', 'flow'))
    return days, cycle.stats(days, today=today or config.today,
                             default_cycle=config.default_cycle,
                             default_period=config.default_period,
                             cycle_hint=person.cycle_hint)


# --------------------------------------------------------------------------- #
# Getting in
# --------------------------------------------------------------------------- #
@never_cache
def join(request):
    """Starting a diary, for somebody the hub already knows.

    The old form asked for a name, a contact and a password. The hub has all three now, so the
    only thing left to ask is the cycle hint — and even that is optional, because two logged
    periods replace it.
    """
    config = Settings.load()
    member = hub.current(request)
    if not member:
        return _no_store(redirect(f'{reverse("member_login")}'
                                  f'?next={reverse("hibiscus_join")}'))
    person = auth.profile_for(member)
    if person:
        return redirect('hibiscus_me_home')
    if not config.signup_open:
        return _no_store(render(request, 'hibiscus/user/closed.html',
                                {'config': config}, status=403))

    error = ''
    if request.method == 'POST':
        if config.invite_code and request.POST.get('invite_code', '').strip() != config.invite_code:
            return _no_store(render(request, 'hibiscus/user/join.html',
                                    {'config': config, 'member': member,
                                     'error': 'That invite code is not right.'}))
        hint = (request.POST.get('cycle_hint') or '').strip()
        person = Person.objects.create(
            member=member, name=member.name,
            cycle_hint=int(hint) if hint.isdigit() and 15 <= int(hint) <= 60 else None,
            signed_up_with='hub')
        auth.log('signed_up', person=person, detail=member.sign_in_id)
        messages.success(request, f'Welcome, {person.name.split(" ")[0]}.')
        return redirect('hibiscus_me_home')
    return _no_store(render(request, 'hibiscus/user/join.html',
                            {'config': config, 'member': member, 'error': error}))


def sign_in_view(request):
    """The diary has no sign-in of its own any more. One hub, one password."""
    return redirect(f'{reverse("member_login")}?next={reverse("hibiscus_me_home")}')


@never_cache
def magic_login(request, key):
    """Sign in by link alone — the diary's own address, still working.

    The key belongs to the member now, but the landing is the diary's: these links are in
    people's bookmarks and notifications, and the whole point of one is that it opens *the
    diary*, not a list of apps. So it signs in here and redirects, keeping the key out of the
    address bar of the page they end up on.
    """
    member = hub.member_by_login_key(key)
    person = auth.profile_for(member) if member else None
    if not person:
        messages.error(request, 'That link does not work any more. Ask for a new one, '
                                'or sign in with your password.')
        return _no_store(redirect('member_login'))

    hub.spend_login_key(member)
    hub.sign_in(request, member)
    auth.log('signed_in_with_link', person=person)
    return _no_store(redirect('hibiscus_me_home'))


GREETED_ON = 'hibiscus_greeted_on'


@never_cache
@auth.person_required
def my_reminder(request):
    """What to show someone on arrival, and whether this arrival is one.

    Asked by every diary page; answers `show: true` at most once per arrival, which is:
      * right after signing in — by password, by magic link, or at sign-up — and
      * the first page of a day for someone whose session was still open.

    Walking from Today to Calendar and back does not count, which is the whole point: the old
    Flask app fired its reminder once after login and never again until the next one.
    """
    person = request.person
    config = Settings.load()
    today = config.today
    just_signed_in = request.session.pop(auth.GREET_KEY, False)
    first_today = request.session.get(GREETED_ON) != today.isoformat()
    show = bool(just_signed_in or first_today)
    if show:
        request.session[GREETED_ON] = today.isoformat()

    message = push.arrival_message(person, config, today)
    return _no_store(JsonResponse({
        'show': show,
        'title': message['title'],
        'body': message['body'],
    }))


def sign_out_view(request):
    """Kept so old links do not dead-end. Ending the session is the hub's, and it is a POST."""
    return redirect('member_apps')


@auth.person_required
def home(request):
    config = Settings.load()
    person = request.person
    days, stats = _stats_for(person, config)
    today = config.today
    phase = cycle.phase(stats, today=today)
    lows = [i for i in person.items.filter(is_active=True) if i.is_low]
    logged_today = any(d == today for d, _ in days)

    # Browser-only people get no push, so the day's line is shown here instead.
    daily_line = None
    morning = NotifySlot.objects.filter(key=NotifySlot.Key.MORNING, is_enabled=True).first()
    if morning:
        message = push.compose(person, morning, config, today)
        daily_line = message['body'] if message else None

    progress = 0
    if stats.cycle_day and stats.cycle_len:
        progress = max(4, min(100, round(stats.cycle_day / stats.cycle_len * 100)))

    return _no_store(render(request, 'hibiscus/user/today.html', {
        **_context(request, config, 'home'),
        'stats': stats,
        'phase_key': phase,
        'phase': PHASE_WORDS.get(phase),
        'low_items': lows,
        'logged_today': logged_today,
        'daily_line': daily_line,
        'progress': progress,
        'day_count': len(days),
    }))


# --------------------------------------------------------------------------- #
# Calendar
# --------------------------------------------------------------------------- #
@auth.person_required
def month(request):
    config = Settings.load()
    person = request.person
    today = config.today
    try:
        year = int(request.GET.get('y', today.year))
        shown = int(request.GET.get('m', today.month))
    except (TypeError, ValueError):
        year, shown = today.year, today.month
    if not 1 <= shown <= 12 or not today.year - 20 <= year <= today.year + 2:
        year, shown = today.year, today.month      # a hand-typed URL cannot crash the page

    days, stats = _stats_for(person, config, today)
    marks = cycle.month_map(days, year, shown, today=today,
                            default_cycle=config.default_cycle,
                            default_period=config.default_period,
                            cycle_hint=person.cycle_hint)

    first_weekday = 6 if config.week_starts_sunday else 0
    weeks = pycal.Calendar(firstweekday=first_weekday).monthdatescalendar(year, shown)
    grid = [[{
        'date': day,
        'this_month': day.month == shown,
        'mark': marks.get(day, ''),
        'today': day == today,
        'future': day > today,
    } for day in week] for week in weeks]

    labels = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
    if not config.week_starts_sunday:
        labels = labels[1:] + labels[:1]

    previous = date(year, shown, 1) - timedelta(days=1)
    following = date(year, shown, pycal.monthrange(year, shown)[1]) + timedelta(days=1)

    return _no_store(render(request, 'hibiscus/user/calendar.html', {
        **_context(request, config, 'calendar'),
        'grid': grid, 'labels': labels, 'stats': stats,
        'month_name': date(year, shown, 1),
        'prev': previous, 'next': following,
        'can_go_next': following <= date(today.year + 1, today.month, 1),
        'expected_from': stats.predicted_next,
        'expected_to': (stats.predicted_next + timedelta(days=stats.period_len - 1)
                        if stats.predicted_next else None),
    }))


@auth.person_required
def day_sheet(request, on):
    """The sheet that opens when a day is tapped: pick a flow, tick symptoms, save.

    A fragment when JavaScript asks for it, a whole page otherwise, so the calendar still works
    with no JavaScript at all.
    """
    config = Settings.load()
    person = request.person
    try:
        day_date = date.fromisoformat(on)
    except ValueError:
        raise Http404('Not a date')
    if day_date > config.today:
        raise Http404('That day has not happened yet')

    row = person.days.filter(date=day_date).first()
    _, stats = _stats_for(person, config)

    if request.method == 'POST':
        flow = request.POST.get('flow', '')
        picked = request.POST.getlist('symptoms')
        note = (request.POST.get('note') or '')[:300]
        if flow == 'none':
            if row:
                row.delete()
                auth.log('cleared_day', person=person, detail=str(day_date))
                messages.success(request, f'{day_date:%d %b} cleared.')
        else:
            try:
                flow_value = int(flow)
            except ValueError:
                flow_value = int(Flow.MEDIUM)
            with transaction.atomic():
                row, created = PeriodDay.objects.update_or_create(
                    person=person, date=day_date,
                    defaults={'flow': flow_value, 'note': note})
                row.symptoms.all().delete()
                DaySymptom.objects.bulk_create(
                    [DaySymptom(day=row, symptom=s) for s in picked
                     if s in dict(Symptom.choices)])
            auth.log('logged_day' if created else 'edited_own_day', person=person,
                     detail=f'{day_date} · {Flow(flow_value).label}')
            messages.success(request, f'{day_date:%d %b} saved.')
        return redirect(request.POST.get('next') or 'hibiscus_me_calendar')

    chosen = list(row.symptoms.values_list('symptom', flat=True)) if row else []
    context = {
        **_context(request, config, 'calendar'),
        'day': day_date,
        'row': row,
        'chosen': chosen,
        'flows': Flow.choices,
        'symptoms': Symptom.choices,
        'cycle_day': (day_date - stats.last_start).days + 1 if stats.last_start else None,
        'next': request.GET.get('next', ''),
    }
    template = ('hibiscus/user/_day_sheet.html' if request.headers.get('X-Sheet')
                else 'hibiscus/user/day_page.html')
    return _no_store(render(request, template, context))


# --------------------------------------------------------------------------- #
# Log a period (a range at once)
# --------------------------------------------------------------------------- #
@auth.person_required
def log_period(request):
    config = Settings.load()
    person = request.person
    form = DayRangeForm(request.POST or None, today=config.today)
    if request.method == 'POST' and form.is_valid():
        dates = form.dates()
        with transaction.atomic():
            for day in dates:
                row, _ = PeriodDay.objects.update_or_create(
                    person=person, date=day,
                    defaults={'flow': form.cleaned_data['flow'],
                              'note': form.cleaned_data['note']})
                row.symptoms.all().delete()
                DaySymptom.objects.bulk_create(
                    [DaySymptom(day=row, symptom=s) for s in form.cleaned_data['symptoms']])
        auth.log('logged_range', person=person, detail=f'{dates[0]} to {dates[-1]}')
        messages.success(request, f'Saved {len(dates)} day{"" if len(dates) == 1 else "s"}.')
        return redirect('hibiscus_me_calendar')

    days, stats = _stats_for(person, config)
    periods = list(reversed(cycle.periods(days)))[:4]
    return _no_store(render(request, 'hibiscus/user/log.html', {
        **_context(request, config, 'log'),
        'form': form, 'stats': stats, 'periods': periods,
    }))


# --------------------------------------------------------------------------- #
# Stocks
# --------------------------------------------------------------------------- #
@auth.person_required
def stocks(request):
    config = Settings.load()
    person = request.person
    item_form = StockItemForm(request.POST if request.POST.get('what') == 'item' else None)

    if request.method == 'POST' and request.POST.get('what') == 'item' and item_form.is_valid():
        item = item_form.save(commit=False)
        item.person = person
        item.save()
        opening = item_form.cleaned_data.get('opening') or 0
        if opening:
            StockMove.objects.create(item=item, date=config.today, change=opening,
                                     reason=StockMove.Reason.OPENING, note='Set up')
        auth.log('added_item', person=person, detail=item.name)
        messages.success(request, f'{item.name} added.')
        return redirect('hibiscus_me_stocks')

    items = []
    for item in person.items.filter(is_active=True):
        items.append({'item': item, 'in_stock': item.in_stock, 'is_low': item.is_low})
    recent = (StockMove.objects.filter(item__person=person)
              .select_related('item').order_by('-date', '-id')[:12])

    return _no_store(render(request, 'hibiscus/user/stocks.html', {
        **_context(request, config, 'stocks'),
        'items': items,
        'low': [i for i in items if i['is_low']],
        'recent': recent,
        'item_form': item_form if request.POST.get('what') == 'item' else StockItemForm(),
        'move_form': StockMoveForm(today=config.today),
    }))


@auth.person_required
@require_POST
def stock_move(request, pk):
    """The plus and minus buttons: one tap, one movement, no page reload."""
    person = request.person
    config = Settings.load()
    item = person.items.filter(pk=pk, is_active=True).first()
    if not item:
        raise Http404('No such item')

    if request.content_type == 'application/json':
        try:
            change = int(json.loads(request.body or b'{}').get('change', 0))
        except (ValueError, TypeError):
            change = 0
        if change:
            StockMove.objects.create(
                item=item, date=config.today, change=change,
                reason=StockMove.Reason.USED if change < 0 else StockMove.Reason.RESTOCKED)
        return JsonResponse({'in_stock': item.in_stock, 'is_low': item.is_low})

    form = StockMoveForm(request.POST, today=config.today)
    if form.is_valid():
        move = form.save(commit=False)
        move.item = item
        move.save()
        auth.log('stock_move', person=person, detail=f'{item.name} {move.change:+d}')
        messages.success(request, f'{item.name}: {move.change:+d}.')
    else:
        messages.error(request, 'Check the date and the number.')
    return redirect('hibiscus_me_stocks')


@auth.person_required
@require_POST
def stock_item_remove(request, pk):
    person = request.person
    item = person.items.filter(pk=pk).first()
    if not item:
        raise Http404('No such item')
    item.is_active = False
    item.save(update_fields=['is_active'])
    auth.log('removed_item', person=person, detail=item.name)
    messages.success(request, f'{item.name} removed from your list.')
    return redirect('hibiscus_me_stocks')


# --------------------------------------------------------------------------- #
# Settings, reminders, the SyncUp token
# --------------------------------------------------------------------------- #
@auth.person_required
def my_settings(request):
    config = Settings.load()
    person = request.person
    form = MySettingsForm(request.POST or None, initial={
        'name': person.name, 'cycle_hint': person.cycle_hint})
    if request.method == 'POST' and form.is_valid():
        person.name = form.cleaned_data['name']
        person.cycle_hint = form.cleaned_data.get('cycle_hint') or None
        person.save(update_fields=['name', 'cycle_hint'])
        if form.cleaned_data.get('new_password'):
            auth.set_password(person, form.cleaned_data['new_password'])
            messages.success(request, 'Saved, and your password is changed.')
        else:
            messages.success(request, 'Saved.')
        auth.log('changed_own_settings', person=person)
        return redirect('hibiscus_me_settings')

    slots = NotifySlot.objects.filter(is_enabled=True)
    muted = set(person.mutes.values_list('slot_id', flat=True))
    return _no_store(render(request, 'hibiscus/user/settings.html', {
        **_context(request, config, ''),
        'form': form,
        'slots': [{'slot': s, 'muted': s.pk in muted} for s in slots],
        'recent_sent': person.sent.select_related('slot')[:6],
        'has_token': bool(person.push_token),
    }))


@auth.person_required
@require_POST
def toggle_slot(request, pk):
    person = request.person
    slot = NotifySlot.objects.filter(pk=pk).first()
    if not slot:
        raise Http404('No such reminder')
    existing = person.mutes.filter(slot=slot)
    if existing.exists():
        existing.delete()
        messages.success(request, f'{slot.get_key_display()} reminders are back on.')
    else:
        NotifyMute.objects.create(person=person, slot=slot)
        messages.success(request, f'{slot.get_key_display()} reminders are off.')
    auth.log('changed_reminders', person=person, detail=slot.key)
    return redirect('hibiscus_me_settings')


@auth.person_required
@require_POST
def set_theme(request):
    """The theme is the person's, not the diary's, so it is kept once on the member."""
    member = request.member
    try:
        theme = json.loads(request.body or b'{}').get('theme', 'system')
    except (ValueError, TypeError):
        theme = 'system'
    if theme in dict(Person.THEMES):
        type(member).objects.filter(pk=member.pk).update(theme=theme)
        return JsonResponse({'theme': theme})
    return JsonResponse({'theme': member.theme})


@auth.person_required
@require_POST
def save_syncup_token(request):
    """Store `window.SyncUp.token`, which is what makes a daily push possible at all."""
    person = request.person
    try:
        token = json.loads(request.body or b'{}').get('token')
    except (ValueError, TypeError):
        token = ''
    # One token per person, not one per module: opening any of them inside SyncUp now makes
    # all of them reachable. `core.push` owns that rule.
    if core_push.remember_token(person, token):
        auth.log('syncup_token_saved', person=person)
    return HttpResponse(status=204)

def moved_from_me(request, rest=''):
    """Old `/hibiscus/me/…` addresses, kept working.

    The diary's magic links and the chat's sign-in links were handed out with `me/` in them, and
    a link somebody saved in a note has to keep working — so the old shape answers a permanent
    redirect to the new one rather than a 404.
    """
    target = reverse('hibiscus_me_home') + (rest or '')
    if request.META.get('QUERY_STRING'):
        target += '?' + request.META['QUERY_STRING']
    return redirect(target, permanent=True)
