"""The admin side of Hibiscus — inside SyncHub, behind SyncHub's admin login.

Every view is wrapped in `core.views.admin_required` (an active superuser, or it signs you out),
and every view that reads or changes someone's diary writes an audit row. The admin can see
everything, notes included, because that is how support and data entry work here; the trail is
the other half of that bargain.
"""
import csv
from datetime import date, datetime, timedelta

from django.contrib import messages
from django.db import transaction
from django.db.models import Count, Max, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.views import admin_required

from . import auth, cycle, defaults, push
from .forms import (DayForm, DayRangeForm, PersonForm, SettingsForm, StockItemEditForm,
                    StockItemForm, StockMoveForm)
from .models import (Audit, DaySymptom, Flow, NotifyLine, NotifySent, NotifySlot, Person,
                     PeriodDay, Settings, StockItem, StockMove, Symptom)


def _magic_url(request, person):
    """The person's magic link, spelled with the host this page was reached by.

    Built per request rather than stored: the same database answers to localhost in development
    and to the real domain on the server, and a stored link would be wrong on one of them.
    """
    return request.build_absolute_uri(person.login_path) if person.login_key else ''


def _tab_counts(person):
    """The two numbers on the tab row, for every page about a person.

    The row itself is one include (`admin/_tabs.html`) because keeping four copies of it in sync
    failed in the obvious way: the Periods page quietly lost its Stocks tab.
    """
    return {'days': person.days.count(), 'items': person.items.count()}


def _person_stats(person, config):
    """The three numbers and the next expected date for one person."""
    days = list(person.days.values_list('date', 'flow'))
    return cycle.stats(days, today=config.today,
                       default_cycle=config.default_cycle,
                       default_period=config.default_period,
                       cycle_hint=person.cycle_hint)


@admin_required
def people(request):
    """The module's landing page: who is in here, and who needs attention."""
    config = Settings.load()
    show_archived = request.GET.get('archived') == '1'
    rows = (Person.objects.filter(is_archived=show_archived)
            .annotate(day_count=Count('days', distinct=True),
                      last_day=Max('days__date')))

    people_rows = []
    for person in rows:
        s = _person_stats(person, config)
        people_rows.append({
            'person': person,
            'stats': s,
            'day_count': person.day_count,
            'last_day': person.last_day,
            'low_items': sum(1 for i in person.items.filter(is_active=True) if i.is_low),
        })
    people_rows.sort(key=lambda r: (r['last_day'] is None, -(r['last_day'].toordinal() if r['last_day'] else 0)))

    return render(request, 'hibiscus/admin/people.html', {
        'rows': people_rows,
        'config': config,
        'show_archived': show_archived,
        'archived_count': Person.objects.filter(is_archived=True).count(),
        'active_count': Person.objects.filter(is_archived=False).count(),
        'day_total': sum(r['day_count'] for r in people_rows),
        'low_total': sum(r['low_items'] for r in people_rows),
    })


@admin_required
def person_detail(request, pk):
    """One person: their account, their numbers, and what has been done to their diary.

    The period and stock tabs are built in the next steps; this page is the account tab.
    """
    person = get_object_or_404(Person, pk=pk)
    config = Settings.load()
    auth.log('viewed_person', person=person, actor=request.user)
    periods = cycle.periods(list(person.days.values_list('date', 'flow')))
    return render(request, 'hibiscus/admin/person.html', {
        'person': person,
        'config': config,
        'magic_url': _magic_url(request, person),
        'tab_counts': _tab_counts(person),
        'stats': _person_stats(person, config),
        'periods': list(reversed(periods))[:6],
        'day_count': person.days.count(),
        'items': person.items.filter(is_active=True),
        'audit': person.audit.all()[:15],
    })


