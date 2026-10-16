from django.apps import AppConfig


class J6Config(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'j6'
    verbose_name = 'J6'

    def ready(self):
        # Registers this module's two hooks with SyncHub's cron hub. J6 owns no cron URL.
        from . import cron  # noqa: F401
