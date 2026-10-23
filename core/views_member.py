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

from . import auth
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
                  'greeted': request.session.pop(auth.GREET_KEY, False)})


@never_cache
@auth.member_required
def you(request):
    member = request.member
    if request.method == 'POST':
        what = request.POST.get('what')
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
