"""The page itself. One view, and it does as little as possible on purpose."""
from django.db.models import F
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.cache import never_cache

from .models import Page

#: What a sandboxed page may still do. Everything here is about the page's own behaviour;
#: what is deliberately *missing* is `allow-same-origin`, and that is the whole mechanism —
#: without it the browser gives the page an opaque origin of its own, so its scripts cannot
#: read this site's cookies, call its endpoints with somebody's session, or touch any other
#: page here. These pages are public and the HTML is pasted in by hand, so one that was copied
#: from somewhere else should not be able to act as whoever happens to be reading it.
SANDBOX = ('sandbox allow-scripts allow-forms allow-popups allow-popups-to-escape-sandbox '
           'allow-modals allow-downloads')


@never_cache
def show(request, slug):
    """Serve the file exactly as it was pasted.

    No template, no context, no layout. A standalone page that gets rewritten on the way out
    is not standalone any more, and the rewriting is what would break it.
    """
    page = get_object_or_404(Page, slug=slug, is_enabled=True)
    found = page.file
    if found is None:
        # The row outlived the file — a deploy that took one and not the other. Serving the
        # empty string would be a blank page that looks like the page is broken; a 404 says
        # the true thing, and the console's list says which row it was.
        raise Http404(f'pages/html/{page.slug}.html is not on this server')
    Page.objects.filter(pk=page.pk).update(last_opened_at=timezone.now(),
                                           open_count=F('open_count') + 1)
    response = HttpResponse(found.read_text(encoding='utf-8'),
                            content_type='text/html; charset=utf-8')
    if page.sandboxed:
        response['Content-Security-Policy'] = SANDBOX
    return response