@admin_required
def person_add(request):
    """Create someone and show their password once."""
    form = PersonForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        person = form.save(commit=False)
        person.signed_up_with = 'admin'
        person.created_by = request.user
        raw = auth.generate_password()
        person.password_hash = ''
        person.save()
        auth.set_password(person, raw)
        auth.log('created_person', person=person, actor=request.user,
                 detail=person.sign_in_id)
        messages.success(request, f'{person.name} added.')
        return render(request, 'hibiscus/admin/password_once.html',
                      {'person': person, 'password': raw, 'config': Settings.load(),
                       'heading': 'Account created'})
    return render(request, 'hibiscus/admin/person_form.html',
                  {'form': form, 'config': Settings.load()})


@admin_required
def person_edit(request, pk):
    person = get_object_or_404(Person, pk=pk)
    form = PersonForm(request.POST or None, instance=person)
    if request.method == 'POST' and form.is_valid():
        form.save()
        auth.log('edited_person', person=person, actor=request.user)
        messages.success(request, 'Details saved.')
        return redirect('hibiscus_person', pk=person.pk)
    return render(request, 'hibiscus/admin/person_form.html',
                  {'form': form, 'person': person, 'config': Settings.load()})


@admin_required
@require_POST
def person_password(request, pk):
    """Set a new password. There is no "send it to them" — it is read off the screen once."""
    person = get_object_or_404(Person, pk=pk)
    raw = auth.generate_password()
    auth.set_password(person, raw)
    auth.log('reset_password', person=person, actor=request.user)
    return render(request, 'hibiscus/admin/password_once.html',
                  {'person': person, 'password': raw, 'config': Settings.load(),
                   'heading': 'New password'})


@admin_required
@require_POST
def person_link(request, pk, what):
    """Make, replace or switch off someone's magic link.

    `make` also covers "replace": one key per person, so writing a new one retires the old link
    immediately. That is the whole revocation story for links that never expire, which is why the
    button says so on the page.
    """
    person = get_object_or_404(Person, pk=pk)
    if what == 'make':
        had_one = person.has_login_link
        auth.new_login_key(person)
        auth.log('made_login_link', person=person, actor=request.user,
                 detail='replaced the old link' if had_one else 'first link')
        messages.success(request, 'Link ready — copy it below.' if not had_one else
                         'New link ready. The old one has stopped working.')
    elif what == 'off':
        auth.clear_login_key(person)
        auth.log('removed_login_link', person=person, actor=request.user)
        messages.success(request, 'Link switched off. Only the password works now.')
    else:
        raise Http404
    return redirect('hibiscus_person', pk=person.pk)


@admin_required
@require_POST
def person_state(request, pk, what):
    """Suspend, resume, archive or restore someone."""
    person = get_object_or_404(Person, pk=pk)
    actions = {
        'suspend': ('is_active', False, 'suspended', f'{person.name} can no longer sign in.'),
        'resume': ('is_active', True, 'resumed', f'{person.name} can sign in again.'),
        'archive': ('is_archived', True, 'archived', f'{person.name} moved to archived.'),
        'restore': ('is_archived', False, 'restored', f'{person.name} is back in the list.'),
    }
    if what not in actions:
        raise Http404('No such action')
    field, value, action, note = actions[what]
    setattr(person, field, value)
    person.save(update_fields=[field])
    auth.log(action, person=person, actor=request.user)
    messages.success(request, note)
    return redirect('hibiscus_person', pk=person.pk)


@admin_required
def person_delete(request, pk):
    """Delete someone and everything of theirs. Typing the name is the confirmation."""
    person = get_object_or_404(Person, pk=pk)
    counts = {
        'days': person.days.count(),
        'items': person.items.count(),
        'moves': sum(i.moves.count() for i in person.items.all()),
    }
    if request.method == 'POST':
        if request.POST.get('confirm', '').strip().lower() != person.name.strip().lower():
            messages.error(request, 'That name did not match, so nothing was deleted.')
        else:
            name = person.name
            person.delete()
            # The person row is gone, so this one is kept against no person on purpose.
            auth.log('deleted_person', actor=request.user, detail=f'{name} · {counts}')
            messages.success(request, f'{name} and all their data were deleted.')
            return redirect('hibiscus_people')
    return render(request, 'hibiscus/admin/person_delete.html',
                  {'person': person, 'counts': counts, 'config': Settings.load()})


