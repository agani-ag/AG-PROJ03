"""The console, inside SyncHub. One admin sign-in, and it can read everything.

Two questions this side answers: *what is on this server*, and *please fix my figure*. Both
halves are shown separately all the way down — a total of a Tracker and a Ledger would be a
number that does not mean anything.

The one thing here that exists nowhere else is a hard delete of an entry, the way J6's console
can hard-delete a message. Somebody has to be able to remove a figure that should never have
been typed.
"""
from datetime import timedelta
from decimal import Decimal

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.db.models import Count, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.models import HubSettings

from . import push, service
from .forms import SettingsForm
from .models import (Flow, Folder, FolderShare, Kind, LedgerEntry, Person, Settings,
                     TrackerEntry)
from .models import Account

PAGE = 60


@staff_member_required
def accounts(request):
    config = Settings.load()
    search = (request.GET.get('q') or '').strip()
    rows = Account.objects.all()
    if search:
        rows = rows.filter(Q(name__icontains=search) | Q(email__icontains=search)
                           | Q(phone__icontains=search))

    month = config.today().replace(day=1)
    tracked = TrackerEntry.objects.filter(voided_at__isnull=True, date__gte=month)
    totals = {row['flow']: row['total'] for row
              in tracked.values('flow').annotate(total=Sum('amount'))}

    outstanding = overdue = Decimal('0.00')
    open_lines = 0
    for folder in Folder.objects.all():
        folder_totals = service.folder_totals(folder)
        outstanding += folder_totals['owed_to_you']
        open_lines += folder_totals['open']
    today = config.today()
    for entry in LedgerEntry.objects.filter(voided_at__isnull=True, kind=Kind.LENT,
                                            due_on__lt=today).select_related('person'):
        balance = service.person_balance(entry.person)
        if balance > 0:
            overdue += min(balance, entry.amount)

    shown = []
    for account in rows.order_by('-last_opened_at', 'name')[:200]:
        position = service.account_position(account)
        shown.append({
            'row': account,
            'position': position,
            'month_in': tracked.filter(account=account, flow=Flow.IN).aggregate(
                t=Sum('amount'))['t'] or Decimal('0.00'),
            'month_out': tracked.filter(account=account, flow=Flow.OUT).aggregate(
                t=Sum('amount'))['t'] or Decimal('0.00'),
            'owned': Folder.objects.filter(owner=account).count(),
            'shared_in': FolderShare.objects.filter(account=account,
                                                    accepted_at__isnull=False).count(),
            'links': Person.objects.filter(linked_account=account,
                                           linked_at__isnull=False).count(),
            'overdue': any(e['late'] > 0 for e in service.due_entries(account, config)),
        })

    return render(request, 'pocket/admin/accounts.html', {
        'config': config, 'rows': shown, 'search': search,
        'total': Account.objects.count(),
        'folders': Folder.objects.count(),
        'links': Person.objects.filter(linked_at__isnull=False).count(),
        'invited': Person.objects.filter(link_code__isnull=False,
                                         linked_at__isnull=True).count(),
        'month_in': totals.get(Flow.IN) or Decimal('0.00'),
        'month_out': totals.get(Flow.OUT) or Decimal('0.00'),
        'outstanding': outstanding, 'overdue': overdue, 'open_lines': open_lines,
        'push_ready': Account.objects.exclude(syncup_token='').count(),
    })


@staff_member_required
def account(request, pk):
    config = Settings.load()
    account = get_object_or_404(Account, pk=pk)
    today = config.today()
    month = service.tracker_month(account, today.year, today.month, config)

    folders = []
    for folder in Folder.objects.filter(owner=account):
        folders.append({'folder': folder, 'role': 'Owner',
                        'totals': service.folder_totals(folder),
                        'entries': folder.entries.count(),
                        'linked': folder.people.filter(linked_at__isnull=False)
                                        .select_related('linked_account'),
                        'invited': folder.people.filter(link_code__isnull=False,
                                                        linked_at__isnull=True),
                        'shares': folder.shares.select_related('account')
                                        .filter(account__isnull=False)})
    for share in FolderShare.objects.filter(account=account, accepted_at__isnull=False
                                            ).select_related('folder', 'folder__owner'):
        folders.append({'folder': share.folder, 'role': 'Shared in',
                        'totals': service.folder_totals(share.folder),
                        'entries': share.folder.entries.count(),
                        'linked': [], 'invited': [], 'shares': []})

    return render(request, 'pocket/admin/account.html', {
        'config': config, 'account': account, 'month': month,
        'position': service.account_position(account),
        'folders': folders,
        'lines': Person.objects.filter(linked_account=account, linked_at__isnull=False)
                       .select_related('folder', 'folder__owner'),
        'link': _magic_link(request, account),
        'recent': service.tracker_entries(account, limit=10)['rows'],
        'accounts_for_handover': Account.objects.exclude(pk=account.pk).filter(
            is_active=True, is_archived=False)[:50],
    })


