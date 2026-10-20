"""Put the names of accounts that already exist into capitals.

New ones are handled by `validate_name`, which every door into the app goes through. This is for
the rows that were created before that rule existed — and it is a command rather than a data
migration because migrations are generated per environment in this project, so one written on a
laptop never reaches the server.

    python manage.py uppercase_names            # do it
    python manage.py uppercase_names --dry-run  # say what would change

Safe to run as often as you like: a name already in capitals is left alone.
"""
from django.core.management.base import BaseCommand

from j6.models import Account


class Command(BaseCommand):
    help = 'Put existing J6 account names into capitals.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='List what would change, and change nothing.')

    def handle(self, *args, **options):
        changed = [a for a in Account.objects.all() if a.name != a.name.upper()]
        if not changed:
            self.stdout.write(self.style.SUCCESS('Every name is already in capitals.'))
            return

        for account in changed:
            self.stdout.write(f'  {account.name}  →  {account.name.upper()}')
            if not options['dry_run']:
                account.name = account.name.upper()
                account.save(update_fields=['name'])

        if options['dry_run']:
            self.stdout.write(self.style.WARNING(f'Dry run: {len(changed)} would change.'))
        else:
            self.stdout.write(self.style.SUCCESS(f'{len(changed)} name'
                                                 f'{"" if len(changed) == 1 else "s"} updated.'))