def _period_rows(person, config):
    """Each period as the table wants it: the run, its days with their flow, and the gap before."""
    days = {d: {'flow': f, 'note': n} for d, f, n in
            person.days.values_list('date', 'flow', 'note')}
    symptoms = {}
    for day_date, symptom in DaySymptom.objects.filter(day__person=person).values_list(
            'day__date', 'symptom'):
        symptoms.setdefault(day_date, []).append(symptom)

    found = cycle.periods(list((d, v['flow']) for d, v in days.items()))
    labels = dict(Symptom.choices)
    rows = []
    for index, period in enumerate(found):
        span = [period.shown_from + timedelta(days=i)
                for i in range((period.shown_to - period.shown_from).days + 1)]
        row_days = [{
            'date': day,
            'logged': day in days,
            'flow': days.get(day, {}).get('flow'),
            'flow_label': (Flow(days[day]['flow']).label if day in days else 'Not logged'),
            'note': days.get(day, {}).get('note', ''),
            'counts': day in days and Flow.counts_as_period(days[day]['flow']),
        } for day in span]
        used = sorted({s for day in span for s in symptoms.get(day, [])})
        gap = ((period.start - found[index - 1].start).days if index else None)
        rows.append({
            'period': period,
            'days': row_days,
            'symptoms': [labels[s] for s in used],
            'gap': gap,
            'gap_counted': gap is not None and cycle.MIN_GAP <= gap < cycle.MAX_GAP,
            'has_lead_spotting': period.shown_from != period.start,
            'has_trail_spotting': period.shown_to != period.end,
            'bridged': [d['date'] for d in row_days if not d['logged']],
        })
    return list(reversed(rows))


@admin_required
def person_periods(request, pk):
    """The periods tab: a range form above the table, because that is the order you type in."""
    person = get_object_or_404(Person, pk=pk)
    config = Settings.load()
    form = DayRangeForm(request.POST or None, today=config.today)

    if request.method == 'POST' and form.is_valid():
        dates = form.dates()
        flow = form.cleaned_data['flow']
        note = form.cleaned_data['note']
        picked = form.cleaned_data['symptoms']
        with transaction.atomic():
            for day in dates:
                row, created = PeriodDay.objects.update_or_create(
                    person=person, date=day, defaults={'flow': flow, 'note': note},
                )
                if created:
                    # Only a new row records who typed it; an edit keeps the original author.
                    row.created_by = request.user
                    row.save(update_fields=['created_by'])
                row.symptoms.all().delete()
                DaySymptom.objects.bulk_create(
                    [DaySymptom(day=row, symptom=s) for s in picked])
        auth.log('added_days', person=person, actor=request.user,
                 detail=f'{dates[0]} to {dates[-1]} · {Flow(flow).label}')
        messages.success(request, f'Saved {len(dates)} day{"" if len(dates) == 1 else "s"} '
                                  f'for {person.name}.')
        return redirect('hibiscus_person_periods', pk=person.pk)

    return render(request, 'hibiscus/admin/person_periods.html', {
        'person': person,
        'config': config,
        'form': form,
        'rows': _period_rows(person, config),
        'stats': _person_stats(person, config),
        'day_count': person.days.count(),
        'tab_counts': _tab_counts(person),
        'flow_levels': Flow.choices,
    })


