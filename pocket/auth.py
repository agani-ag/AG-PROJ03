"""Who is using Pocket Money.

Almost nothing is left here. Identity — the password, the magic link, the session — belongs to
`core.auth` now, because the same person keeps a diary and a set of money books and used to
have two of everything. What Pocket Money still owns is its *profile*: the `Account` row with
the UPI ID, the opening balance, the folders and all ten foreign keys that point at it.

So a view asks two questions in order: is somebody signed in to the hub, and have they started
Pocket Money. The first is core's; only the second is ours, and only we know what to do about
it — which is why `account_required` exists rather than core's decorator being used directly.
"""
from functools import wraps

from django.shortcuts import redirect
from django.urls import reverse

from core.auth import normalise_phone, split_sign_in_id  # noqa: F401  (re-exported, still used)
from core import auth as hub

from .models import Account

PASSWORD_MIN = hub.PASSWORD_MIN


def profile_for(member):
    """This person's money books, or None if they have never opened Pocket Money."""
    if not member:
        return None
    return Account.objects.filter(member=member, is_active=True, is_archived=False).first()


def current(request):
    """The signed-in account, or None. Re-read every request, so archiving bites at once."""
    return profile_for(hub.current(request))


def account_required(view):
    """Signed in to the hub, and has started Pocket Money.

    Those are two different "no"s and they go to two different places: the hub's sign-in page,
    carrying where you were headed, or the page that offers to start you off.
    """
    @wraps(view)
    def inner(request, *args, **kwargs):
        member = hub.current(request)
        if not member:
            return redirect(f'{reverse("member_login")}?next={request.get_full_path()}')
        account = profile_for(member)
        if not account:
            return redirect('pocket_join')
        request.member = member
        request.account = account
        return view(request, *args, **kwargs)
    return inner
