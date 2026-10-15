"""The diary itself — what Divya and Meena see.

A separate route (`/hibiscus/me/`), its own session, its own small stylesheet. Nothing of
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
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import auth, cycle, push
from .forms import (DayForm, DayRangeForm, MySettingsForm, SignInForm, SignUpForm, StockItemForm,
                    StockMoveForm)
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
def sign_up(request):
    config = Settings.load()
    if auth.current_person(request):
        return redirect('hibiscus_me_home')
    if not config.signup_open:
        return _no_store(render(request, 'hibiscus/user/closed.html',
                                {'config': config}, status=403))

    form = SignUpForm(request.POST or None, config=config)
    if request.method == 'POST' and form.is_valid():
        kind = form.cleaned_data.get('kind', 'email')
        person = Person(
            name=form.cleaned_data['name'],
            cycle_hint=form.cleaned_data.get('cycle_hint') or None,
            signed_up_with=kind,
        )
        setattr(person, 'email' if kind == 'email' else 'phone', form.cleaned_data['contact'])
        person.save()
        auth.set_password(person, form.cleaned_data['password'])
        auth.log('signed_up', person=person, detail=person.sign_in_id)
        auth.sign_in(request, person)
        messages.success(request, f'Welcome, {person.name.split(" ")[0]}.')
        return redirect('hibiscus_me_home')
    return _no_store(render(request, 'hibiscus/user/signup.html',
                            {'form': form, 'config': config}))


@never_cache
def sign_in_view(request):
    config = Settings.load()
    if auth.current_person(request):
        return redirect('hibiscus_me_home')
    form = SignInForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        person = auth.find_person(form.cleaned_data['contact'])
        if person and not (person.is_active and not person.is_archived):
            messages.error(request, 'That account is switched off. Ask the admin about it.')
        elif person and auth.check_person_password(person, form.cleaned_data['password']):
            auth.sign_in(request, person)
            auth.log('signed_in', person=person)
            return redirect('hibiscus_me_home')
        else:
            messages.error(request, 'That did not match. Check the email or number, and the password.')
    return _no_store(render(request, 'hibiscus/user/login.html',
                            {'form': form, 'config': config}))


@never_cache
def magic_login(request, key):
    """Sign in by link alone — no password, no expiry.

    The admin hands this out from someone's page. Opening it signs that person in and then
    *redirects*, so the key does not sit in the address bar of the page they end up on and does
    not get shared along with a screenshot of it.

    A key that has been replaced, switched off, or belongs to a suspended account lands on the
    normal sign-in page with a plain explanation. Nothing says whether the key was ever real,
    which is all the lookup resistance a 43-character random key needs.
    """
    person = auth.person_by_login_key(key)
    if not person:
        messages.error(request, 'That link does not work any more. Ask for a new one, '
                                'or sign in with your password.')
        return _no_store(redirect('hibiscus_me_login'))

    auth.sign_in(request, person)
    Person.objects.filter(pk=person.pk).update(
        login_key_used_at=timezone.now(), login_key_uses=F('login_key_uses') + 1)
    auth.log('signed_in_with_link', person=person)
    return _no_store(redirect('hibiscus_me_home'))


def sign_out_view(request):
    auth.sign_out(request)
    return redirect('hibiscus_me_login')


# --------------------------------------------------------------------------- #
# Today
# --------------------------------------------------------------------------- #
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
        'has_token': bool(person.syncup_token),
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
    person = request.person
    try:
        theme = json.loads(request.body or b'{}').get('theme', 'system')
    except (ValueError, TypeError):
        theme = 'system'
    if theme in dict(Person.THEMES):
        person.theme = theme
        person.save(update_fields=['theme'])
    return JsonResponse({'theme': person.theme})


@auth.person_required
@require_POST
def save_syncup_token(request):
    """Store `window.SyncUp.token`, which is what makes a daily push possible at all."""
    person = request.person
    try:
        token = str(json.loads(request.body or b'{}').get('token') or '')[:500]
    except (ValueError, TypeError):
        token = ''
    if token and token != person.syncup_token:
        person.syncup_token = token
        person.save(update_fields=['syncup_token'])
        auth.log('syncup_token_saved', person=person)
    return HttpResponse(status=204)
