"""The hub's own public pages: sign in, your apps, you.

Three screens that used to be two sets of three, one per module. A module keeps its own front
door — `/pocket/` still looks like Pocket Money — but the sign-in behind it is this one, and the
session it starts works everywhere.

Nothing here knows what a diary or a ledger is. It knows people.
"""
from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_POST

from . import auth, syncup_people
from .models import Member
from .modules import MODULES


def _page(request, template, context, status=200):
    member = getattr(request, 'member', None) or auth.current(request)
    base = {'member': member}
    base.update(context)
    response = render(request, template, base, status=status)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'same-origin'
    return response


def _signed_in(request, member, to):
    """Sign in and go somewhere."""
    auth.sign_in(request, member)
    return redirect(to)


def _profiles(member):
    """Which modules this person is actually in, and which they could join.

    "Being in a module" is simply having its profile row — there is no separate membership
    table to drift out of step with reality.
    """
    joined, available = [], []
    for module in MODULES:
        if not module.is_live:
            continue
        profile = getattr(member, module.key, None) if member else None
        (joined if profile else available).append({'module': module, 'profile': profile})
    return joined, available


@never_cache
def join(request):
    """Making a hub account — the one front door for everything here.

    Each module used to own a sign-up form. Taking those away without putting one here would
    have left a server nobody new could get into.
    """
    if auth.current(request):
        return redirect(auth.safe_next(request))
    error = ''
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        email, phone = auth.split_sign_in_id(request.POST.get('sign_in_id'))
        password = request.POST.get('password') or ''
        # The same two checks the console runs, so the rules cannot drift apart.
        _, error = auth.check_identity(name, email, phone)
        error = error or auth.check_password_value(password)
        if not error:
            member = Member.objects.create(name=name[:80], email=email or None,
                                           phone=phone or None)
            auth.set_password(member, password)
            return _signed_in(request, member, auth.safe_next(request))
    return _page(request, 'core/member/join.html',
                 {'error': error, 'next': auth.next_value(request)})


@never_cache
def sign_in(request):
    if auth.current(request):
        return redirect(auth.safe_next(request))
    error = ''
    if request.method == 'POST':
        member = auth.find_member(request.POST.get('sign_in_id'))
        password = request.POST.get('password')
        if member and member.can_sign_in and auth.check_member_password(member, password):
            auth.settle_password(member, password)     # two hashes become one, quietly
            return _signed_in(request, member, auth.safe_next(request))
        error = 'That email or phone and password do not go together.'
    return _page(request, 'core/member/sign_in.html',
                 {'error': error, 'next': auth.next_value(request)})


@require_POST
def ask_for_link(request):
    """No mail server here, so the link waits with the admin — same as the diary always did."""
    member = auth.find_member(request.POST.get('sign_in_id'))
    if member and member.can_sign_in:
        auth.new_login_key(member)
    messages.info(request, 'If that account exists, a sign-in link is waiting with the admin.')
    return redirect('member_login')


@never_cache
def magic(request, key):
    member = auth.member_by_login_key(key)
    if not member:
        return _page(request, 'core/member/sign_in.html',
                     {'error': 'That link has been used, or it is more than an hour old.',
                      'next': ''}, status=404)
    auth.spend_login_key(member)
    return _signed_in(request, member, auth.safe_next(request))


def _close_linked_chat(request, member):
    """J6 keeps a session of its own, and one kind of it belongs to this sign-out.

    A chat account **linked** to the person signing out is that person, and leaving it open on
    a shared phone is the leak sign-out exists to prevent. An unlinked Self-ID session is
    somebody else as far as this server knows — an anonymous account that happens to share a
    browser — so it is left alone, exactly as it was before the hub could open J6 at all.

    Reaching into a module from core, inside a function and guarded, is what
    `views_people._unmerged_counts` already does. The alternative is a hook registry for one
    caller.
    """
    try:
        from j6 import auth as chat
        from j6.models import Account
    except Exception:                       # pragma: no cover - the module is optional
        return
    # Whatever they had decided about the hub opening J6 goes with the session it was decided
    # in, so signing in again starts clean.
    request.session.pop(chat.NO_HUB_KEY, None)
    pk = request.session.get(chat.SESSION_KEY)
    if pk and member and Account.objects.filter(pk=pk, member=member).exists():
        request.session.pop(chat.SESSION_KEY, None)


