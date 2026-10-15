# SyncHub (AG-PROJ03)

A Django admin console with **one admin login**, built as a shell that separate feature modules
plug into (chat, health tracker, and so on). The whole look — `app.css`, `app.js`, the Lucide
icon sprite, the sidebar/topbar layout — is taken from **AG-PROJ01 (SyncUp)** unchanged, so every
module looks the same as SyncUp's console.

## The name

**SyncHub** is the platform: the house that holds several apps, each with its own audience, behind
one admin login. It follows the family — SyncUp, GSTSync, AudioSync.

Naming rule for the family:

| Kind | Pattern | Examples |
|---|---|---|
| One app, one purpose, shipped on its own | `<Topic>Sync` | GSTSync, AudioSync |
| The platform that holds several | `SyncHub` | this repo |
| A module inside the platform | plain name, shown as `SyncHub · <Name>` | SyncHub · Chat, SyncHub · Health |

A module takes a `<Topic>Sync` name only when it grows up and ships as its own separate app.

The name lives in `APP_NAME` in `.env`, so changing it is one line.

Right now there is **no feature module**: sign-in, the layout, the dashboard, the UI kit and a
placeholder page per planned module.

## Run it

```
python -m venv venv
venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env            # then edit it
python manage.py migrate
python manage.py createadminuser
python manage.py runserver
```

Open <http://127.0.0.1:8000/> and sign in with `ADMIN_USERNAME` / `ADMIN_PASSWORD` from `.env`.

| Setting in `.env` | What it does |
|---|---|
| `APP_NAME` | The name in the sidebar, page titles and sign-in page |
| `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS` | Standard Django settings |
| `ADMIN_USERNAME`, `ADMIN_EMAIL`, `ADMIN_PASSWORD`, `ADMIN_NAME` | The one admin account |
| `ADMIN_PIN` | Optional 5-character PIN (A-Z, 0-9) for the PIN tab on the sign-in page |

`manage.py createadminuser --reset` sets the password and PIN again for an existing admin.

## Two ways to sign in

Like AG-PROJ01 and AG-PROJ02, the sign-in page has a **Password** tab and a **PIN** tab.

- **Password**: username + password, superusers only.
- **PIN**: five boxes that submit as soon as the fifth character is typed (pasting the whole PIN
  works too). It posts to `/login/pin`, which answers JSON. PINs are stored as salted hashes
  (`core.models.AdminPin`), are not case-sensitive, and five wrong tries from one IP address
  block PIN sign-in for 15 minutes — the password still works.

Both the password and the PIN can be changed from **Profile**.

## Database backup

The sidebar's database icon and the button on **Profile** download the whole SQLite file, named
`ag-proj03_<date>_<time>.sqlite3`. The copy is taken with SQLite's `VACUUM INTO`, so it is a
consistent snapshot even while the app is being used, and it falls back to a plain file copy if
that fails. Admin only. The file holds everything, including the password and PIN hashes, so keep
it private.

## Migrations

`migrations/` is gitignored, the same as in AG-PROJ01 and AG-PROJ02. Each environment creates its
own, so after cloning or pulling a model change, run:

```
python manage.py makemigrations
python manage.py migrate
```

## Putting it on a server

```
git clone … && cd AG-PROJ03
python -m venv venv && venv/bin/pip install -r requirements.txt
cp .env.example .env            # then edit it — see the table below
python manage.py makemigrations # migrations are gitignored, so generate them here
python manage.py migrate
python manage.py createadminuser
python manage.py collectstatic --noinput
python manage.py check          # must pass before the site goes up
```

**`.env` on the server** — `manage.py check` refuses to start without the first one and warns
about the rest:

| Key | Why it matters |
|---|---|
| `SECRET_KEY` | A long random string. Sessions and every signed value depend on it. **Startup fails without it.** |
| `DEBUG=0` | The fallback is already off, so a missing value is safe. Never set it to 1 on a server. |
| `ALLOWED_HOSTS` | `*` — any host name, as in AG-PROJ01 and AG-PROJ02 |
| `CSRF_TRUSTED_ORIGINS` | **Leave empty.** Django rejects `*` outright (`4_0.E001`) and refuses to start. Empty already works on any domain, because Django compares the Origin against the request's own host. Only fill it in if another domain must post into this one. |
| `CRON_KEY` | A long random string. Until it is set, both cron URLs answer 404 and nothing scheduled runs. |
| `ADMIN_*` | The one admin account, used by `createadminuser` |

**Two things Django will not do for you:**

1. **Serve static files.** With `DEBUG=0` Django serves nothing, so point the web server's
   `/static/` at `staticfiles/` (on PythonAnywhere that is the Static files table in the Web tab)
   and re-run `collectstatic` after every deploy. If you skip it, pages load with no CSS — the
   `synchub.W003` warning catches this.
2. **Generate migrations.** `migrations/` is gitignored, as in AG-PROJ01 and AG-PROJ02, so a
   fresh clone has none: run `makemigrations` *before* `migrate`, or nothing gets created.

