"""The hub's own public pages: sign in, your apps, you.

Three screens that used to be two sets of three, one per module. A module keeps its own front
door — `/pocket/` still looks like Pocket Money — but the sign-in behind it is this one, and the
session it starts works everywhere.

Nothing here knows what a diary or a ledger is. It knows people.
"""
import json

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
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


#: Set when somebody signs out on purpose, and read before any quiet sign-in is attempted.
#: Without it the silent hand-off fires on the very next page and they can never leave while
#: signed in to SyncUp — sign-out stops meaning anything. A cookie rather than a session flag,
#: so closing the browser does not quietly re-enable it, and httponly so the page cannot forge
#: one either way. Any deliberate sign-in clears it.
NO_AUTO_COOKIE = 'synchub_no_auto'
NO_AUTO_DAYS = 30


def _signed_in(request, member, to, via='password'):
    """Sign in, go somewhere, and lift the "do not sign me in by myself" marker."""
    auth.sign_in(request, member, via=via)
    response = redirect(to)
    response.delete_cookie(NO_AUTO_COOKIE)
    return response


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
    have left a server nobody new could get into, since SyncUp sign-in is not live yet.
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
    from . import syncup_sso
    return _page(request, 'core/member/sign_in.html',
                 {'error': error, 'next': request.GET.get('next', ''),
                  'auto_sso': NO_AUTO_COOKIE not in request.COOKIES,
                  'sso_url': reverse('member_sso') if syncup_sso.is_configured() else ''})


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
    return _signed_in(request, member, auth.safe_next(request), via='link')


@require_POST
def sign_out(request):
    """POST, not GET: a link anybody can put in an <img> should not end your session.

    Leaving a marker behind matters as much as clearing the session. Without it the quiet
    SyncUp sign-in fires on the very next page and puts them straight back in — which is not
    signing out, it is a redirect with extra steps.
    """
    auth.sign_out(request)
    response = redirect('member_login')
    response.set_cookie(NO_AUTO_COOKIE, '1', max_age=60 * 60 * 24 * NO_AUTO_DAYS,
                        httponly=True, samesite='Lax')
    return response


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
        'via': auth.signed_in_via(request),
    })


# --------------------------------------------------------------------------- #
# Continue with SyncUp
#
# Inert until an SSO key is set: without one the button is never rendered and these views say
# so rather than guessing. The page hands over a one-time code and nothing else — who the
# person is comes from SyncUp's server, not from anything JavaScript could have typed.
# --------------------------------------------------------------------------- #
PENDING_KEY = 'synchub_sso_pending'


@require_POST
def sso_sign_in(request):
    from . import syncup_sso

    if not syncup_sso.is_configured():
        messages.error(request, 'SyncUp sign-in is not set up on this server.')
        return redirect('member_login')

    user, error = syncup_sso.check(request.POST.get('code'))
    if error:
        messages.error(request, error)
        return redirect('member_login')

    outcome, member = syncup_sso.resolve(user)
    if outcome == 'linked':
        if not member.can_sign_in:
            messages.error(request, 'That account is closed here.')
            return redirect('member_login')
        return _signed_in(request, member, auth.safe_next(request), via='syncup')

    if outcome == 'prove':
        # Somebody here already has that email or phone. SyncUp does not verify addresses, so
        # arriving with one is not proof of being its owner — they type their password first.
        request.session[PENDING_KEY] = {'user': user, 'member': member.pk,
                                        'next': request.POST.get('next', '')}
        return redirect('member_prove')

    member = syncup_sso.create(user)
    return _signed_in(request, member, auth.safe_next(request), via='syncup')


@never_cache
def prove(request):
    """"We already know that address — show us it is yours."" Or: make a separate account."""
    from . import syncup_sso

    pending = request.session.get(PENDING_KEY)
    if not pending:
        return redirect('member_login')
    member = Member.objects.filter(pk=pending['member']).first()
    if not member:
        request.session.pop(PENDING_KEY, None)
        return redirect('member_login')

    error = ''
    if request.method == 'POST':
        if request.POST.get('what') == 'separate':
            # Not created here. The address belongs to the account above, so this one would have
            # no email — and `/in/` looks people up by email or phone *before* checking a
            # password, so it would be unreachable from a browser whatever password it had.
            # The next screen collects an identifier and a password, and cannot be skipped.
            return redirect('member_separate')

        password = request.POST.get('password')
        if member.can_sign_in and auth.check_member_password(member, password):
            auth.settle_password(member, password)
            syncup_sso.attach(member, pending['user'])
            request.session.pop(PENDING_KEY, None)
            return _signed_in(request, member, auth.safe_next(request), via='syncup')
        error = 'That password is not right.'

    return _page(request, 'core/member/prove.html',
                 {'claim': member, 'user': pending['user'], 'error': error})


@never_cache
def separate(request):
    """"Make a separate account" — but not without a way back into it.

    Asking here rather than nudging later, because the moment this account exists with neither
    an email nor a phone, the SyncUp app is the only thing in the world that can open it.
    """
    from . import syncup_sso

    pending = request.session.get(PENDING_KEY)
    if not pending:
        return redirect('member_login')

    error = ''
    typed = request.POST.get('sign_in_id', '')
    if request.method == 'POST':
        email, phone = auth.split_sign_in_id(typed)
        password = request.POST.get('password') or ''
        if not email and not phone:
            error = 'An email address or a phone number, so you can get back in.'
        elif phone and len(phone) < 10:
            error = 'That phone number looks too short.'
        elif len(password) < auth.PASSWORD_MIN:
            error = f'A password needs at least {auth.PASSWORD_MIN} characters.'
        else:
            error = syncup_sso.identifier_taken(email, phone)
        if not error:
            member = syncup_sso.create(pending['user'], email=email, phone=phone,
                                       password=password)
            request.session.pop(PENDING_KEY, None)
            messages.success(request, 'Separate account made. You can sign in with that '
                                      'password from any browser.')
            return _signed_in(request, member, 'member_apps', via='syncup')

    return _page(request, 'core/member/separate.html',
                 {'user': pending['user'], 'error': error, 'typed': typed})


@require_POST
def sso_account_changed(request):
    """The app says the SyncUp account switched or signed out while this page was open.

    Two rules, and they are different on purpose:

    * **A different person** always ends the session. Shared phones are exactly why.
    * **Signed out of SyncUp** ends only a session that *began* through SyncUp and belongs to
      somebody with no password of their own. If they have a password, this session is theirs
      by their own right and killing it because a different app signed out would be rude.
    """
    member = auth.current(request)
    if not member:
        return JsonResponse({'signed_out': False})

    try:
        payload = json.loads(request.body or b'{}')
    except ValueError:
        return JsonResponse({'signed_out': False}, status=400)
    now_id = (payload.get('id') or '').strip()

    different = bool(now_id) and bool(member.syncup_id) and now_id != member.syncup_id
    sso_only = auth.signed_in_via(request) == 'syncup' and not member.password_hash
    if different or (not now_id and sso_only):
        auth.sign_out(request)
        # J6 keeps a session of its own, because a Self-ID is not a hub sign-in. A different
        # person holding the phone must not be left looking at somebody's chats, so that one
        # goes too — the Self-ID still works and nothing is deleted.
        request.session.pop('j6_account_id', None)
        return JsonResponse({'signed_out': True})
    return JsonResponse({'signed_out': False})
