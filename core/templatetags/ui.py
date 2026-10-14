"""UI template tags, registered as builtins in settings (no {% load %} needed).

    {% icon "plus" %}                      → <svg class="icon"><use href="/static/icons.svg?v=…#i-plus"></use></svg>
    {% icon "trash" "icon-sm" %}           → extra CSS classes
    {% nav_active "dashboard ui_kit" %}    → aria-current="page" when the current URL name is in the list
    {{ some_datetime|ago }}                → "just now", "5 minutes ago", "2 days ago"
    {% static_v "css/app.css" %}           → /static/css/app.css?v=… (changes when the file does)

Icon names are Lucide names; static/icons.svg lists the ones available.
Taken from AG-PROJ01 so both projects share one set of UI helpers.
"""
import hashlib
from functools import lru_cache

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.utils.timesince import timesince

register = template.Library()


@lru_cache(maxsize=32)
def _versioned(path):
    """The file's static URL with ?v=<short hash of its contents>, so browsers pick up changes.
    Worked out once per process (a reload after deploying picks up the new file)."""
    found = finders.find(path)
    if not found:
        return static(path)
    with open(found, 'rb') as f:
        return f"{static(path)}?v={hashlib.md5(f.read()).hexdigest()[:10]}"


@register.simple_tag
def static_v(path):
    return _versioned(path)


@register.simple_tag
def icon(name, css=''):
    return format_html(
        '<svg class="icon {}" aria-hidden="true"><use href="{}#i-{}"></use></svg>',
        css, _versioned('icons.svg'), name,
    )


@register.simple_tag(takes_context=True)
def nav_active(context, url_names):
    """aria-current="page" when the page being rendered is one of these URL names.
    An entry may be "name:value" to also require that the view's <key> kwarg equals value."""
    request = context.get('request')
    match = getattr(request, 'resolver_match', None)
    if match is None or not url_names:
        return ''   # a module with no URL yet passes None
    current = match.url_name
    for entry in url_names.split():
        name, _, key = entry.partition(':')
        if name == current and (not key or match.kwargs.get('key') == key):
            return mark_safe('aria-current="page"')
    return ''


@register.filter
def ago(value):
    """Short relative time: the largest unit only ("2 days ago", not "2 days, 3 hours ago")."""
    if not value:
        return ''
    if (timezone.now() - value).total_seconds() < 60:
        return 'just now'
    return timesince(value).split(',')[0] + ' ago'
