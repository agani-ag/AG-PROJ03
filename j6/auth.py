"""Who is asking — in a browser, and from a desktop client.

Three doors into the same house. A Self-ID typed into this browser gets a session of its own.
A SyncHub sign-in opens whichever chat account is linked to that member, because one sign-in is
supposed to open all three modules. A desktop or terminal client sends its Self-ID in a header,
because it is not a browser and should not have to pretend to be one for the sake of CSRF.

All three end at the same place: an `Account` on the request, or nothing.
"""
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import redirect

from core import auth as hub

from .models import Account

SESSION_KEY = 'j6_account_id'
#: Set when somebody signs out of J6 on purpose, and read before the hub is ever consulted.
#: Without it the hub hands them straight back in on the next page and Sign out stops meaning
#: anything — which is exactly how the old SyncUp sign-in got it wrong. Cleared by any
#: deliberate way back in.
NO_HUB_KEY = 'j6_no_hub'
TOKEN_HEADER = 'HTTP_X_J6_SELF'
SESSION_DAYS = 30


def sign_in(request, account):
    """Start a browser session for this account, on its Self-ID alone.

    Deliberately separate from the hub: this session is the Self-ID's, so it survives a hub
    sign-out the way any other credential typed into this browser would.
    """
    request.session[SESSION_KEY] = account.pk
    request.session.pop(NO_HUB_KEY, None)
    request.session.set_expiry(60 * 60 * 24 * SESSION_DAYS)
    account.touch()


def allow_hub_session(request):
    """Let the hub open J6 again, for somebody who had signed out of it.

    No session of its own is written: the account is found through the member every request, so
    signing out of SyncHub closes the chat too. That is the point on a shared phone.
    """
    request.session.pop(NO_HUB_KEY, None)


def forget(request):
    """Drop a stale session key without deciding anything. Housekeeping, not a sign-out.

    The difference matters: `sign_out` also says "do not let the hub open this again", and a
    guard tidying up after a suspended or deleted account must not say that on somebody's
    behalf. It did, once, and the symptom was that signing back in to SyncHub no longer opened
    the chat — a redirect had quietly cast a vote.
    """
    request.session.pop(SESSION_KEY, None)


def sign_out(request):
    """Somebody pressed Sign out. That has to stick, even with the hub still open."""
    request.session.pop(SESSION_KEY, None)
    request.session[NO_HUB_KEY] = True


def by_self_id(self_id):
    """The account this Self-ID belongs to, if it is real and the account is open."""
    value = (self_id or '').strip()
    return Account.objects.filter(self_id=value, is_active=True).first() if value else None


def by_account_id(account_id):
    value = (account_id or '').strip().lower()
    return Account.objects.filter(account_id=value, is_active=True).first() if value else None


def by_member(member):
    """The chat account linked to this hub member, if they have one."""
    return Account.objects.filter(member=member, is_active=True).first() if member else None


def current(request):
    """The signed-in account, or None.

    A Self-ID session first, then the hub. Re-read on every request rather than trusted from the
    session, so suspending somebody in the console stops them at once instead of whenever their
    session happens to end — and so that signing out of SyncHub closes the chat in the same
    breath, since nothing about the hub route is cached in this session.
    """
    pk = request.session.get(SESSION_KEY)
    if pk:
        return Account.objects.filter(pk=pk, is_active=True).first()
    if request.session.get(NO_HUB_KEY):
        return None
    return by_member(hub.current(request))


def account_required(view):
    """Signed-in account only. Anyone else goes to the welcome screen."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        account = current(request)
        if not account:
            forget(request)
            return redirect('j6_welcome')
        request.account = account
        account.touch()
        return view(request, *args, **kwargs)
    return inner


def token_required(view):
    """Desktop and terminal clients: the Self-ID in `X-J6-Self`, and nothing else.

    No cookie, so no CSRF to carry; the header *is* the credential. A suspended account is
    refused here exactly as it is in a browser.
    """
    @wraps(view)
    def inner(request, *args, **kwargs):
        account = by_self_id(request.META.get(TOKEN_HEADER, ''))
        if not account:
            return JsonResponse({'error': 'unknown or suspended account'}, status=401)
        request.account = account
        account.touch()
        return view(request, *args, **kwargs)
    return inner
