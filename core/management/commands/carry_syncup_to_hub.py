"""Move a module's old SyncUp settings into the hub's, before the columns are dropped.

Why this exists as a command rather than a data migration: migrations are generated per
environment in this project (`migrations/` is gitignored, as in AG-PROJ01 and AG-PROJ02), so a
data step written here on a laptop never reaches the server. The server's own `makemigrations`
would produce a plain column drop — and the SyncUp address somebody had typed in would go with
it, silently, which is the one thing a deploy must not do.

So on a server the order is:

    python manage.py makemigrations
    python manage.py migrate core            # the hub's table exists from here
    python manage.py carry_syncup_to_hub     # copy while the old columns are still there
    python manage.py migrate                 # now the old columns may go

Safe to run at any time and as often as you like: it only reads columns that still exist, and it
never overwrites a value the hub already has.
"""
from django.core.management.base import BaseCommand
from django.db import connection

from core.models import HubSettings

# Where the three values used to live, before they became the hub's.
SOURCES = [
    ('hibiscus_settings', 'Hibiscus'),
    ('j6_settings', 'J6'),
]
FIELDS = ('site_base', 'syncup_base', 'syncup_notify_key')


def _columns(table):
    """The column names of a table, or an empty set when the table is not there."""
    with connection.cursor() as cursor:
        try:
            return {row.name for row in connection.introspection.get_table_description(cursor,
                                                                                       table)}
        except Exception:
            return set()


class Command(BaseCommand):
    help = "Copy a module's old SyncUp settings into the hub's, before the columns are dropped."

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Say what would be carried over, and change nothing.')

    def handle(self, *args, **options):
        hub = HubSettings.load()
        carried = {}

        for table, label in SOURCES:
            present = [f for f in FIELDS if f in _columns(table)]
            if not present:
                self.stdout.write(f'{label}: nothing to carry (columns already gone).')
                continue
            with connection.cursor() as cursor:
                cursor.execute(f'SELECT {", ".join(present)} FROM {table} WHERE id = 1')
                row = cursor.fetchone()
            if not row:
                self.stdout.write(f'{label}: no settings row yet.')
                continue
            for field, value in zip(present, row):
                value = (value or '').strip()
                if value and not getattr(hub, field) and field not in carried:
                    carried[field] = (value, label)

        if not carried:
            self.stdout.write(self.style.SUCCESS('Nothing to carry — the hub already has what '
                                                 'it needs, or there was nothing set.'))
            return

        for field, (value, label) in carried.items():
            shown = value if field != 'syncup_notify_key' else '•' * len(value)
            self.stdout.write(f'  {field} ← {label}: {shown}')
            if not options['dry_run']:
                setattr(hub, field, value)

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('Dry run: nothing was saved.'))
            return
        hub.save()
        self.stdout.write(self.style.SUCCESS(f'Carried {len(carried)} value'
                                             f'{"" if len(carried) == 1 else "s"} into the hub. '
                                             f'Check them at System → Settings.'))
