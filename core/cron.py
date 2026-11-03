"""SyncHub's own cron hooks — the shell's share of the two jobs.

Modules add their own `<app>/cron.py`; this is core's. Nothing here is specific to a feature.
"""
import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.sessions.models import Session
from django.db import connection
from django.utils import timezone

from . import cronhub
from .models import CronRun

logger = logging.getLogger(__name__)

# How long to keep the cron run log. Long enough to see a weekly job's history, short enough that
# the table never needs thinking about.
KEEP_RUNS_DAYS = 30


@cronhub.tick('core.syncup_people', budget=8)
def syncup_people(ctx):
    """Copy SyncUp's people in, before the modules push to them.

    Order is free rather than arranged: `CoreConfig.ready()` calls `autodiscover_modules`,
    which follows INSTALLED_APPS, and core is listed above hibiscus, j6 and pocket. So this
    hook registers first and runs first, and a person who arrived in SyncUp a minute ago can
    be pushed to in the same tick they are copied in.
    """
    from . import syncup_people as people
    why = people.problem()
    if why:
        return {'items': 0, 'skipped': why}
    return people.run(ctx)


@cronhub.cleanup('core.sessions')
def expired_sessions(ctx):
    """Drop expired session rows. Django never does this on its own."""
    deleted, _ = Session.objects.filter(expire_date__lt=timezone.now()).delete()
    return {'items': deleted, 'sessions_deleted': deleted}


@cronhub.cleanup('core.cron_log')
def trim_run_log(ctx):
    """Keep the run log from growing for ever."""
    cutoff = timezone.now() - timedelta(days=KEEP_RUNS_DAYS)
    deleted, _ = CronRun.objects.filter(started_at__lt=cutoff).delete()
    return {'items': deleted, 'runs_deleted': deleted}


@cronhub.cleanup('core.vacuum', budget=40)
def vacuum(ctx, vacuum=False, **_):
    """Compact SQLite, but only when asked (`/cron/cleanup?vacuum=1`) — it rewrites the file."""
    if not vacuum:
        return {'skipped': 'not requested'}
    if 'sqlite' not in settings.DATABASES['default']['ENGINE']:
        return {'skipped': 'not sqlite'}
    with connection.cursor() as cursor:
        cursor.execute('VACUUM')
    return {'vacuumed': True}