**Then, in order:** set the cron service (below) → open `/cron/health?key=…` to confirm → fill in
the SyncUp address and notify key on the Hibiscus settings page → add the people and their
history → and only then switch the old Flask tracker off.

## Cron — two jobs for the whole hub

SyncHub has exactly **two** scheduled URLs, however many modules it holds. A module never gets
its own: it writes `<app>/cron.py` with one or both hooks, and `core/apps.py` imports it at
startup, the same way Django finds signals.

```python
from core import cronhub

@cronhub.tick('hibiscus.notify', budget=8)      # every 15 minutes
def notify(ctx): ...

@cronhub.cleanup('hibiscus.retention')          # nightly
def retention(ctx): ...
```

Set these once in an external cron service (times in Asia/Kolkata):

```
every 15 min   GET /cron/tick?key=...
02:15 daily    GET /cron/cleanup?key=...
02:30 Sundays  GET /cron/cleanup?key=...&vacuum=1
(any time)     GET /cron/health?key=...      — read-only, not a scheduled job
```

They are public URLs guarded by `CRON_KEY`: an unset key **closes** them and they answer 404.
A run takes a database lock, so an overlapping call answers `200 {"skipped": "locked"}` rather
than a 5xx that would trip the cron service's alerting. Each hook runs inside its own try/except
with a slice of the run's budget, so one module failing never stops another's work. Every run is
recorded and shown on **System → Cron**, which also has "Run now" buttons.

## Modules

| Module | What it is |
|---|---|
| **Hibiscus** (`/hibiscus/`) | A cycle diary. Admin side inside SyncHub; the diary itself at `/hibiscus/me/` with its own sign-up, session and theme. Was a separate Flask app called Red Hibiscus. |

Hibiscus in short: flow is recorded **per day**, and a period is a *run* of days worked out by
`hibiscus/cycle.py` — no start or end dates are stored. Spotting shows on the calendar but is not
counted as a period day; one missing day inside a run is bridged, two start a new period. People
sign themselves up with an email **or** a phone number. Notifications go out through SyncUp at
times the admin sets, each time of day with its own words, delivered by the hub's 15-minute cron.

## Pages

| URL | Page |
|---|---|
| `/login` | Sign in with a password or a PIN (admin only; anyone who isn't an active superuser is refused) |
| `/login/pin` | JSON endpoint the PIN tab posts to |
| `/` | Dashboard: module cards and counts |
| `/ui/` | UI kit: every component in `app.css`, to copy from when building a module |
| `/profile/` | Admin name, password, PIN and the database backup |
| `/download/sqlite` | Downloads the whole SQLite database (admin only) |
| `/cron/` | The two hub jobs, their hooks and recent runs (admin only) |
| `/hibiscus/` | Hibiscus admin: people, periods, stocks, notifications, settings |
| `/hibiscus/me/` | The diary, for the people who keep it (their own sign-in) |
| `/m/<key>/` | Placeholder for a module that isn't built yet |
| `/django-admin/` | Django's own admin |

## Layout

```
main/            settings, urls, wsgi, asgi
core/            the shell
  models.py        AdminPin (the admin's 5-character PIN, hashed)
  modules.py       the list of modules (sidebar and dashboard read it)
  views.py         sign-in, dashboard, UI kit, profile, placeholder
  forms.py         sign-in and profile forms, styled with app.css classes
  context_processors.py   app name + module list for every template
  templatetags/ui.py      {% icon %}, {% nav_active %}, {% static_v %}, |ago
  templates/       base.html, _sidebar.html, _messages.html, _theme_color.html,
                   auth/login.html, core/*.html, 404.html, 500.html
static/          app.css, app.js, icons.svg, images/app-icon.svg  (copied from AG-PROJ01)
```

`static/images/app-icon.svg` is still SyncUp's mark — replace it when this product gets its own.

## Adding a module

1. `python manage.py startapp chat`
2. Add `'chat.apps.ChatConfig'` to `INSTALLED_APPS` in `main/settings.py`.
3. Add `path('chat/', include('chat.urls'))` in `main/urls.py`.
4. Templates extend the shared shell and use the shared components:

   ```django
   {% extends "base.html" %}
   {% block title %}Chat{% endblock %}
   {% block content %}
   <div class="page-head">
     <div class="titles"><h1>Chat</h1><p class="page-sub">One line about the page</p></div>
     <div class="page-actions"><a class="btn btn-primary" href="#">{% icon "plus" %}New</a></div>
   </div>
   <div class="card"><div class="card-body">…</div></div>
   {% endblock %}
   ```
5. Guard every view with `@admin_required` from `core.views`.
6. Set `url_name` on that module's entry in `core/modules.py`, and the sidebar and dashboard card
   link straight to it instead of the placeholder.

Keep modules independent: no imports between module apps, and each keeps its own models,
templates and URLs.
