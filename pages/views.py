"""Standalone HTML, served from this folder.

Drop a `.html` file into `pages/`, commit, pull, Reload — it is live at `/p/<name>`. There is
no model, no admin screen and nothing to switch on: the file being in the repository *is* the
decision, the same way a line of code is.

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
from pathlib import Path

from django.conf import settings
from django.http import Http404, HttpResponse

#: The folder this module lives in. The `.html` files sit beside this file.
HTML_DIR = Path(settings.BASE_DIR) / 'pages'


def show(request, name):
    """`/p/<name>` → `pages/<name>.html`, exactly as committed."""
    found = (HTML_DIR / f'{name}.html').resolve()
    # Proved rather than trusted: the URL pattern already forbids a dot or a slash, but this
    # is the one place a path is built from something somebody typed.
    if found.parent != HTML_DIR.resolve() or not found.is_file():
        raise Http404(f'There is no pages/{name}.html')
    return HttpResponse(found.read_text(encoding='utf-8'),
                        content_type='text/html; charset=utf-8')
