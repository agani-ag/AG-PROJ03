from django.apps import AppConfig


class GamesConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'games'
    verbose_name = 'Games'

    def ready(self):
        # Registers this module's two hooks with SyncHub's cron hub. Games owns no cron URL.
        from . import cron  # noqa: F401