@admin_required
def day_edit(request, pk, on):
    """One day on its own — change the flow, the symptoms or the note, or remove the day."""
    person = get_object_or_404(Person, pk=pk)
    config = Settings.load()
    try:
        day_date = date.fromisoformat(on)
    except ValueError:
        raise Http404('Not a date')
    row = PeriodDay.objects.filter(person=person, date=day_date).first()
    if not row:
        raise Http404('Nothing logged on that day')

    form = DayForm(request.POST or None, initial={
        'flow': row.flow, 'note': row.note,
        'symptoms': list(row.symptoms.values_list('symptom', flat=True)),
    })
    if request.method == 'POST' and form.is_valid():
        row.flow = form.cleaned_data['flow']
        row.note = form.cleaned_data['note']
        row.save(update_fields=['flow', 'note', 'updated_at'])
        row.symptoms.all().delete()
        DaySymptom.objects.bulk_create(
            [DaySymptom(day=row, symptom=s) for s in form.cleaned_data['symptoms']])
        auth.log('edited_day', person=person, actor=request.user,
                 detail=f'{day_date} · {Flow(row.flow).label}')
        messages.success(request, f'{day_date:%d %b %Y} saved.')
        return redirect('hibiscus_person_periods', pk=person.pk)

    return render(request, 'hibiscus/admin/day_form.html', {
        'person': person, 'config': config, 'form': form, 'day': row,
    })


@admin_required
@require_POST
def day_delete(request, pk, on):
    """Remove one day. Days on either side keep their own rows, so a period can be trimmed."""
    person = get_object_or_404(Person, pk=pk)
    try:
        day_date = date.fromisoformat(on)
    except ValueError:
        raise Http404('Not a date')
    deleted, _ = PeriodDay.objects.filter(person=person, date=day_date).delete()
    if deleted:
        auth.log('deleted_day', person=person, actor=request.user, detail=str(day_date))
        messages.success(request, f'{day_date:%d %b %Y} removed.')
    return redirect('hibiscus_person_periods', pk=person.pk)


@admin_required
@require_POST
def period_delete(request, pk, start):
    """Remove a whole period — every day in that run, including its spotting days."""
    person = get_object_or_404(Person, pk=pk)
    try:
        start_date = date.fromisoformat(start)
    except ValueError:
        raise Http404('Not a date')

    days = list(person.days.values_list('date', 'flow'))
    run = next((r for r in cycle.periods(days) if r.start == start_date), None)
    if not run:
        raise Http404('No period starts on that day')
    deleted, _ = PeriodDay.objects.filter(person=person,
                                          date__gte=run.shown_from,
                                          date__lte=run.shown_to).delete()
    auth.log('deleted_period', person=person, actor=request.user,
             detail=f'{run.shown_from} to {run.shown_to} · {deleted} row(s)')
    messages.success(request, f'Removed the period of {run.start:%d %b} '
                              f'({deleted} day{"" if deleted == 1 else "s"}).')
    return redirect('hibiscus_person_periods', pk=person.pk)


@admin_required
def person_activity(request, pk):
    person = get_object_or_404(Person, pk=pk)
    return render(request, 'hibiscus/admin/person_activity.html',
                  {'person': person, 'audit': person.audit.all()[:200],
                   'config': Settings.load(), 'tab_counts': _tab_counts(person)})


