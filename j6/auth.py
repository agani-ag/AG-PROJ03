"""Who is asking — in a browser, and from a desktop client.

Two doors into the same house. A browser gets a session cookie, the way every other page in
SyncHub works. A desktop or terminal client sends its Self-ID in a header, because it is not a
browser and should not have to pretend to be one for the sake of CSRF.

Both end at the same place: an `Account` on the request, or nothing.
"""
from functools import wraps

from django.http import JsonResponse
from django.shortcuts import redirect

from .models import Account

SESSION_KEY = 'j6_account_id'
TOKEN_HEADER = 'HTTP_X_J6_SELF'
SESSION_DAYS = 30


def sign_in(request, account):
    """Start a browser session for this account."""
    request.session[SESSION_KEY] = account.pk
    request.session.set_expiry(60 * 60 * 24 * SESSION_DAYS)
    account.touch()


def sign_out(request):
    request.session.pop(SESSION_KEY, None)


def by_self_id(self_id):
    """The account this Self-ID belongs to, if it is real and the account is open."""
    value = (self_id or '').strip()
    return Account.objects.filter(self_id=value, is_active=True).first() if value else None


def by_account_id(account_id):
    value = (account_id or '').strip().lower()
    return Account.objects.filter(account_id=value, is_active=True).first() if value else None


def current(request):
    """The signed-in account, or None.

    Re-read on every request rather than trusted from the session, so suspending somebody in the
    console stops them at once instead of whenever their session happens to end.
    """
    pk = request.session.get(SESSION_KEY)
    if not pk:
        return None
    return Account.objects.filter(pk=pk, is_active=True).first()


def account_required(view):
    """Signed-in account only. Anyone else goes to the welcome screen."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        account = current(request)
        if not account:
            sign_out(request)
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
