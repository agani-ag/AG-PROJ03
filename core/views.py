"""Sign-in and the shell pages.

AG-PROJ03 has one account: the admin, an active superuser created by
`manage.py createadminuser`. `admin_required` guards every page except sign-in.
"""
import json
import logging
import shutil
import tempfile
from datetime import datetime
from functools import wraps
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.cache import cache
from django.db import connection
from django.http import Http404, HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_http_methods, require_POST

from . import cron_views, cronhub
from .forms import AuthForm, ProfileForm
from .models import AdminPin, CronRun
from .modules import MODULES, MODULES_BY_KEY

# PIN sign-ins don't go through authenticate(), so they have to name the backend.
logger = logging.getLogger(__name__)

MODEL_BACKEND = 'django.contrib.auth.backends.ModelBackend'
PIN_MAX_TRIES = 5            # wrong PINs allowed per IP…
PIN_LOCK_SECONDS = 15 * 60   # …per 15 minutes


def admin_required(view):
    """Signed-in admin only. Anyone else is signed out and sent to the sign-in page."""
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            logout(request)
            return redirect('login')
        return view(request, *args, **kwargs)
    return wrapped


def _after_login(request):
    """The ?next= page when it belongs to this site, else the dashboard."""
    nxt = request.POST.get('next') or request.GET.get('next')
    if nxt and url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()},
                                               require_https=request.is_secure()):
        return redirect(nxt)
    return redirect('dashboard')


@never_cache
def login_view(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return redirect('dashboard')
    form = AuthForm(request, data=request.POST or None)
    if request.method == 'POST':
        if form.is_valid():
            login(request, form.get_user())
            # Unticked: the session ends when the browser closes (SESSION_EXPIRE_AT_BROWSER_CLOSE).
            if request.POST.get('remember'):
                request.session.set_expiry(settings.SESSION_COOKIE_AGE)
            return _after_login(request)
        messages.error(request, 'Wrong username or password.')
    return render(request, 'auth/login.html', {'auth_form': form})


def _client_ip(request):
    forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '')
    return (forwarded.split(',')[0].strip() if forwarded
            else request.META.get('REMOTE_ADDR', '')) or 'unknown'


@never_cache
@require_POST
def pin_auth(request):
    """PIN sign-in, called by the PIN tab on the sign-in page. Answers JSON:
    {"message": …} on success, {"error": …} otherwise."""
    try:
        data = json.loads(request.body or b'{}')
    except (ValueError, TypeError):
        return JsonResponse({'error': 'Invalid request.'}, status=400)

    raw = (data.get('pin') or data.get('passkey') or '') if isinstance(data, dict) else ''
    pin = str(raw).strip().upper()
    if not AdminPin.PATTERN.fullmatch(pin):
        return JsonResponse({'error': 'Enter your 5-character PIN.'}, status=400)

    # Wrong PINs are limited per IP, as in AG-PROJ02's passkey sign-in.
    key = f'pin-tries:{_client_ip(request)}'
    tries = cache.get(key, 0)
    if tries >= PIN_MAX_TRIES:
        return JsonResponse({'error': 'Too many tries. Wait 15 minutes or use your password.'},
                            status=429)

    # PINs are stored hashed, so there's nothing to look up — check each admin's PIN in turn.
    for admin_pin in AdminPin.objects.select_related('user').filter(
            user__is_active=True, user__is_superuser=True):
        if admin_pin.check_pin(pin):
            login(request, admin_pin.user, backend=MODEL_BACKEND)
            AdminPin.objects.filter(pk=admin_pin.pk).update(last_used_at=timezone.now())
            cache.delete(key)
            return JsonResponse({'message': 'Signed in.'})

    cache.set(key, tries + 1, PIN_LOCK_SECONDS)
    left = PIN_MAX_TRIES - (tries + 1)
    return JsonResponse({'error': f'Wrong PIN. {left} tr{"y" if left == 1 else "ies"} left.'
                                  if left > 0 else 'Wrong PIN. Too many tries.'}, status=401)


@require_http_methods(['GET', 'POST'])
def logout_view(request):
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, 'Signed out.')
    return redirect('login')


@admin_required
def dashboard(request):
    live = [m for m in MODULES if m.is_live]
    return render(request, 'core/dashboard.html', {
        'modules': MODULES,
        'live_count': len(live),
        'planned_count': len(MODULES) - len(live),
    })


@admin_required
def module_placeholder(request, key):
    """Stands in for a module until its own app is built, so every nav link leads somewhere."""
    module = MODULES_BY_KEY.get(key)
    if module is None:
        raise Http404('No such module')
    if module.is_live:
        return redirect(module.url_name)
    return render(request, 'core/module_placeholder.html', {'module': module})