@admin_required
def person_stocks(request, pk):
    """What they keep at home, and the ledger the count comes from."""
    person = get_object_or_404(Person, pk=pk)
    config = Settings.load()
    item_form = StockItemForm(request.POST if request.POST.get('what') == 'item' else None)
    move_form = StockMoveForm(request.POST if request.POST.get('what') == 'move' else None,
                              today=config.today)

    if request.method == 'POST' and request.POST.get('what') == 'item' and item_form.is_valid():
        item = item_form.save(commit=False)
        item.person = person
        item.save()
        if item_form.cleaned_data.get('opening'):
            StockMove.objects.create(item=item, date=config.today,
                                     change=item_form.cleaned_data['opening'],
                                     reason=StockMove.Reason.OPENING, note='Set up',
                                     created_by=request.user)
        auth.log('added_item', person=person, actor=request.user, detail=item.name)
        messages.success(request, f'{item.name} added.')
        return redirect('hibiscus_person_stocks', pk=person.pk)

    if request.method == 'POST' and request.POST.get('what') == 'move' and move_form.is_valid():
        item = person.items.filter(pk=request.POST.get('item')).first()
        if not item:
            raise Http404('No such item')
        move = move_form.save(commit=False)
        move.item = item
        move.created_by = request.user
        move.save()
        auth.log('stock_move', person=person, actor=request.user,
                 detail=f'{item.name} {move.change:+d} on {move.date}')
        messages.success(request, f'{item.name}: {move.change:+d} on {move.date:%d %b}.')
        return redirect('hibiscus_person_stocks', pk=person.pk)

    items = [{'item': i, 'in_stock': i.in_stock, 'is_low': i.is_low,
              'used_30': -(i.moves.filter(date__gte=config.today - timedelta(days=30),
                                          change__lt=0)
                           .aggregate(n=Sum('change'))['n'] or 0),
              'last': i.moves.first()}
             for i in person.items.all()]
    return render(request, 'hibiscus/admin/person_stocks.html', {
        'person': person, 'config': config,
        'items': items,
        'low': [i for i in items if i['is_low'] and i['item'].is_active],
        'moves': StockMove.objects.filter(item__person=person).select_related('item')[:30],
        'item_form': item_form if request.POST.get('what') == 'item' else StockItemForm(),
        'move_form': move_form if request.POST.get('what') == 'move' else StockMoveForm(today=config.today),
        'day_count': person.days.count(),
        'tab_counts': _tab_counts(person),
    })


@admin_required
def stock_item_edit(request, pk, item_pk):
    """Rename an item, or change the level that makes it count as low.

    The low level is the one thing on this page that changes what gets *sent*: an item at or
    below it puts a line in her reminders, so it belongs with the admin as well as with her.
    """
    person = get_object_or_404(Person, pk=pk)
    item = get_object_or_404(StockItem, pk=item_pk, person=person)
    was = (item.name, item.low_at)                 # the form mutates `item` while validating
    form = StockItemEditForm(request.POST or None, instance=item)
    if request.method == 'POST' and form.is_valid():
        form.save()
        changes = []
        if was[0] != item.name:
            changes.append(f'{was[0]} → {item.name}')
        if was[1] != item.low_at:
            changes.append(f'low at {was[1]} → {item.low_at}')
        auth.log('edited_item', person=person, actor=request.user,
                 detail=' · '.join(changes) or f'{item.name} unchanged')
        messages.success(request, f'{item.name} saved.')
        return redirect('hibiscus_person_stocks', pk=person.pk)
    return render(request, 'hibiscus/admin/item_form.html', {
        'person': person, 'item': item, 'form': form, 'config': Settings.load(),
        'in_stock': item.in_stock,
    })


@admin_required
@require_POST
def stock_item_toggle(request, pk, item_pk):
    person = get_object_or_404(Person, pk=pk)
    item = get_object_or_404(StockItem, pk=item_pk, person=person)
    item.is_active = not item.is_active
    item.save(update_fields=['is_active'])
    auth.log('item_toggled', person=person, actor=request.user,
             detail=f'{item.name} {"on" if item.is_active else "off"}')
    return redirect('hibiscus_person_stocks', pk=person.pk)


@admin_required
def person_export(request, pk):
    """One CSV with everything of theirs.

    A normal https GET with Content-Disposition, never a file built in the page — that is what
    crashes the SyncUp app, and it is the rule for every download here.
    """
    person = get_object_or_404(Person, pk=pk)
    response = HttpResponse(content_type='text/csv; charset=utf-8')
    stamp = timezone.localtime(timezone.now(), Settings.load().tzinfo)
    name = f'hibiscus-{person.name.lower().replace(" ", "-")}-{stamp:%Y%m%d}.csv'
    response['Content-Disposition'] = f'attachment; filename="{name}"'

    writer = csv.writer(response)
    writer.writerow(['Hibiscus export', person.name, person.sign_in_id,
                     stamp.strftime('%Y-%m-%d %H:%M')])
    writer.writerow([])
    writer.writerow(['Period days'])
    writer.writerow(['date', 'flow', 'symptoms', 'note', 'added by'])
    symptoms = {}
    for day_date, symptom in DaySymptom.objects.filter(day__person=person).values_list(
            'day__date', 'symptom'):
        symptoms.setdefault(day_date, []).append(symptom)
    for day in person.days.order_by('date'):
        writer.writerow([day.date, Flow(day.flow).label,
                         ', '.join(symptoms.get(day.date, [])), day.note,
                         day.created_by.username if day.created_by else person.name])

    writer.writerow([])
    writer.writerow(['Stock movements'])
    writer.writerow(['date', 'item', 'change', 'reason', 'note'])
    for move in StockMove.objects.filter(item__person=person).order_by('date'):
        writer.writerow([move.date, move.item.name, move.change,
                         move.get_reason_display(), move.note])

    auth.log('exported', person=person, actor=request.user)
    return response