def _magic_link(request, account):
    if not account.login_key:
        return ''
    from django.urls import reverse
    return request.build_absolute_uri(reverse('pocket_magic', args=[account.login_key]))


@staff_member_required
def folder(request, pk):
    config = Settings.load()
    folder = get_object_or_404(Folder.objects.select_related('owner'), pk=pk)
    before = request.GET.get('before')
    page = service.folder_entries(folder, limit=PAGE, before=before)

    balances = service.folder_balances(folder)
    people = []
    for person in folder.people.select_related('linked_account'):
        people.append({'person': person, 'balance': balances.get(person.pk, Decimal('0.00')),
                       'entries': person.entries.count()})

    # A running balance per row, oldest first, so "it was right until Tuesday" can be checked.
    total = Decimal('0.00')
    for entry in sorted(page['rows'], key=lambda e: (e.date, e.pk)):
        if not entry.is_void:
            total += entry.signed
        entry.running = total

    return render(request, 'pocket/admin/folder.html', {
        'config': config, 'folder': folder, 'page': page, 'people': people,
        'totals': service.folder_totals(folder),
        'is_window': bool(before), 'count': folder.entries.count(),
        'logs': folder.logs.select_related('account')[:20],
    })


@staff_member_required
def settings_page(request):
    config = Settings.load()
    form = SettingsForm(request.POST or None, instance=config)
    if request.method == 'POST' and form.is_valid():
        saved = form.save(commit=False)
        saved.updated_by = request.user
        saved.save()
        messages.success(request, 'Settings saved.')
        return redirect('pocket_settings')
    return render(request, 'pocket/admin/settings.html', {
        'config': config, 'form': form, 'hub': HubSettings.load(),
        'accounts': Account.objects.count(),
        'entries': TrackerEntry.objects.count() + LedgerEntry.objects.count(),
    })


@require_POST
@staff_member_required
def account_state(request, pk, what):
    account = get_object_or_404(Account, pk=pk)
    if what == 'archive':
        account.is_archived = not account.is_archived
        account.save(update_fields=['is_archived'])
        messages.success(request, f'{account.name} is '
                                  f'{"archived" if account.is_archived else "back"}. '
                                  'Shared folders and linked lines keep working either way.')
    elif what == 'link':
        from . import auth as pocket_auth
        pocket_auth.new_login_key(account)
        messages.success(request, 'A one-hour sign-in link is on their page. Hand it over '
                                  'yourself — this server sends no mail.')
    return redirect('pocket_admin_account', pk=account.pk)


@require_POST
@staff_member_required
def account_test_push(request, pk):
    account = get_object_or_404(Account, pk=pk)
    if not account.syncup_token:
        messages.warning(request, f'{account.name} has no device token yet. They have to open '
                                  'Pocket Money once inside the SyncUp app.')
        return redirect('pocket_admin_account', pk=account.pk)
    delivered, error = push.send_now(account, 'Pocket Money', 'A test from the console.',
                                     timeout=push.TEST_TIMEOUT)
    if delivered:
        messages.success(request, f'Delivered to {delivered} device.')
    else:
        messages.error(request, f'Nothing went out — {error or "SyncUp said zero devices"}.')
    return redirect('pocket_admin_account', pk=account.pk)


@require_POST
@staff_member_required
def account_delete(request, pk):
    """Deleting asks what happens to the folders first — that is the form's job, not a flag."""
    account = get_object_or_404(Account, pk=pk)
    owned = Folder.objects.filter(owner=account)
    hand_to = request.POST.get('hand_to')
    if hand_to:
        new_owner = Account.objects.filter(pk=hand_to).first()
        if not new_owner:
            messages.error(request, 'That account is gone.')
            return redirect('pocket_admin_account', pk=account.pk)
        owned.update(owner=new_owner)
        messages.info(request, f'{owned.count()} folder(s) now belong to {new_owner.name}.')
    name = account.name
    account.delete()
    messages.success(request, f'{name} is gone, with their Tracker.')
    return redirect('pocket_accounts')


@require_POST
@staff_member_required
def entry_delete(request, pk, entry_pk):
    """A hard delete, console-only. The one action in the module that leaves no row behind."""
    folder = get_object_or_404(Folder, pk=pk)
    entry = get_object_or_404(LedgerEntry, pk=entry_pk, folder=folder)
    amount = entry.amount
    entry.delete()
    service.log('ledger', 'Hard deleted by the admin', folder=folder, before=amount,
                detail=f'{request.user.get_username()}')
    messages.success(request, 'Entry removed for good.')
    return redirect('pocket_admin_folder', pk=folder.pk)
