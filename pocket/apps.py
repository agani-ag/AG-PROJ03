from django.apps import AppConfig


class PocketConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'pocket'
    verbose_name = 'Pocket Money'

    def ready(self):
        # Registers the tick and cleanup hooks with the hub's cron. No URL of its own.
        from . import cron  # noqa: F401
