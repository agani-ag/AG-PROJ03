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
        if len(name) < 2:
            error = 'A name needs at least two characters.'
        elif not email and not phone:
            error = 'An email address or a phone number, so you can get back in.'
        elif phone and len(phone) < 10:
            error = 'That phone number looks too short.'
        elif len(password) < auth.PASSWORD_MIN:
            error = f'A password needs at least {auth.PASSWORD_MIN} characters.'
        elif email and Member.objects.filter(email__iexact=email).exists():
            error = 'There is already an account with that email address.'
        elif phone and Member.objects.filter(phone__endswith=phone).exists():
            error = 'There is already an account with that phone number.'
        else:
            member = Member.objects.create(name=name[:80], email=email or None,
                                           phone=phone or None)
            auth.set_password(member, password)
            return _signed_in(request, member, auth.safe_next(request))
    return _page(request, 'core/member/join.html',
                 {'error': error, 'next': request.GET.get('next', '')})


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
                 {'error': error, 'next': request.GET.get('next', '')})


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


@require_POST
def sign_out(request):
    """POST, not GET: a link anybody can put in an <img> should not end your session."""
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
