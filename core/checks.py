"""Start-up checks for a hosted SyncHub, run by `manage.py check` and on every deploy.

These exist because the mistakes they catch are silent. A server with the development secret key,
or with no collected static files, starts up and serves pages — it just does it wrongly, and you
find out later. Django's own `check --deploy` covers the standard ground; this covers ours.
"""
from django.conf import settings
from django.core.checks import Error, Warning, register

DEV_SECRET = 'dev-only-insecure-key-change-me'


@register('synchub')
def check_deploy(app_configs, **kwargs):
    problems = []
    if settings.DEBUG:
        return problems                   # these only matter on a hosted server

    if settings.SECRET_KEY in ('', DEV_SECRET):
        problems.append(Error(
            'SECRET_KEY is still the development value.',
            hint='Set SECRET_KEY in .env to a long random string. Sessions and signed values '
                 'are worthless without it.',
            id='synchub.E001',
        ))

    # ALLOWED_HOSTS = ['*'] is deliberate here, as in AG-PROJ01 and AG-PROJ02, so it is not
    # flagged. CSRF_TRUSTED_ORIGINS being empty is also correct: Django compares the Origin
    # against the request's own host, so same-origin posts work on whatever domain this is
    # hosted at. It is only needed when another domain posts into this one — and it can never
    # be "*", which Django rejects outright (4_0.E001).

    static_root = getattr(settings, 'STATIC_ROOT', None)
    if static_root and not (static_root / 'css' / 'app.css').exists():
        problems.append(Warning(
            'Static files have not been collected, so pages will load without CSS.',
            hint='Run "manage.py collectstatic" and point the web server at '
                 f'{static_root} for /static/.',
            id='synchub.W003',
        ))

    if not getattr(settings, 'CRON_KEY', ''):
        problems.append(Warning(
            'CRON_KEY is not set, so /cron/tick and /cron/cleanup answer 404.',
            hint='Set CRON_KEY in .env, then point your cron service at those two URLs. '
                 'Nothing scheduled runs until you do.',
            id='synchub.W004',
        ))

    return problems
