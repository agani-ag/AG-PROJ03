"""The admin side of Hibiscus — inside SyncHub, behind SyncHub's admin login.

Every view is wrapped in `core.views.admin_required` (an active superuser, or it signs you out),
and every view that reads or changes someone's diary writes an audit row. The admin can see
everything, notes included, because that is how support and data entry work here; the trail is
the other half of that bargain.
"""
from django.contrib import messages
from django.db.models import Count, Max
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.views import admin_required

from . import auth, cycle
from .forms import PersonForm, SettingsForm
from .models import Audit, Person, Settings


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


@admin_required
def person_activity(request, pk):
    person = get_object_or_404(Person, pk=pk)
    return render(request, 'hibiscus/admin/person_activity.html',
                  {'person': person, 'audit': person.audit.all()[:200],
                   'config': Settings.load()})


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
