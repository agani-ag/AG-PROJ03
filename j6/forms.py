"""The one form J6 has: module settings in the console.

The user side posts plain fields rather than forms — a name, a Self-ID, a message — because
every one of them is a single value with its rule in `service`, where the desktop clients reach
it too.
"""
from django import forms

from .models import Settings


def _style(form):
    """Give every widget the console's own classes, as the other modules do."""
    for field in form.fields.values():
        widget = field.widget
        if isinstance(widget, forms.CheckboxInput):
            widget.attrs.setdefault('class', 'check')
        elif isinstance(widget, forms.Select):
            widget.attrs.setdefault('class', 'input select')
        else:
            widget.attrs.setdefault('class', 'input')


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Settings
        fields = ['signup_open', 'new_chats_per_hour', 'msgs_per_minute', 'max_chars',
                  'keep_days', 'idle_account_days',
                  'render_media', 'autoplay_seconds', 'max_media_px', 'allow_svg',
                  'site_base', 'syncup_base', 'syncup_notify_key', 'push_enabled']
        labels = {
            'signup_open': 'New accounts are open',
            'new_chats_per_hour': 'New chats per account, per hour',
            'msgs_per_minute': 'Messages per minute, per account',
            'max_chars': 'Characters per message',
            'keep_days': 'Keep messages for (days)',
            'idle_account_days': 'Retire accounts idle for (days)',
            'render_media': 'Render media links',
            'autoplay_seconds': 'Autoplay clips shorter than (seconds)',
            'max_media_px': 'Tallest a bubble may be (px)',
            'allow_svg': 'Allow SVG',
            'site_base': "This site's address",
            'syncup_base': 'SyncUp address',
            'syncup_notify_key': 'SyncUp notify key',
            'push_enabled': 'Push unread through SyncUp',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
