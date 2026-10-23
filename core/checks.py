"""Start-up checks for a hosted SyncHub, run by `manage.py check` and on every deploy.

These exist because the mistakes they catch are silent. A server with the development secret key,
or with its static files missing, starts up and serves pages — it just does it wrongly, and you
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

    # One file per kind: the console's, a module's, and the sprite. The web server serves the
    # project's static/ folder straight from the checkout (as AG-PROJ01 does), so there is no
    # collectstatic step — a git pull and a Reload are the whole deploy. This looks for them there.
    static_dir = settings.BASE_DIR / 'static'
    expected = ['css/app.css', 'icons.svg', 'hibiscus/user.css', 'j6/user.css']
    missing = [f for f in expected if not (static_dir / f).exists()]
    if missing:
        problems.append(Warning(
            'Static files are missing from the project static/ folder, so pages will load '
            'without CSS: ' + ', '.join(missing) + '.',
            hint=(f"git pull again, and map the web server's /static/ to {static_dir} "
                  "(and /static/admin/ to Django's own admin static folder — see "
                  "docs/pythonanywhere-deploy.html, step 7)."),
            id='synchub.W003',
        ))

    # The hazard of the move away from collectstatic, and it is a silent one: a server whose
    # /static/ still points at staticfiles/ keeps serving the copy that was collected there, so
    # the site stays up and simply stops picking up new CSS. Nothing in Django can see the web
    # server's mapping — what it can see is the stale folder sitting there, which is the only
    # reason that mapping could still be working. Deleting it makes a wrong mapping fail loudly.
    collected = settings.BASE_DIR / 'staticfiles'
    if collected.is_dir():
        problems.append(Warning(
            'staticfiles/ is still here, left by an old collectstatic.',
            hint=(f"Delete {collected}, and point the web server's /static/ at {static_dir} "
                  "instead. While both exist, a mapping still aimed at staticfiles/ serves "
                  "whatever was last collected and quietly ignores everything you deploy."),
            id='synchub.W005',
        ))

    if not getattr(settings, 'CRON_KEY', ''):
        problems.append(Warning(
            'CRON_KEY is not set, so /cron/tick and /cron/cleanup answer 404.',
            hint='Set CRON_KEY in .env, then point your cron service at those two URLs. '
                 'Nothing scheduled runs until you do.',
            id='synchub.W004',
        ))

    return problems
