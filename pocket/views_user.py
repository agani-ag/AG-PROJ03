"""What people see at /pocket/ — four tabs, one session, no console chrome.

Server-rendered pages with two small JSON endpoints (the SyncUp token, and the QR scan). Every
page sends `Cache-Control: no-store`, because a cached balance is a wrong balance.

Nothing in here decides who may see what. That is `service.access()`, once, so there is one
place to read and one place to test.
"""
import json
from datetime import date, timedelta

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core import auth as hub
from core import push as core_push

from . import auth, push, service
from .models import (Account, Category, Flow, Folder, LedgerEntry, Person, Settings,
                     TrackerEntry, pretty_code, validate_name, validate_upi)


def _no_store(response):
    """No caching, and a referrer policy that does not break form posts.

    `no-referrer` makes browsers send Origin: null, which Django's CSRF check refuses — the
    mistake that broke every form in Hibiscus once.
    """
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'same-origin'
    return response


def _page(request, template, context, status=200):
    config = Settings.load()
    account = getattr(request, 'account', None) or auth.current(request)
    base = {'config': config, 'account': account, 'money': config.currency_symbol,
            'member': getattr(request, 'member', None) or hub.current(request)}
    if account:
        base['pending'] = (service.pending_person_invites(account).count()
                           + service.suggestions_for(account).count())
    base.update(context)
    return _no_store(render(request, template, base, status=status))


def _month(request, config):
    """`?m=2026-10`, or this month. One place, because four screens take it."""
    raw = (request.GET.get('m') or '').strip()
    today = config.today()
    try:
        year, month = (int(part) for part in raw.split('-', 1))
        if 2000 <= year <= 2999 and 1 <= month <= 12:
            return year, month
    except (ValueError, TypeError):
        pass
    return today.year, today.month


def _parse_date(value, config):
    try:
        return date.fromisoformat((value or '').strip())
    except ValueError:
        return config.today()


def _back(request, fallback='pocket_tracker'):
    nxt = request.POST.get('next') or request.GET.get('next') or ''
    return redirect(nxt) if nxt.startswith('/pocket/') else redirect(fallback)


# --------------------------------------------------------------------------- #
# Getting in
# --------------------------------------------------------------------------- #
@never_cache
def home(request):
    """`/pocket/` — wherever this person actually belongs."""
    member = hub.current(request)
    if not member:
        return redirect(f'{reverse("member_login")}?next={reverse("pocket_tracker")}')
    return redirect('pocket_tracker' if auth.profile_for(member) else 'pocket_join')


@never_cache
def join(request):
    """Starting Pocket Money, for somebody the hub already knows.

    There is nothing to ask. The old sign-up form wanted a name, an email and a password, and
    the hub has all three — so this is a sentence and a button.
    """
    member = hub.current(request)
    if not member:
        return redirect(f'{reverse("member_login")}?next={reverse("pocket_join")}')
    if auth.profile_for(member):
        return redirect('pocket_tracker')
    config = Settings.load()
    error = ''
    if request.method == 'POST':
        if not config.signup_open:
            error = 'Pocket Money is closed to new people on this server.'
        elif config.invite_code and request.POST.get('invite', '').strip() != config.invite_code:
            error = 'That invite code is not right.'
        if error:
            return _page(request, 'pocket/user/join.html',
                         {'member': member, 'config': config, 'error': error})
        account = service.join(member)
        for person in Person.objects.filter(suggested_account=account):
            push.notify_suggestion(person, Settings.load())
        return redirect('pocket_tracker')
    return _page(request, 'pocket/user/join.html',
                 {'member': member, 'config': config, 'error': ''})


def login(request):
    """Pocket Money has no sign-in of its own any more. One hub, one password."""
    return redirect(f'{reverse("member_login")}?next={reverse("pocket_tracker")}')


def logout(request):
    return redirect('member_apps')


# --------------------------------------------------------------------------- #
# Half one — the Tracker
# --------------------------------------------------------------------------- #
@never_cache
@auth.account_required
def tracker(request):
    config = Settings.load()
    year, month = _month(request, config)
    summary = service.tracker_month(request.account, year, month, config)
    page = service.tracker_entries(request.account, year, month,
                                   before=request.GET.get('before'))
    shown = date(year, month, 1)
    previous = (shown - timedelta(days=1))
    following = (shown + timedelta(days=32)).replace(day=1)
    return _page(request, 'pocket/user/tracker.html', {
        'summary': summary, 'page': page, 'shown': shown,
        'prev_month': previous.strftime('%Y-%m'),
        'next_month': following.strftime('%Y-%m') if following <= config.today() else '',
        'in_categories': service.categories(request.account, Flow.IN),
        'out_categories': service.categories(request.account, Flow.OUT),
        'today': config.today(),
    })


