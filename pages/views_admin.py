"""System → Pages. A list of the files, and a switch beside each one.

The list is built from **both** sides — the rows in the database and the files on disk — and
says plainly when the two disagree. That is the whole job of this screen: a file nobody has
set up is invisible otherwise, and a row whose file went away in a deploy would 404 with
nothing to explain why.
"""
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from core import syncup_links
from core.views import admin_required

from .forms import PageForm
from .models import Page, files_on_disk


def _rows():
    """Every page, with the state of its file, plus any file with no page yet."""
    found = files_on_disk()
    rows = []
    for page in Page.objects.all():
        rows.append({'page': page, 'file': found.get(page.slug)})
    spare = sorted(set(found) - {p['page'].slug for p in rows})
    return rows, spare


@admin_required
def index(request):
    rows, spare = _rows()
    return render(request, 'pages/admin/index.html', {
        'rows': rows,
        'spare': spare,
        'live': sum(1 for r in rows if r['page'].is_ready),
        'missing': sum(1 for r in rows if r['file'] is None),
        'in_syncup': sum(1 for r in rows if r['page'].in_syncup),
    })


@admin_required
def new(request):
    """Set up a file that is already on this server.

    `?file=<slug>` fills it in from the list, which is how it is normally reached — typing a
    slug that has no file is refused by the form rather than making a row that cannot work.
    """
    initial = {}
    picked = (request.GET.get('file') or '').strip()
    if picked:
        initial = {'slug': picked, 'title': picked.replace('-', ' ').replace('_', ' ').title()}
    form = PageForm(request.POST or None, initial=initial)
    if request.method == 'POST' and form.is_valid():
        page = form.save(commit=False)
        page.created_by = request.user
        page.save()
        messages.success(request, f'{page.title} is at {page.path}'
                                  f'{"" if page.is_enabled else " — switched off for now"}.')
        return redirect('pages')
    return render(request, 'pages/admin/form.html',
                  {'form': form, 'adding': True, 'spare': _rows()[1]})


@admin_required
def edit(request, pk):
    page = get_object_or_404(Page, pk=pk)
    form = PageForm(request.POST or None, instance=page)
    if request.method == 'POST' and form.is_valid():
        form.save()
        messages.success(request, f'{page.title} saved.')
        return redirect('pages')
    return render(request, 'pages/admin/form.html', {'form': form, 'page': page})


@require_POST
@admin_required
def toggle(request, pk, what):
    """The switches, from the list, without opening the page's own screen."""
    page = get_object_or_404(Page, pk=pk)
    if what not in ('enabled', 'syncup', 'sandbox'):
        return redirect('pages')
    field = {'enabled': 'is_enabled', 'syncup': 'in_syncup', 'sandbox': 'sandboxed'}[what]
    setattr(page, field, not getattr(page, field))
    page.save(update_fields=[field])

    if what == 'enabled':
        messages.success(request, f'{page.title} is {"on" if page.is_enabled else "off"}'
                                  f'{"" if page.is_enabled else " — /p/" + page.slug + " now answers 404"}.')
        # A page switched off while its link is live in everybody's app is the one case worth
        # chasing: the link stays until somebody sends again, so say so rather than leave it.
        if not page.is_enabled and syncup_links.sent_before(page.link_subject):
            messages.warning(request, 'Its link is still in the SyncUp app. Hide it at '
                                      'System → Settings → Tools in SyncUp.')
    elif what == 'syncup':
        messages.success(request, 'Added to the SyncUp list — send it at System → Settings.'
                                  if page.in_syncup else
                                  'Taken off the SyncUp list. Hide the link at System → '
                                  'Settings if it has already been sent.')
    else:
        messages.success(request, f'{page.title} now runs '
                                  f'{"sandboxed" if page.sandboxed else "on this site’s origin"}.')
    return redirect('pages')


@require_POST
@admin_required
def delete(request, pk):
    """Forget the page. The file stays where it is — this is a row, not a repository."""
    page = get_object_or_404(Page, pk=pk)
    name, had_link = page.title, syncup_links.sent_before(page.link_subject)
    page.delete()
    messages.success(request, f'{name} is no longer set up. pages/html/{page.slug}.html is '
                              'untouched, and it is back in the list below as a file you '
                              'can set up again.')
    if had_link:
        messages.warning(request, 'Its link is still in the SyncUp app — hide it at System → '
                                  'Settings → Tools in SyncUp.')
    return redirect('pages')