@require_POST
def sign_out(request):
    """POST, not GET: a link anybody can put in an <img> should not end your session."""
    _close_linked_chat(request, auth.current(request))
    auth.sign_out(request)
    return redirect('member_login')


@never_cache
@auth.member_required
def apps(request):
    joined, available = _profiles(request.member)
    return _page(request, 'core/member/apps.html',
                 {'joined': joined, 'available': available,
                  'has_password': bool(request.member.password_hash),
                  'connect': syncup_people.connect_offer(request, request.member),
                  'greeted': request.session.pop(auth.GREET_KEY, False)})


@require_POST
@never_cache
@auth.member_required
def connect(request):
    """The connect card: this account and the SyncUp person on this phone, joined.

    Three answers, and two of them are not "yes". Somebody using their own account on a
    family phone must be able to say so once and never be asked again — which is what
    `refuse` records — and somebody who simply has not decided should be able to get on with
    what they opened the app for.
    """
    member = request.member
    fields = syncup_people.connect_offer(request, member)
    if not fields:
        return redirect('member_apps')

    what = request.POST.get('what')
    if what == 'not-now':
        request.session[syncup_people.CONNECT_HIDDEN] = True
        return redirect('member_apps')
    if what == 'different':
        syncup_people.refuse(member, fields['syncup_id'])
        request.session.pop(syncup_people.CONNECT_KEY, None)
        messages.success(request, 'Noted — you will not be asked about that one again.')
        return redirect('member_apps')

    password = request.POST.get('password')
    if not auth.check_member_password(member, password):
        messages.error(request, 'That password is not right, so nothing was joined.')
        return redirect('member_apps')

    ok, why = syncup_people.join(member, fields)
    if not ok:
        messages.error(request, why)
        return redirect('member_apps')
    request.session.pop(syncup_people.CONNECT_KEY, None)
    messages.success(request, 'Joined. From now on your SyncUp password signs you in here '
                              'too, and your name comes from SyncUp.')
    return redirect('member_apps')


# --------------------------------------------------------------------------- #
# Use token — signing in from inside the SyncUp app
#
# The app hands the page `window.SyncUp.token`. That token is not a credential and is never
# trusted on its own: it is posted here, and SyncUp's own server says whose it is. A phone's
# own token, belonging to nobody signed in, never signs anybody in.
#
# Four endings, and one rule behind all of them — a person must never end up with two
# SyncHub accounts:
#   already linked and used    -> sign in
#   an older account we know   -> that account's own password, once
#   nothing known              -> "do you already have one here?"
#   not a signed-in person     -> say so, and the normal sign-in is right there
# --------------------------------------------------------------------------- #
PENDING = 'synchub_syncup_pending'


def _pending(request):
    data = request.session.get(PENDING)
    return data if isinstance(data, dict) and data.get('fields') else None


def _landed(request, member, fields):
    """Join this member to the SyncUp person and sign them in.

    Where they were going is carried in the session, because the question screens in between
    post nothing of their own. Without it, somebody who opened J6, pressed Use token and
    answered "I am new" arrives on /apps/ wondering where the chat went.
    """
    going = (_pending(request) or {}).get('next') or ''
    ok, why = syncup_people.join(member, fields)
    if not ok:
        messages.error(request, why)
        return redirect('member_login')
    request.session.pop(PENDING, None)
    auth.sign_in(request, member, via='token')
    request.session[auth.TOKEN_SEEN_KEY] = fields['syncup_token']
    return redirect(auth.safe_url(request, going))