@admin_required
def notifications_page(request):
    """How many messages a day, at what times, and the words each slot uses."""
    config = Settings.load()
    defaults.ensure_slots()
    slots = NotifySlot.objects.prefetch_related('lines').all()

    if request.method == 'POST':
        changed = 0
        for slot in slots:
            prefix = f'slot-{slot.pk}-'
            at = request.POST.get(prefix + 'at')
            mode = request.POST.get(prefix + 'mode')
            enabled = request.POST.get(prefix + 'enabled') == 'on'
            if at:
                try:
                    slot.at = datetime.strptime(at, '%H:%M').time()
                except ValueError:
                    messages.error(request, f'{slot.get_key_display()}: that is not a time.')
                    continue
            if mode in dict(NotifySlot.Mode.choices):
                slot.mode = mode
            slot.is_enabled = enabled
            slot.save(update_fields=['at', 'mode', 'is_enabled'])
            changed += 1
        auth.log('changed_notifications', actor=request.user)
        messages.success(request, f'Saved {changed} time{"" if changed == 1 else "s"} of day.')
        return redirect('hibiscus_notifications')

    enabled = [s for s in slots if s.is_enabled]
    return render(request, 'hibiscus/admin/notifications.html', {
        'config': config,
        'slots': slots,
        'enabled_count': len(enabled),
        'situations': NotifyLine.Situation.choices,
        'modes': NotifySlot.Mode.choices,
        'recent': NotifySent.objects.select_related('person', 'slot')[:20],
        'people_count': Person.objects.filter(is_active=True, is_archived=False).count(),
        'with_token': Person.objects.filter(is_active=True).exclude(syncup_token='').count(),
    })


@admin_required
@require_POST
def notifications_preview(request):
    """Dry run: what would go out right now, to whom, without sending anything."""
    config = Settings.load()
    now_local = timezone.localtime(timezone.now(), config.tzinfo)
    preview = []
    for slot in NotifySlot.objects.filter(is_enabled=True):
        result = push.run_slot(slot, config, now_local=now_local, dry_run=True)
        for row in result.get('preview', []):
            preview.append({'slot': slot, **row})
        if not result.get('preview'):
            preview.append({'slot': slot, 'person': '—', 'situation': 'nothing to say',
                            'body': f"{result['quiet']} quiet, {result['muted']} muted"})
    request.session['hibiscus_preview'] = [
        {'slot': p['slot'].get_key_display(), 'person': p['person'],
         'situation': str(p['situation']), 'body': p['body']} for p in preview]
    messages.success(request, 'Preview below — nothing was sent.')
    return redirect('hibiscus_notifications')


@admin_required
def settings_page(request):
    config = Settings.load()
    form = SettingsForm(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        config = form.save(commit=False)
        config.updated_by = request.user
        config.save()
        auth.log('changed_settings', actor=request.user)
        messages.success(request, 'Settings saved.')
        return redirect('hibiscus_settings')
    return render(request, 'hibiscus/admin/settings.html', {
        'form': form, 'config': config,
        'now_local': timezone.localtime(timezone.now(), config.tzinfo),
        'recent': Audit.objects.filter(action='changed_settings')[:5],
    })
