from django.apps import AppConfig
from django.utils.module_loading import autodiscover_modules


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'core'
    verbose_name = 'Core shell'

    def ready(self):
        # Import `<app>/cron.py` for every installed app, so a module registers its cron hooks
        # just by having the file — the same way Django finds signals.
        autodiscover_modules('cron')
