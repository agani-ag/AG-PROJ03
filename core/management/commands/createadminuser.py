"""Create (or update) the admin — the only account AG-PROJ03 has — from environment variables.

    ADMIN_USERNAME, ADMIN_EMAIL, ADMIN_PASSWORD   required
    ADMIN_NAME                                    optional display name (defaults to the username)
    ADMIN_PIN                                     optional 5-character sign-in PIN (A-Z, 0-9)

Put them in .env (see .env.example), then:

    python manage.py createadminuser            # creates it, or says it already exists
    python manage.py createadminuser --reset    # also resets the password, name and PIN

The name, password and PIN can be changed afterwards from Profile.
"""
import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from ...models import AdminPin


class Command(BaseCommand):
    help = 'Creates the admin superuser from environment variables'

    def add_arguments(self, parser):
        parser.add_argument('--reset', action='store_true',
                            help='Reset the password and name if the user already exists')

    def handle(self, *args, **options):
        User = get_user_model()
        username = (os.environ.get('ADMIN_USERNAME') or '').strip().lower()
        email = (os.environ.get('ADMIN_EMAIL') or '').strip()
        password = os.environ.get('ADMIN_PASSWORD') or ''
        name = (os.environ.get('ADMIN_NAME') or '').strip()
        pin = (os.environ.get('ADMIN_PIN') or '').strip().upper()

        if not username or not email or not password:
            self.stdout.write(self.style.ERROR(
                'Set ADMIN_USERNAME, ADMIN_EMAIL and ADMIN_PASSWORD in .env first.'))
            return
        if pin and not AdminPin.PATTERN.fullmatch(pin):
            self.stdout.write(self.style.ERROR(
                'ADMIN_PIN must be exactly 5 letters or digits (A-Z, 0-9).'))
            return

        user = User.objects.filter(username=username).first()
        if user and not options['reset']:
            self.stdout.write(self.style.WARNING(
                f"Admin '{username}' already exists. Use --reset to set the password again."))
            return

        if user:
            user.email = email
            user.first_name = name or user.first_name or username.title()
            user.is_staff = user.is_superuser = user.is_active = True
            user.set_password(password)
            user.save()
            self._set_pin(user, pin)
            self.stdout.write(self.style.SUCCESS(
                f"Admin '{username}' updated." + (' PIN set.' if pin else '')))
            return

        user = User.objects.create_superuser(
            username=username, email=email, password=password,
            first_name=name or username.title(),
        )
        self._set_pin(user, pin)
        self.stdout.write(self.style.SUCCESS(
            f"Admin '{username}' created." + (' PIN set.' if pin else '')))

    def _set_pin(self, user, pin):
        if not pin:
            return
        admin_pin = AdminPin.objects.filter(user=user).first() or AdminPin(user=user)
        admin_pin.set_pin(pin)
        admin_pin.save()
