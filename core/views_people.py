"""One page for the admin that is about a *person* rather than a module.

Before this, answering "who is this and what do they have here" meant opening the diary's
console and the money module's console and comparing two lists that each thought they owned the
identity. Now there is one row per human, and the module consoles stay where they are for the
things only they know about — cycles, folders, chats.
"""
from django.contrib import messages
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from . import auth as hub
from . import push as core_push
from .models import Member
from .modules import MODULES
from .views import admin_required


def _profiles_of(member):
    """What this person has here, module by module, without asking the modules anything."""
    out = []
    for module in MODULES:
        if not module.is_live:
            continue
        out.append({'module': module, 'profile': getattr(member, module.key, None)})
    return out


def _how_they_sign_in(member):
    ways = []
    if member.password_hash:
        ways.append('Password')
    if member.syncup_id:
        ways.append('SyncUp')
    if member.login_key:
        ways.append('Link')
    return ways or ['Nothing yet']


@admin_required
def people(request):
    search = (request.GET.get('q') or '').strip()
    rows = Member.objects.all()
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(email__icontains=search)
                           | Q(phone__icontains=search))

    shown = []
    for member in rows.order_by('-last_opened_at', 'name')[:300]:
        profiles = _profiles_of(member)
        shown.append({'member': member, 'profiles': profiles,
                      'ways': _how_they_sign_in(member),
                      'count': len([p for p in profiles if p['profile']])})

    unmerged = sum(1 for module in MODULES if module.is_live)
    return render(request, 'core/people.html', {
        'rows': shown, 'search': search,
        'total': Member.objects.count(),
        'with_password': Member.objects.exclude(password_hash='').count(),
        'with_syncup': Member.objects.exclude(syncup_id=None).count(),
        'push_ready': Member.objects.exclude(syncup_token='').count(),
        'modules': [m for m in MODULES if m.is_live],
        'unmerged': _unmerged_counts(),
        'module_count': unmerged,
    })


def _unmerged_counts():
    """Module rows that no Member owns yet — i.e. `merge_identities` has not been run.

    Worth showing rather than hiding: until it is zero, some people still cannot sign in.
    """
    counts = {}
    try:
        from hibiscus.models import Person
        counts['Hibiscus'] = Person.objects.filter(member__isnull=True).count()
    except Exception:                                    # pragma: no cover - module optional
        pass
    try:
        from pocket.models import Account
        counts['Pocket Money'] = Account.objects.filter(member__isnull=True).count()
    except Exception:                                    # pragma: no cover
        pass
    return {name: n for name, n in counts.items() if n}


@admin_required
def person(request, pk):
    member = get_object_or_404(Member, pk=pk)
    link = ''
    if member.login_key:
        from django.urls import reverse
        link = request.build_absolute_uri(reverse('member_magic', args=[member.login_key]))
    return render(request, 'core/person.html', {
        'member': member, 'profiles': _profiles_of(member), 'link': link,
        'ways': _how_they_sign_in(member),
    })


@require_POST
@admin_required
def person_action(request, pk, what):
    member = get_object_or_404(Member, pk=pk)
    if what == 'link':
        hub.new_login_key(member)
        messages.success(request, 'A one-hour sign-in link is on their page. Hand it over '
                                  'yourself — this server sends no mail.')
    elif what == 'archive':
        member.is_archived = not member.is_archived
        member.save(update_fields=['is_archived'])
        messages.success(request, f'{member.name} is '
                                  f'{"archived" if member.is_archived else "back"}. '
                                  'What each module lets them do is still a separate switch.')
    elif what == 'unlink-syncup':
        member.syncup_id = None
        member.save(update_fields=['syncup_id'])
        messages.success(request, 'SyncUp sign-in disconnected for this person.')
    elif what == 'push':
        if not member.syncup_token:
            messages.warning(request, f'{member.name} has no device token yet. They have to '
                                      'open one of these inside the SyncUp app.')
        else:
            delivered, error = core_push.send(member.syncup_token, 'SyncHub',
                                              'A test from the console.', '', timeout=12)
            if delivered:
                messages.success(request, f'Delivered to {delivered} device.')
            else:
                messages.error(request, f'Nothing went out — {error or "SyncUp said zero"}.')
    return redirect('person', pk=member.pk)