@require_POST
@auth.account_required
def tracker_add(request):
    config = Settings.load()
    category = None
    if request.POST.get('category'):
        category = Category.objects.filter(pk=request.POST['category'],
                                           account=request.account).first()
    try:
        service.add_tracker(request.account, request.POST.get('flow'),
                            request.POST.get('amount'), category=category,
                            date=_parse_date(request.POST.get('date'), config),
                            source=request.POST.get('source', ''),
                            note=request.POST.get('note', ''), config=config)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return _back(request)


@require_POST
@auth.account_required
def tracker_void(request, pk):
    entry = get_object_or_404(TrackerEntry, pk=pk, account=request.account)
    service.void_tracker(request.account, entry, request.POST.get('reason', ''))
    return _back(request)


@never_cache
@auth.account_required
def tracker_csv(request):
    config = Settings.load()
    year, month = _month(request, config)
    body = service.tracker_csv(request.account, year, month)
    response = HttpResponse(body, content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="tracker-{year}-{month:02d}.csv"'
    return _no_store(response)


@never_cache
@auth.account_required
def categories(request):
    if request.method == 'POST':
        try:
            service.add_category(request.account, request.POST.get('flow'),
                                 request.POST.get('name'))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            return redirect(f"{reverse('pocket_categories')}?flow={request.POST.get('flow')}")
    flow = request.GET.get('flow') if request.GET.get('flow') in Flow.values else Flow.OUT
    rows = list(request.account.categories.filter(flow=flow))
    for row in rows:
        row.used = (row.tracker_entries.filter(voided_at__isnull=True).count()
                    + row.ledger_entries.filter(voided_at__isnull=True).count())
    return _page(request, 'pocket/user/categories.html',
                 {'rows': rows, 'flow': flow, 'flows': Flow.choices})


@require_POST
@auth.account_required
def category_archive(request, pk):
    category = get_object_or_404(Category, pk=pk, account=request.account)
    category.is_archived = not category.is_archived
    category.save(update_fields=['is_archived'])
    return redirect(f"{reverse('pocket_categories')}?flow={category.flow}")


# --------------------------------------------------------------------------- #
# Half two — the Ledger
# --------------------------------------------------------------------------- #
@never_cache
@auth.account_required
def ledger(request):
    own, shared = service.visible_folders(request.account)
    folders = []
    for folder in own:
        totals = service.folder_totals(folder)
        linked = folder.people.filter(linked_account__isnull=False).count()
        folders.append({'folder': folder, 'totals': totals, 'linked': linked,
                        'shared_with': [s.account for s in folder.shares.select_related('account')
                                        if s.account and s.is_open]})
    shared_rows = [{'folder': f, 'totals': service.folder_totals(f)} for f in shared]
    return _page(request, 'pocket/user/ledger.html', {
        'folders': folders, 'shared_rows': shared_rows,
        'position': service.account_position(request.account),
        'suggestions': service.suggestions_for(request.account),
    })


@require_POST
@auth.account_required
def folder_new(request):
    try:
        name = validate_name(request.POST.get('name'))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect('pocket_ledger')
    folder = Folder.objects.create(owner=request.account, name=name,
                                   note=request.POST.get('note', '')[:200])
    service.log('link', 'Made a folder', account=request.account, folder=folder, detail=name)
    return redirect('pocket_folder', pk=folder.pk)


def _folder_access(request, pk, person=None):
    folder = get_object_or_404(Folder, pk=pk)
    acc = service.access(request.account, folder, person)
    if not acc or (acc.role == 'person' and person is None):
        raise Http404('No such folder here.')
    return folder, acc


@never_cache
@auth.account_required
def folder(request, pk):
    folder, acc = _folder_access(request, pk)
    return _page(request, 'pocket/user/folder.html', {
        'folder': folder, 'acc': acc, 'people': service.folder_people(folder),
        'totals': service.folder_totals(folder),
        'shares': folder.shares.select_related('account').all(),
    })


@require_POST
@auth.account_required
def person_add(request, pk):
    folder, acc = _folder_access(request, pk)
    try:
        person = service.add_person(acc, request.POST.get('name'),
                                    phone=request.POST.get('phone', ''),
                                    upi_id=request.POST.get('upi_id', ''),
                                    note=request.POST.get('note', ''))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect('pocket_folder', pk=folder.pk)
    return redirect('pocket_person', pk=folder.pk, person_pk=person.pk)


@never_cache
@auth.account_required
def person(request, pk, person_pk):
    person = get_object_or_404(Person, pk=person_pk, folder_id=pk)
    folder, acc = _folder_access(request, pk, person)
    config = Settings.load()
    page = service.statement(person, mirrored=acc.mirrored, before=request.GET.get('before'))
    return _page(request, 'pocket/user/person.html', {
        'folder': folder, 'acc': acc, 'person': person, 'page': page,
        'categories': service.categories(folder.owner, Flow.LEDGER),
        'upi': service.upi_link(person.upi_id, person.name),
        'today': config.today(),
        'deletable_here': not service.is_shared(folder) and not person.linked_account_id,
    })


@require_POST
@auth.account_required
def ledger_add(request, pk, person_pk):
    person = get_object_or_404(Person, pk=person_pk, folder_id=pk)
    folder, acc = _folder_access(request, pk, person)
    config = Settings.load()
    category = None
    if request.POST.get('category'):
        category = Category.objects.filter(pk=request.POST['category'],
                                           account=folder.owner).first()
    due = request.POST.get('due_on') or ''
    try:
        entry = service.add_ledger(
            acc, person, request.POST.get('kind'), request.POST.get('amount'),
            category=category, date=_parse_date(request.POST.get('date'), config),
            source=request.POST.get('source', ''), note=request.POST.get('note', ''),
            due_on=date.fromisoformat(due) if due else None, config=config)
    except (ValidationError, ValueError) as exc:
        messages.error(request, getattr(exc, 'messages', [str(exc)])[0])
    else:
        push.notify_entry(entry, request.account, config)
    return _back(request, 'pocket_ledger')


@require_POST
@auth.account_required
def settle(request, pk, person_pk):
    person = get_object_or_404(Person, pk=person_pk, folder_id=pk)
    folder, acc = _folder_access(request, pk, person)
    try:
        entry = service.settle_up(acc, person)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        if entry:
            push.notify_entry(entry, request.account)
            messages.success(request, f'{person.name} is square.')
    return _back(request, 'pocket_ledger')


@require_POST
@auth.account_required
def ledger_void(request, pk, entry_pk):
    entry = get_object_or_404(LedgerEntry, pk=entry_pk, folder_id=pk)
    folder, acc = _folder_access(request, pk)
    try:
        service.void_ledger(acc, entry, request.POST.get('reason', ''))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    return _back(request, 'pocket_ledger')


@never_cache
@auth.account_required
def person_edit(request, pk, person_pk):
    person = get_object_or_404(Person, pk=person_pk, folder_id=pk)
    folder, acc = _folder_access(request, pk, person)
    if not acc.can_write or acc.role == 'person':
        raise Http404('Not yours to change.')
    if request.method == 'POST':
        try:
            person.name = validate_name(request.POST.get('name'))
            person.upi_id = validate_upi(request.POST.get('upi_id', ''))
        except ValidationError as exc:
            messages.error(request, exc.messages[0])
        else:
            person.phone = auth.normalise_phone(request.POST.get('phone', ''))
            person.note = request.POST.get('note', '')[:200]
            person.save(update_fields=['name', 'upi_id', 'phone', 'note'])
            service.log('link', 'Edited a person', account=request.account, folder=folder,
                        detail=person.name)
            return redirect('pocket_person', pk=folder.pk, person_pk=person.pk)
    return _page(request, 'pocket/user/person_edit.html',
                 {'folder': folder, 'person': person, 'acc': acc})


@never_cache
@auth.account_required
def person_invite(request, pk, person_pk):
    """Offer somebody their own line — and say plainly what they will see."""
    person = get_object_or_404(Person, pk=person_pk, folder_id=pk)
    folder, acc = _folder_access(request, pk, person)
    if not acc.can_manage:
        raise Http404('Only the owner invites.')
    config = Settings.load()
    if request.method == 'POST':
        what = request.POST.get('what', 'invite')
        if what == 'unlink':
            service.unlink_person(person, by=request.account)
            messages.info(request, f'{person.name} can no longer see this line.')
        elif what == 'permission':
            person.can_edit = request.POST.get('can_edit') == '1'
            person.save(update_fields=['can_edit'])
        else:
            service.invite_person(person, can_edit=request.POST.get('can_edit') == '1',
                                  by=request.account)
        return redirect('pocket_person_invite', pk=folder.pk, person_pk=person.pk)

    link = ''
    if person.link_code:
        link = request.build_absolute_uri(reverse('pocket_accept_person',
                                                  args=[pretty_code(person.link_code)]))
    others = folder.people.exclude(pk=person.pk).count()
    return _page(request, 'pocket/user/invite.html', {
        'folder': folder, 'person': person, 'acc': acc, 'link': link, 'others': others,
        'entries': person.entries.filter(voided_at__isnull=True).count(),
        'balance': service.person_balance(person), 'config': config,
        'added': (person.entries.filter(created_by=person.linked_account).order_by('-date')[:5]
                  if person.linked_account_id else []),
    })


@never_cache
@auth.account_required
def folder_share(request, pk):
    folder, acc = _folder_access(request, pk)
    if not acc.can_manage:
        raise Http404('Only the owner shares.')
    if request.method == 'POST':
        if request.POST.get('stop'):
            share = folder.shares.filter(pk=request.POST['stop']).first()
            if share:
                service.stop_share(share, by=request.account)
        else:
            service.share_folder(folder, can_edit=request.POST.get('can_edit') == '1',
                                 by=request.account)
        return redirect('pocket_folder_share', pk=folder.pk)

    open_link = folder.shares.filter(account__isnull=True, declined_at__isnull=True).last()
    link = ''
    if open_link:
        link = request.build_absolute_uri(reverse('pocket_accept_folder',
                                                  args=[pretty_code(open_link.code)]))
    return _page(request, 'pocket/user/share.html', {
        'folder': folder, 'acc': acc, 'link': link, 'open_link': open_link,
        'shares': folder.shares.select_related('account').filter(account__isnull=False),
        'in_folder': folder.people.filter(linked_account__isnull=False).select_related(
            'linked_account'),
    })


@never_cache
@auth.account_required
def folder_csv(request, pk):
    folder, acc = _folder_access(request, pk)
    if acc.role == 'person':
        raise Http404('Not yours to take.')
    response = HttpResponse(service.folder_csv(folder), content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="{folder.name}.csv"'
    return _no_store(response)


# --------------------------------------------------------------------------- #
# The other side of a line
# --------------------------------------------------------------------------- #
def _my_line(request, pk):
    person = get_object_or_404(Person, pk=pk, linked_account=request.account,
                               linked_at__isnull=False, declined_at__isnull=True)
    acc = service.access(request.account, person.folder, person)
    if not acc:
        raise Http404('Not your line.')
    return person, acc


@never_cache
@auth.account_required
def line(request, pk):
    person, acc = _my_line(request, pk)
    owner = person.folder.owner
    page = service.statement(person, mirrored=True, before=request.GET.get('before'))
    return _page(request, 'pocket/user/line.html', {
        'person': person, 'acc': acc, 'owner': owner, 'page': page,
        'folder': person.folder, 'upi': service.upi_link(owner.upi_id, owner.name),
        'today': Settings.load().today(),
    })


@require_POST
@auth.account_required
def line_add(request, pk):
    person, acc = _my_line(request, pk)
    config = Settings.load()
    try:
        entry = service.add_ledger(acc, person, request.POST.get('kind'),
                                   request.POST.get('amount'),
                                   date=_parse_date(request.POST.get('date'), config),
                                   source=request.POST.get('source', ''),
                                   note=request.POST.get('note', ''), config=config)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        push.notify_entry(entry, request.account, config)
    return redirect('pocket_line', pk=person.pk)


@require_POST
@auth.account_required
def line_leave(request, pk):
    person, acc = _my_line(request, pk)
    service.unlink_person(person, by=request.account)
    messages.info(request, f"You have left {person.folder.owner.name}'s line. "
                           'The entries stay with them.')
    return redirect('pocket_shared')


# --------------------------------------------------------------------------- #
# Invitations
# --------------------------------------------------------------------------- #
@never_cache
@auth.account_required
def shared(request):
    account = request.account
    lines = []
    for person in service.linked_lines(account):
        balance = -service.person_balance(person)
        lines.append({'person': person, 'balance': balance, 'owner': person.folder.owner})
    own_people = Person.objects.filter(folder__owner=account).values_list('name', flat=True)
    clash = sorted({p.folder.owner.name for p in service.linked_lines(account)}
                   & set(own_people))
    return _page(request, 'pocket/user/shared.html', {
        'invites': service.pending_person_invites(account),
        'lines': lines,
        'folders': [{'folder': f, 'totals': service.folder_totals(f)}
                    for f in service.visible_folders(account)[1]],
        'clash': clash,
    })


@never_cache
@auth.account_required
def accept_person(request, code):
    config = Settings.load()
    person = service.person_by_code(code, config)
    if not person:
        return _page(request, 'pocket/user/accept.html',
                     {'gone': True, 'what': 'invitation'}, status=404)
    owner = person.folder.owner
    if request.method == 'POST':
        if request.POST.get('answer') == 'yes':
            try:
                service.accept_person_invite(person, request.account)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect('pocket_shared')
            push.notify_invite_answer(person, True, config)
            return redirect('pocket_line', pk=person.pk)
        service.decline_person_invite(person, request.account)
        push.notify_invite_answer(person, False, config)
        return redirect('pocket_shared')
    return _page(request, 'pocket/user/accept.html', {
        'person': person, 'owner': owner, 'folder': person.folder,
        'balance': -service.person_balance(person),
        'entries': person.entries.filter(voided_at__isnull=True).count(),
        'others': person.folder.people.exclude(pk=person.pk).count(),
    })


@never_cache
@auth.account_required
def accept_folder(request, code):
    config = Settings.load()
    share = service.share_by_code(code, config)
    if not share:
        return _page(request, 'pocket/user/accept.html',
                     {'gone': True, 'what': 'share link'}, status=404)
    if request.method == 'POST':
        if request.POST.get('answer') == 'yes':
            try:
                service.accept_share(share, request.account)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
                return redirect('pocket_ledger')
            return redirect('pocket_folder', pk=share.folder_id)
        share.declined_at = timezone.now()
        share.save(update_fields=['declined_at'])
        return redirect('pocket_shared')
    return _page(request, 'pocket/user/accept.html', {
        'share': share, 'folder': share.folder, 'owner': share.folder.owner,
        'totals': service.folder_totals(share.folder),
    })


@require_POST
@auth.account_required
def suggestion(request, pk, answer):
    """"Somebody signed up with this number — is it your Mani?" Yes only sends an invitation."""
    person = get_object_or_404(Person, pk=pk, folder__owner=request.account,
                               suggested_account__isnull=False)
    if answer == 'yes':
        service.invite_person(person, can_edit=True, by=request.account)
        messages.success(request, f'{person.name} has been invited. They have to accept it too.')
    else:
        service.dismiss_suggestion(person, by=request.account)
    return redirect('pocket_ledger')


# --------------------------------------------------------------------------- #
# You
# --------------------------------------------------------------------------- #
@never_cache
@auth.account_required
def me(request):
    account = request.account
    if request.method == 'POST':
        what = request.POST.get('what')
        if what == 'notify':
            account.notify = request.POST.get('notify') == '1'
            account.save(update_fields=['notify'])
        return redirect('pocket_me')
    return _page(request, 'pocket/user/me.html', {
        'position': service.account_position(account),
        'folders': Folder.objects.filter(owner=account).count(),
        'lines': service.linked_lines(account).count(),
    })


@require_POST
@auth.account_required
def save_upi(request):
    try:
        request.account.upi_id = validate_upi(request.POST.get('upi_id', ''))
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
    else:
        request.account.save(update_fields=['upi_id'])
        messages.success(request, 'Saved.')
    return _back(request, 'pocket_me')


@csrf_exempt
@require_POST
@auth.account_required
def scan_upi(request):
    """What a scanned QR decoded to. The amount in it, if any, is deliberately ignored."""
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'ok': False, 'error': 'Nothing to read.'}, status=400)
    try:
        upi, name = service.parse_upi(payload.get('text'))
    except ValidationError as exc:
        return JsonResponse({'ok': False, 'error': exc.messages[0]})
    if not upi:
        return JsonResponse({'ok': False, 'error': 'That QR is not a UPI code.'})
    return JsonResponse({'ok': True, 'upi': upi, 'name': name})


