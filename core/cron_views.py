"""The two cron URLs — mounted at /cron/ (see core/urls.py).

An external cron service calls these on a schedule; there is no in-process scheduler. Such a
service can and will call an endpoint twice (it retries on timeout, and two ticks overlap whenever
a run outlasts its interval), so both endpoints are:

  * secret-gated  — public URLs, guarded by settings.CRON_KEY,
  * single-flight — a database lock stops overlapping runs,
  * idempotent    — the module hooks claim work before doing it,
  * bounded       — a run stops at a wall-clock budget and reports what is left.

Suggested schedule (Asia/Kolkata):

    every 15 min   GET /cron/tick?key=...
    02:15 daily    GET /cron/cleanup?key=...
    02:30 Sundays  GET /cron/cleanup?key=...&vacuum=1
    (any time)     GET /cron/health?key=...      — read-only, not a scheduled job

Shaped after AG-PROJ01's mobileapi/cron_views.py and AG-PROJ02's gstbillingapp/cron_views.py.
"""
import hmac
import logging
from functools import wraps

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import cronhub
from .models import CronLock, CronRun

logger = logging.getLogger(__name__)

# Longer than any single run, short enough that a crashed run frees the lock soon after.
LOCK_TTL_SECONDS = 15 * 60

# Stop claiming new work after this long. Cron services typically abort the call near 30 s.
TICK_BUDGET_SECONDS = 20
CLEANUP_BUDGET_SECONDS = 50       # nightly, nothing waiting on it, so it may take longer


def _authorized(request):
    expected = getattr(settings, 'CRON_KEY', None)
    if not expected:
        return False                   # unset key = endpoints closed, never open
    given = request.headers.get('X-Cron-Key') or request.GET.get('key', '')
    return hmac.compare_digest(str(given), str(expected))


def cron_endpoint(view):
    """Gate a view on the shared cron secret. GET and POST both allowed — plenty of cron
    services only issue GET."""
    @wraps(view)
    def inner(request, *args, **kwargs):
        if not _authorized(request):
            # 404 rather than 403: don't confirm the endpoint exists to anyone scanning.
            return HttpResponse(status=404)
        return view(request, *args, **kwargs)
    return csrf_exempt(require_http_methods(['GET', 'POST'])(inner))


def run_job(job, budget_seconds, triggered_by='cron', **options):
    """Take the lock, run every hook for `job`, write the run row. Returns (payload, status)."""
    if not CronLock.acquire(job, LOCK_TTL_SECONDS):
        # A previous run is still going. Normal operation, not a failure — a 5xx here would set
        # off the cron service's alerting for nothing.
        return {'ok': True, 'skipped': 'locked', 'job': job}, 200

    run = CronRun.objects.create(job=job, triggered_by=triggered_by)
    try:
        summary = cronhub.run(job, budget_seconds=budget_seconds,
                              triggered_by=triggered_by, **options)
        run.finished_at = timezone.now()
        run.duration_ms = summary.get('ms', 0)
        run.ok = summary.get('ok', True)
        run.items = summary.get('items', 0)
        run.detail = summary.get('hooks', {})
        if not run.ok:
            first = next((d.get('error') for d in run.detail.values() if d.get('error')), '')
            run.error = str(first)[:500]
        run.save()
        return summary, (200 if run.ok else 500)
    except Exception as exc:                                       # noqa: BLE001
        logger.exception('cron job %s failed', job)
        run.finished_at = timezone.now()
        run.ok = False
        run.error = f'{type(exc).__name__}: {exc}'[:500]
        run.save()
        return {'ok': False, 'job': job, 'error': run.error}, 500
    finally:
        CronLock.release(job)


@cron_endpoint
def tick(request):
    """Every 15 minutes: due notifications, scheduled messages, activity tracking."""
    payload, status = run_job(cronhub.TICK, TICK_BUDGET_SECONDS)
    return JsonResponse(payload, status=status)


@cron_endpoint
def cleanup(request):
    """Nightly: retention and housekeeping. `?vacuum=1` also compacts the database."""
    payload, status = run_job(
        cronhub.CLEANUP, CLEANUP_BUDGET_SECONDS,
        vacuum=request.GET.get('vacuum') in ('1', 'true', 'yes'),
    )
    return JsonResponse(payload, status=status)


@cron_endpoint
def health(request):
    """Read-only: call this first when wiring up the cron service."""
    def last(job):
        row = CronRun.objects.filter(job=job).first()
        if not row:
            return None
        return {'at': row.started_at.isoformat(), 'ok': row.ok,
                'items': row.items, 'ms': row.duration_ms}

    return JsonResponse({
        'ok': True,
        'now': timezone.now().isoformat(),
        'hooks': {
            'tick': [h.name for h in cronhub.hooks(cronhub.TICK)],
            'cleanup': [h.name for h in cronhub.hooks(cronhub.CLEANUP)],
        },
        'last_run': {'tick': last(cronhub.TICK), 'cleanup': last(cronhub.CLEANUP)},
    })
