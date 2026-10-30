"""Standalone HTML pages, shipped as files and switched on from the console.

Years of single-file HTML tools — a photo stamper, a Bedrock console, a gallery, an explainer
— live in folders on a laptop and are useful to nobody there. This is where they go: drop the
file into `pages/html/`, pull, and give it a row with a switch beside it.

**The file is the page.** Nothing here templates it, wraps it in a layout or rewrites its
markup; what is on disk is what the browser gets, byte for byte. That is the point of a
standalone page, and it is why these are not Django templates — a template would need a view,
a context and an opinion about the layout, which is three more reasons for a page to break.

**Why `pages/html/` and not `static/`.** Since the move away from collectstatic the web server
serves `static/` straight off the checkout, so anything in there is public the moment it is
pulled, whatever this console says. These files are read by a view instead, which is what
makes the on/off switch mean something.
"""
from pathlib import Path

from django.conf import settings
from django.contrib.auth.models import User
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from core.syncup_links import LinkSubject

#: Where the files live. Deployed by `git pull` like any other code.
HTML_DIR = Path(settings.BASE_DIR) / 'pages' / 'html'

#: SyncUp's own icon list (mobileapi/link_icons.py), the same names the modules use.
ICONS = [('link', 'Link'), ('tool', 'Tool'), ('camera', 'Camera'), ('image', 'Image'),
         ('video', 'Video'), ('chat', 'Chat'), ('doc', 'Document'), ('chart', 'Chart'),
         ('cloud', 'Cloud'), ('code', 'Code'), ('flower', 'Flower'), ('wallet', 'Wallet')]
COLOURS = [('slate', 'Slate'), ('rose', 'Rose'), ('teal', 'Teal'), ('amber', 'Amber'),
           ('blue', 'Blue'), ('green', 'Green'), ('violet', 'Violet'), ('orange', 'Orange'),
           ('cyan', 'Cyan')]

slug_chars = RegexValidator(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$',
                            'Lowercase letters, numbers and hyphens, starting and ending with '
                            'a letter or a number.')


def files_on_disk():
    """Every `.html` in `pages/html/`, by slug. Sorted, so the console list does not jump."""
    if not HTML_DIR.is_dir():
        return {}
    return {p.stem: p for p in sorted(HTML_DIR.glob('*.html')) if p.is_file()}


class Page(models.Model):
    """One file in `pages/html/`, at `/p/<slug>`.

    The row is the switch and the wording; the file is the page. Deleting the row leaves the
    file alone — it stops being served, and the console offers it again as something to set up.
    """

    slug = models.SlugField(
        max_length=48, unique=True, validators=[slug_chars],
        help_text='Both the address (/p/&lt;slug&gt;) and the file name '
                  '(pages/html/&lt;slug&gt;.html). Changing it breaks any link already handed '
                  'out, including the one in SyncUp.')
    title = models.CharField(max_length=80)
    description = models.CharField(
        max_length=120, blank=True,
        help_text='One line, shown under the title here and in the SyncUp app.')

    icon = models.CharField(max_length=20, choices=ICONS, default='link')
    colour = models.CharField(max_length=20, choices=COLOURS, default='slate')

    is_enabled = models.BooleanField(
        default=True, help_text='Off and the address answers 404 — and the SyncUp link, if '
                                'there is one, is hidden the next time you send.')
    in_syncup = models.BooleanField(
        'Link in SyncUp', default=False,
        help_text='Offer this page as a General link in the SyncUp app, alongside the '
                  'modules. It still has to be sent from System → Settings.')
    sandboxed = models.BooleanField(
        'Run sandboxed', default=False,
        help_text='Gives the page an origin of its own, so its scripts cannot reach anything '
                  'else on this site. These files are in the repository and reviewed like any '
                  'other code, so it is off by default — turn it on for one you did not write. '
                  'A sandboxed page has no localStorage, and an API that refuses a null Origin '
                  'will refuse it.')

    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name='+')
    last_opened_at = models.DateTimeField(null=True, blank=True)
    open_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort_order', 'title']

    def __str__(self):
        return self.title

    @property
    def path(self):
        return f'/p/{self.slug}'

    @property
    def file(self):
        """The file this row names, or None.

        Resolved and then checked to be inside `pages/html/`. The slug validator already
        forbids a dot or a slash, but a path built from stored text is worth proving rather
        than trusting — this is the one place a row reaches the filesystem.
        """
        candidate = (HTML_DIR / f'{self.slug}.html').resolve()
        if candidate.parent != HTML_DIR.resolve() or not candidate.is_file():
            return None
        return candidate

    @property
    def html(self):
        found = self.file
        return found.read_text(encoding='utf-8') if found else ''

    @property
    def is_ready(self):
        """Switched on *and* the file is actually there after the last deploy."""
        return self.is_enabled and self.file is not None

    @property
    def size_kb(self):
        found = self.file
        return round(found.stat().st_size / 1024, 1) if found else 0

    @property
    def link_key(self):
        """Namespaced so a page called `j6` cannot take over the J6 module's link."""
        return f'page-{self.slug}'

    @property
    def link_subject(self):
        """This page as something `core.syncup_links` can send, exactly as a module is.

        `has_syncup_link` is the admin's tickbox and `syncup_ready` is whether the page would
        actually open, so switching one off leaves its row on the System page offering to hide
        the link — rather than the row vanishing while the link is still live in everybody's
        app.
        """
        return LinkSubject(
            key=self.link_key,
            title=self.title,
            syncup_path=self.path if self.in_syncup else '',
            syncup_description=self.description,
            syncup_icon=self.icon,
            syncup_color=self.colour,
            syncup_token=False,            # a standalone page has nothing to push
            ready=self.is_ready,
        )
