"""Standalone HTML, served from this folder.

Drop a `.html` file into `pages/templates/pages/html/`, commit, pull — it is live at
`/p/<name>`. There is no model, no admin screen and nothing to switch on: the file being in
the repository *is* the decision, the same way a line of code is.

**The file is served byte for byte.** No template engine, no layout, no rewriting. A page
containing `{{ x }}` or `{% y %}` gets those characters back. That is the whole point of a
standalone page — one that is quietly templated on the way out has stopped being the thing
that was committed, and it stops in a way nobody notices until it misbehaves in front of
somebody.

**Why not `static/`.** The web server serves that folder straight off the checkout, which
would work — but these are pages, not assets. A page belongs at a readable address with a
view that can say 404, not at `/static/photo-stamp.html`.

They are public, like the rest of `static/`. Anything carrying a key or spending somebody's
money does not belong in here.
"""
import html as htmllib
import re
from datetime import datetime, timezone as dt_timezone
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse
from django.shortcuts import render

from core.views import admin_required

#: Where the pages live. Under `templates/` by choice, next to the console's own template —
#: Django's loader never touches them, because nothing here renders one by name. They are
#: read off disk and sent as they are.
HTML_DIR = Path(settings.BASE_DIR) / 'pages' / 'templates' / 'pages' / 'html'


TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)
#: Enough of a file to find its <title> without reading a 40 KB page to list it.
HEAD_BYTES = 4096


def _title_of(path):
    """The page's own <title>, or '' — so the list reads like the pages do."""
    try:
        with path.open(encoding='utf-8', errors='replace') as handle:
            found = TITLE.search(handle.read(HEAD_BYTES))
    except OSError:                         # pragma: no cover - unreadable file
        return ''
    return htmllib.unescape(' '.join(found.group(1).split()))[:120] if found else ''


def listing():
    """Every `.html` in `pages/`, newest edit first."""
    if not HTML_DIR.is_dir():
        return []
    out = []
    for path in sorted(HTML_DIR.glob('*.html')):
        stat = path.stat()
        out.append({
            'name': path.stem,
            'file': path.name,
            'title': _title_of(path),
            'size_kb': round(stat.st_size / 1024, 1),
            'changed': datetime.fromtimestamp(stat.st_mtime, dt_timezone.utc),
            'path': f'/p/{path.stem}',
        })
    return sorted(out, key=lambda r: r['changed'], reverse=True)


@admin_required
def index(request):
    """System → Pages. A list of what is in the folder, and nothing to press.

    Read-only on purpose: there is no state here to change. The file is the decision, so the
    console's job is to answer "what is there and what is its address" — which is otherwise
    an ls and some guessing.
    """
    rows = listing()
    for row in rows:
        row['link'] = request.build_absolute_uri(row['path'])
    return render(request, 'pages/index.html',
                  {'rows': rows, 'folder': HTML_DIR,
                   'total_kb': round(sum(r['size_kb'] for r in rows), 1)})


def show(request, name):
    """`/p/<name>` → `pages/templates/pages/html/<name>.html`, exactly as committed."""
    found = (HTML_DIR / f'{name}.html').resolve()
    # Proved rather than trusted: the URL pattern already forbids a dot or a slash, but this
    # is the one place a path is built from something somebody typed.
    if found.parent != HTML_DIR.resolve() or not found.is_file():
        raise Http404(f'There is no pages/templates/pages/html/{name}.html')
    # Bytes, not text. Reading it as text would decode and re-encode it, and Python's
    # universal newlines would quietly turn a CRLF file into an LF response — which is how
    # "served exactly as committed" stops being true on a Windows checkout without anybody
    # noticing. Whatever is in the file is what goes out.
    return HttpResponse(found.read_bytes(), content_type='text/html; charset=utf-8')