@admin_required
def download_sqlite(request):
    """Download the whole SQLite database as a backup, as AG-PROJ01 and AG-PROJ02 do.

    The copy is made with `VACUUM INTO`, so it is a consistent snapshot even if something is
    being written at that moment (copying the file by hand can catch it half-written).
    """
    db = settings.DATABASES['default']
    db_path = Path(str(db['NAME']))
    if 'sqlite3' not in db['ENGINE'] or not db_path.exists():
        messages.error(request, 'No SQLite database to download.')
        return redirect('profile')

    filename = timezone.localtime().strftime('ag-proj03_%Y%m%d_%H%M%S.sqlite3')
    tmp_dir = Path(tempfile.mkdtemp(prefix='ag3-backup-'))
    snapshot = tmp_dir / filename
    try:
        with connection.cursor() as cursor:
            cursor.execute(f"VACUUM INTO '{snapshot.as_posix()}'")
        data = snapshot.read_bytes()
    except Exception:                      # noqa: BLE001 - fall back to the plain file copy
        logger.exception('VACUUM INTO failed; sending the database file as it is')
        data = db_path.read_bytes()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    response = HttpResponse(data, content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    response['Content-Length'] = str(len(data))
    return response


@admin_required
def cron_page(request):
    """What the two hub jobs are, which modules plug into them, and how the last runs went."""
    return render(request, 'core/cron.html', {
        'tick_hooks': cronhub.hooks(cronhub.TICK),
        'cleanup_hooks': cronhub.hooks(cronhub.CLEANUP),
        'runs': CronRun.objects.all()[:20],
        'last_tick': CronRun.objects.filter(job=cronhub.TICK).first(),
        'last_cleanup': CronRun.objects.filter(job=cronhub.CLEANUP).first(),
        'key_set': bool(settings.CRON_KEY),
        'tick_url': request.build_absolute_uri(reverse('cron_tick')),
        'cleanup_url': request.build_absolute_uri(reverse('cron_cleanup')),
    })


@admin_required
@require_POST
def cron_run_now(request, job):
    """Run one of the two jobs from the page, for when you don't want to wait for the schedule."""
    if job not in (cronhub.TICK, cronhub.CLEANUP):
        raise Http404('No such job')
    budget = (cron_views.TICK_BUDGET_SECONDS if job == cronhub.TICK
              else cron_views.CLEANUP_BUDGET_SECONDS)
    payload, _ = cron_views.run_job(job, budget, triggered_by='admin')
    if payload.get('skipped'):
        messages.warning(request, f'{job}: already running, so this run was skipped.')
    elif payload.get('ok'):
        messages.success(request, f'{job}: done in {payload.get("ms", 0)} ms, '
                                  f'{payload.get("items", 0)} item(s).')
    else:
        messages.error(request, f'{job} failed: {payload.get("error") or "see the run below"}')
    return redirect('cron_page')


@admin_required
def ui_kit(request):
    """Every component in app.css on one page, to copy from when building a module."""
    return render(request, 'core/ui_kit.html')


@admin_required
def profile(request):
    user = request.user
    form = ProfileForm(request.POST or None, user=user,
                       initial={'name': user.first_name or user.username})
    if request.method == 'POST' and form.is_valid():
        user.first_name = form.cleaned_data['name']
        new_password = form.cleaned_data.get('new_password')
        new_pin = form.cleaned_data.get('new_pin')
        if new_password:
            user.set_password(new_password)
        user.save()

        saved = ['Profile']
        if new_pin:
            admin_pin, _ = AdminPin.objects.get_or_create(
                user=user, defaults={'pin_hash': ''})
            admin_pin.set_pin(new_pin)
            admin_pin.save()
            saved.append('PIN')
        if new_password:
            update_session_auth_hash(request, user)   # stay signed in after a password change
            saved.append('password')
        if len(saved) == 1:
            messages.success(request, 'Profile saved.')
        else:
            messages.success(request, ', '.join(saved[:-1]) + ' and ' + saved[-1] + ' saved.')
        return redirect('profile')
    db_path = Path(str(settings.DATABASES['default']['NAME']))
    stat = db_path.stat() if db_path.exists() else None
    return render(request, 'core/profile.html', {
        'form': form,
        'admin_pin': AdminPin.objects.filter(user=user).first(),
        'db_name': db_path.name,
        'db_size': stat.st_size if stat else None,
        'db_changed': (datetime.fromtimestamp(stat.st_mtime, tz=timezone.get_current_timezone())
                       if stat else None),
    })