@csrf_exempt
@require_POST
@auth.account_required
def save_syncup_token(request):
    """`window.SyncUp.token`, posted by the page when it is opened inside the app."""
    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'saved': False}, status=400)
    # One token per person, not one per module: opening any module inside SyncUp now makes
    # all of them reachable. `core.push` owns that rule.
    saved = core_push.remember_token(request.account, payload.get('token'))
    return JsonResponse({'saved': saved})


# --------------------------------------------------------------------------- #
# Putting a wrong figure right
# --------------------------------------------------------------------------- #
@never_cache
@auth.account_required
def tracker_edit(request, pk):
    """Correct or remove one of your own entries. Nobody else has ever seen it."""
    entry = get_object_or_404(TrackerEntry, pk=pk, account=request.account)
    config = Settings.load()
    error = ''
    if request.method == 'POST':
        if request.POST.get('what') == 'delete':
            service.delete_tracker(request.account, entry)
            messages.success(request, 'Deleted.')
            return redirect(f"{reverse('pocket_tracker')}?m={entry.date:%Y-%m}")
        category = None
        if request.POST.get('category'):
            category = Category.objects.filter(pk=request.POST['category'],
                                               account=request.account).first()
        try:
            service.edit_tracker(request.account, entry, flow=request.POST.get('flow'),
                                 amount=request.POST.get('amount'), category=category,
                                 date=_parse_date(request.POST.get('date'), config),
                                 source=request.POST.get('source', ''),
                                 note=request.POST.get('note', ''))
        except ValidationError as exc:
            error = exc.messages[0]
        else:
            messages.success(request, 'Saved.')
            return redirect(f"{reverse('pocket_tracker')}?m={entry.date:%Y-%m}")
    return _page(request, 'pocket/user/tracker_edit.html', {
        'entry': entry, 'error': error,
        'in_categories': service.categories(request.account, Flow.IN),
        'out_categories': service.categories(request.account, Flow.OUT),
    })