@require_POST
@never_cache
def token_sign_in(request):
    """The Use token button: ask SyncUp whose token this is, then route."""
    person, why = syncup_people.person_for_token(request.POST.get('token'))
    if person is None:
        messages.info(request, why)
        return redirect('member_login')

    fields = syncup_people.clean(person)
    request.session[PENDING] = {'fields': fields, 'next': request.POST.get('next', '')}

    linked = Member.objects.filter(syncup_id=fields['syncup_id']).first()
    if linked and not linked.syncup_unused:
        if not linked.can_sign_in:
            messages.error(request, 'That account is switched off here.')
            return redirect('member_login')
        syncup_people.take(fields)                  # catch up on anything SyncUp changed
        linked.refresh_from_db()
        request.session.pop(PENDING, None)
        auth.sign_in(request, linked, via='token')
        request.session[auth.TOKEN_SEEN_KEY] = fields['syncup_token']
        return redirect(auth.safe_next(request))

    older, _ = syncup_people.older_account_for(fields)
    return redirect('member_token_join' if older else 'member_token_first')


@never_cache
def token_join(request):
    """An older SyncHub account that looks like them — its own password, once.

    The password is the whole safeguard. On a shared phone it is what stops one person
    picking up another's diary by pressing a button.
    """
    pending = _pending(request)
    if not pending:
        return redirect('member_login')
    fields = pending['fields']
    older, why = syncup_people.older_account_for(fields)
    if not older:
        return redirect('member_token_first')

    error = ''
    if request.method == 'POST':
        if request.POST.get('what') == 'not-mine':
            # A same-phone match only ever meant "this phone", never "this person".
            syncup_people.refuse(older, fields['syncup_id'])
            return redirect('member_token_first')
        password = request.POST.get('password')
        if older.can_sign_in and auth.check_member_password(older, password):
            return _landed(request, older, fields)
        error = 'That password is not right.'

    return _page(request, 'core/member/token_join.html', {
        'older': older, 'why': why, 'person': fields, 'error': error,
        'can_refuse': why == Member.Waiting.TOKEN,
    })


@never_cache
def token_first(request):
    """Nothing older is known: "do you already have a SyncHub account?"."""
    pending = _pending(request)
    if not pending:
        return redirect('member_login')
    fields = pending['fields']

    error = ''
    if request.method == 'POST':
        if request.POST.get('what') == 'new':
            made = Member.objects.filter(syncup_id=fields['syncup_id']).first()
            if made is None:
                syncup_people.take(fields)
                made = Member.objects.filter(syncup_id=fields['syncup_id']).first()
            if made is None:
                error = 'Something went wrong making that account. Try again.'
            else:
                return _landed(request, made, fields)
        else:
            found = auth.find_member(request.POST.get('sign_in_id'))
            password = request.POST.get('password')
            if (found is None or found.syncup_id or not found.can_sign_in
                    or not auth.check_member_password(found, password)):
                error = 'That email or phone and password do not go together.'
            else:
                return _landed(request, found, fields)

    return _page(request, 'core/member/token_first.html', {'person': fields, 'error': error})


@never_cache
@auth.member_required
def you(request):
    member = request.member
    if request.method == 'POST':
        what = request.POST.get('what')
        # SyncUp owns these people's name and password; the theme is still theirs to pick.
        if member.syncup_id and what in ('name', 'password'):
            messages.info(request, 'Your name and password come from SyncUp. Change them '
                                   'there and they arrive here within a few minutes.')
            return redirect('member_you')
        if what == 'name':
            name = (request.POST.get('name') or '').strip()
            if len(name) < 2:
                messages.error(request, 'A name needs at least two characters.')
            else:
                member.name = name[:80]
                member.save(update_fields=['name'])
        elif what == 'theme':
            theme = request.POST.get('theme')
            if theme in dict(Member.THEMES):
                member.theme = theme
                member.save(update_fields=['theme'])
        elif what == 'password':
            new = request.POST.get('new_password') or ''
            if len(new) < auth.PASSWORD_MIN:
                messages.error(request,
                               f'A password needs at least {auth.PASSWORD_MIN} characters.')
            elif member.password_hash and not auth.check_member_password(
                    member, request.POST.get('current_password')):
                messages.error(request, 'That current password is not right.')
            else:
                auth.set_password(member, new)
                messages.success(request, 'Password saved.')
        return redirect('member_you')

    joined, _ = _profiles(member)
    return _page(request, 'core/member/you.html', {
        'joined': joined,
        'has_password': bool(member.password_hash),
    })
