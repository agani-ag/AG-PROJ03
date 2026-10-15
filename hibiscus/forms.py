"""Admin-side forms, styled with SyncHub's app.css classes (class="input" on every widget)."""
import re

from django import forms

from .models import Person, Settings

PHONE = re.compile(r'^[6-9]\d{9}$')          # a 10-digit Indian mobile number


def _style(form):
    for field in form.fields.values():
        if isinstance(field.widget, (forms.CheckboxInput,)):
            continue
        field.widget.attrs.setdefault('class', 'input')


class PersonForm(forms.ModelForm):
    """Add or edit someone. An email address or a phone number — at least one of the two."""

    class Meta:
        model = Person
        fields = ['name', 'email', 'phone', 'cycle_hint']
        widgets = {
            'name': forms.TextInput(attrs={'placeholder': 'e.g. Divya', 'autofocus': True}),
            'email': forms.TextInput(attrs={'placeholder': 'name@example.com'}),
            'phone': forms.TextInput(attrs={'placeholder': '10-digit mobile', 'inputmode': 'tel'}),
            'cycle_hint': forms.NumberInput(attrs={'placeholder': 'e.g. 30', 'min': 15, 'max': 90}),
        }
        labels = {'cycle_hint': 'Usual cycle length'}
        help_texts = {
            'cycle_hint': 'Optional. Used until two periods are logged, instead of assuming 28 days.',
            'email': 'Either an email address or a phone number is required.',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)

    def clean_email(self):
        email = (self.cleaned_data.get('email') or '').strip().lower()
        return email or None                      # '' would collide on the unique index

    def clean_phone(self):
        raw = (self.cleaned_data.get('phone') or '').strip()
        if not raw:
            return None
        digits = ''.join(c for c in raw if c.isdigit())[-10:]
        if not PHONE.fullmatch(digits):
            raise forms.ValidationError('Enter a 10-digit mobile number.')
        return digits

    def clean(self):
        data = super().clean()
        if not data.get('email') and not data.get('phone'):
            raise forms.ValidationError('Add an email address or a phone number.')
        return data


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Settings
        fields = ['display_name', 'time_zone', 'week_starts_sunday', 'default_cycle',
                  'default_period', 'luteal_days', 'bridge_days', 'signup_open', 'invite_code',
                  'keep_audit_days']
        labels = {
            'display_name': 'Name shown to people',
            'time_zone': 'Time zone for "today"',
            'week_starts_sunday': 'Week starts on Sunday',
            'default_cycle': 'Cycle length until two periods exist',
            'default_period': 'Period length default',
            'luteal_days': 'Luteal phase (ovulation = cycle − this)',
            'bridge_days': 'A new period starts after this many empty days + 1',
            'signup_open': 'Anyone may sign up',
            'invite_code': 'Invite code',
            'keep_audit_days': 'Keep the activity trail for (days)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