@never_cache
@auth.account_required
def ledger_edit(request, pk, entry_pk):
    """Correct one entry — and remove it outright when nobody else is reading this book."""
    entry = get_object_or_404(LedgerEntry.objects.select_related('person'),
                              pk=entry_pk, folder_id=pk)
    folder, acc = _folder_access(request, pk, entry.person)
    if not acc.can_write or acc.role == 'person':
        raise Http404('Not yours to change.')
    config = Settings.load()
    error = ''
    deletable = service.can_delete_ledger(acc, entry)

    if request.method == 'POST':
        what = request.POST.get('what')
        if what == 'delete':
            try:
                service.delete_ledger(acc, entry)
            except ValidationError as exc:
                messages.error(request, exc.messages[0])
            else:
                messages.success(request, 'Deleted.')
            return redirect('pocket_person', pk=folder.pk, person_pk=entry.person_id)
        if what == 'void':
            service.void_ledger(acc, entry, request.POST.get('reason', ''))
            messages.success(request, 'Taken back. It stays on the line, struck through.')
            return redirect('pocket_person', pk=folder.pk, person_pk=entry.person_id)
        category = None
        if request.POST.get('category'):
            category = Category.objects.filter(pk=request.POST['category'],
                                               account=folder.owner).first()
        due = request.POST.get('due_on') or ''
        try:
            service.edit_ledger(acc, entry, kind=request.POST.get('kind'),
                                amount=request.POST.get('amount'), category=category,
                                date=_parse_date(request.POST.get('date'), config),
                                source=request.POST.get('source', ''),
                                note=request.POST.get('note', ''),
                                due_on=date.fromisoformat(due) if due else '')
        except (ValidationError, ValueError) as exc:
            error = getattr(exc, 'messages', [str(exc)])[0]
        else:
            messages.success(request, 'Saved.')
            return redirect('pocket_person', pk=folder.pk, person_pk=entry.person_id)

    return _page(request, 'pocket/user/ledger_edit.html', {
        'entry': entry, 'folder': folder, 'acc': acc, 'error': error,
        'deletable': deletable, 'shared': service.is_shared(folder),
        'categories': service.categories(folder.owner, Flow.LEDGER),
    })
