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

Generated migrations are gitignored, the same as in AG-PROJ01 and AG-PROJ02. Each environment
creates its own, so after cloning or pulling a model change, run:

```
python manage.py makemigrations
python manage.py migrate
```

No app labels needed — that bare command covers every installed app. It only works because each
app's empty `migrations/__init__.py` **is** committed; Django treats an app with no `migrations/`
folder as unmigrated and silently skips it, which is why a bare run would otherwise answer
"No changes detected" and force `makemigrations core hibiscus`. The two `.gitignore` lines that
keep this true:

```
**/migrations/*
!**/migrations/__init__.py
```

**Adding a module app?** Create `<app>/migrations/__init__.py` along with it, or bare
`makemigrations` will leave that app out.

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
   `/static/` at **`staticfiles/`** (on PythonAnywhere that is the Static files table in the Web
   tab) and re-run `collectstatic` after every deploy. It must be `staticfiles/`, not the
   project's own `static/`: that folder holds the shared console files only, so mapping it serves
   `css/app.css` and 404s everything an app ships with — the console then looks perfect while
   `/hibiscus/me/` renders as plain text. `manage.py check` looks for both kinds of file and
   warns with `synchub.W003`.
2. **Generate migrations.** Migration files are gitignored, as in AG-PROJ01 and AG-PROJ02, so a
   fresh clone has none: run `makemigrations` *before* `migrate`, or nothing gets created. Plain
   `makemigrations` is enough — it picks up every installed app.

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
| **J6** (`/j6/`) | Chat with no sign-up. Console side inside SyncHub; the chat itself at `/j6/me/`, plus a portable Windows client in [desktop/](desktop/). |

Hibiscus in short: flow is recorded **per day**, and a period is a *run* of days worked out by
`hibiscus/cycle.py` — no start or end dates are stored. Spotting shows on the calendar but is not
counted as a period day; one missing day inside a run is bridged, two start a new period. People
sign themselves up with an email **or** a phone number. Notifications go out through SyncUp at
times the admin sets, each time of day with its own words, delivered by the hub's 15-minute cron.
Arriving at the diary — signing in, opening a magic link, or the first page of a new day on a
session left open — fires one **browser notification** with where the cycle stands, the way the
old Flask app did it: a one-shot session flag, popped by the first page that asks
(`/hibiscus/me/reminder`). Moving around the diary never re-fires it. It is the plain Notification
API, which the SyncUp app answers for the page, so the same code shows a real Android
notification inside the app and a browser one outside it; where permission has not been given,
the line is shown in the page instead.

A person's page has a **Test push** button that sends one real notification there and then, and
reports what SyncUp answered — the device token, the SyncUp address and the notify key are each
only proved by a delivery. Tapping any notification opens the diary, using *this site's address*
from the module settings; the first Test push fills that in from the page it was pressed on.

The admin can also hand someone a **magic link** (`/hibiscus/me/k/<key>`): opening it signs that
person in with no password and **no expiry**, which is the point — one tap from a bookmark or a
chat message. The link is therefore the credential, so there is one key per person (making a new
one retires the old link immediately), an off switch, a refusal for suspended or archived
accounts, and an audit row for every use. The key never stays in the address bar: the view signs
them in and redirects.

J6 in short: you give a **name** and get two permanent IDs — an **Account ID** to hand out and a
**Self-ID** to come back with. No email, no password, no recovery: lose the Self-ID and you make
a new account. Chats are one-to-one, text only, and any **public media link plays in the bubble**
— the browser probes the URL and draws a picture, a clip or an audio player, or leaves it a link.
Nothing is ever uploaded; a message with a picture in it is a message with a URL in its body.
Delivery is polling (3s focused, 8s for the list, 20s hidden, `304` when nothing changed), because
the host cannot do WebSockets. The console reads **by account**: pick a person, see their chats,
open one as they see it. Messages are kept 90 days and idle accounts 180, both by SyncHub's
nightly cron.

The same server also answers `/j6/api/` for non-browser clients, authenticated by the Self-ID in
an `X-J6-Self` header — that is what the Windows app and the terminal client speak.

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
| `/hibiscus/me/k/<key>` | Magic link: signs that person in and redirects, no password, no expiry |
| `/j6/` | J6 console: accounts → one account's chats → one conversation, read only |
| `/j6/me/` | The chat itself (its own session) |
| `/j6/s/<self-id>` | One-tap sign-in link, also how the Windows app hands its account to the page |
| `/j6/api/…` | `start · hello · poll · send · new · read · version` for desktop and terminal clients |
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
  hibiscus/        user.css, user.js, flower.svg — the diary's own assets
  j6/              user.css, user.js — the chat's own
desktop/         the Windows client: one portable j6.exe that is a window and a terminal
```

**Every asset lives in this one `static/` folder, including a module's own** — not in
`<app>/static/`, which is Django's other convention. The reason is operational: a server whose
`/static/` points at `static/` instead of `staticfiles/` serves a project-level file fine and
404s anything that only appears after `collectstatic`, which is how the diary once came up as
unstyled plain text while the console looked perfect. Keeping assets here means both mappings
work. (`collectstatic` is still required — Django's own admin pages have assets of their own.)

SyncHub's mark is the **repeat arrow** — one loop that comes back round, which is what the hub
does: the same two cron jobs, every 15 minutes, for every module. It is **black and white**, the
same treatment as the SyncUp app icon, so the family looks like a family.
`static/images/app-icon.svg` is the only copy: the favicon, the sidebar badge and the sign-in
page all point at that one file. A module's own mark goes in `core/modules.py` — Hibiscus uses
`flower`, the same flower its own pages and favicon carry.
`static/icons.svg` is Lucide and is meant to be regenerated rather than hand-edited; the one
exception is that flower, which is ours and is marked as such at the end of the file.

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

7. If the module needs CSS or images of its own, put them in `static/<module>/` — the project
   folder, not `<app>/static/`. See the note under **Layout** for why. A page-sized scrap of CSS
   (under a kilobyte or so, used by one page) is better inlined in a `{% block head %}` than
   given a file: one request fewer, and nothing to collect.

Keep modules independent: no imports between module apps, and each keeps its own models,
templates and URLs.
