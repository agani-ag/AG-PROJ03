"""Admin-side forms, styled with SyncHub's app.css classes (class="input" on every widget)."""
import re
from datetime import date, timedelta

from django import forms

from .models import Flow, Person, Settings, StockItem, StockMove, Symptom

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


class DayRangeForm(forms.Form):
    """Log a period as a range of days, then correct single days afterwards.

    One flow level and one set of symptoms are applied to every day in the range — that is what
    makes typing months of history quick. Each day is its own row, so any of them can be changed
    on its own later.
    """
    MAX_SPAN = 31                     # a typo should not write three years of rows

    first_day = forms.DateField(label='First day',
                                widget=forms.DateInput(attrs={'type': 'date'}))
    last_day = forms.DateField(label='Last day', required=False,
                               widget=forms.DateInput(attrs={'type': 'date'}),
                               help_text='Leave blank for a single day, or while it is still going.')
    flow = forms.TypedChoiceField(label='Flow', choices=Flow.choices, coerce=int,
                                  initial=Flow.MEDIUM)
    symptoms = forms.MultipleChoiceField(label='Symptoms', choices=Symptom.choices,
                                         required=False, widget=forms.CheckboxSelectMultiple)
    note = forms.CharField(label='Note', required=False, max_length=300,
                           widget=forms.TextInput(attrs={'placeholder': 'Anything to remember…'}))

    def __init__(self, *args, today=None, **kwargs):
        self.today = today or date.today()
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields['symptoms'].widget.attrs.pop('class', None)

    def clean_first_day(self):
        day = self.cleaned_data['first_day']
        if day > self.today:
            raise forms.ValidationError('That day is in the future.')
        return day

    def clean(self):
        data = super().clean()
        first, last = data.get('first_day'), data.get('last_day')
        if not first:
            return data
        if not last:
            data['last_day'] = last = first
        if last < first:
            self.add_error('last_day', 'The last day cannot be before the first.')
        elif last > self.today:
            self.add_error('last_day', 'That day is in the future.')
        elif (last - first).days + 1 > self.MAX_SPAN:
            self.add_error('last_day', f'That is more than {self.MAX_SPAN} days — check the dates.')
        return data

    def dates(self):
        first, last = self.cleaned_data['first_day'], self.cleaned_data['last_day']
        return [first + timedelta(days=i) for i in range((last - first).days + 1)]


class DayForm(forms.Form):
    """One day on its own — the per-day correction."""
    flow = forms.TypedChoiceField(label='Flow', choices=Flow.choices, coerce=int)
    symptoms = forms.MultipleChoiceField(label='Symptoms', choices=Symptom.choices,
                                         required=False, widget=forms.CheckboxSelectMultiple)
    note = forms.CharField(label='Note', required=False, max_length=300,
                           widget=forms.TextInput(attrs={'placeholder': 'Anything to remember…'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields['symptoms'].widget.attrs.pop('class', None)


class SignUpForm(forms.Form):
    """Sign yourself up with an email address or a phone number — one of the two."""
    MIN_PASSWORD = 8

    name = forms.CharField(label='Your name', max_length=80,
                           widget=forms.TextInput(attrs={'placeholder': 'e.g. Divya',
                                                         'autofocus': True}))
    contact = forms.CharField(label='Email or phone', max_length=120,
                              widget=forms.TextInput(attrs={'placeholder': 'name@example.com or 10-digit mobile'}))
    password = forms.CharField(label='Password', min_length=MIN_PASSWORD, strip=False,
                               widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
                               help_text=f'At least {MIN_PASSWORD} characters.')
    cycle_hint = forms.IntegerField(label='Usual cycle length', required=False, min_value=15,
                                    max_value=90,
                                    widget=forms.NumberInput(attrs={'placeholder': 'e.g. 28'}),
                                    help_text='Optional — it makes your first calendar useful.')
    invite_code = forms.CharField(label='Invite code', required=False, max_length=24)

    def __init__(self, *args, config=None, **kwargs):
        self.config = config
        super().__init__(*args, **kwargs)
        if not (config and config.invite_code):
            self.fields.pop('invite_code')
        _style(self)

    def clean_invite_code(self):
        given = (self.cleaned_data.get('invite_code') or '').strip()
        if self.config and self.config.invite_code and given != self.config.invite_code:
            raise forms.ValidationError('That code is not right.')
        return given

    def clean_contact(self):
        raw = (self.cleaned_data.get('contact') or '').strip()
        if '@' in raw:
            email = raw.lower()
            forms.EmailField().clean(email)
            if Person.objects.filter(email__iexact=email).exists():
                raise forms.ValidationError('There is already an account with that email.')
            self.cleaned_data['kind'] = 'email'
            return email
        digits = ''.join(c for c in raw if c.isdigit())[-10:]
        if not PHONE.fullmatch(digits):
            raise forms.ValidationError('Enter an email address or a 10-digit mobile number.')
        if Person.objects.filter(phone=digits).exists():
            raise forms.ValidationError('There is already an account with that number.')
        self.cleaned_data['kind'] = 'phone'
        return digits


class SignInForm(forms.Form):
    contact = forms.CharField(label='Email or phone', max_length=120,
                              widget=forms.TextInput(attrs={'autofocus': True,
                                                            'autocapitalize': 'none'}))
    password = forms.CharField(label='Password', strip=False,
                               widget=forms.PasswordInput(attrs={'autocomplete': 'current-password'}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class MySettingsForm(forms.Form):
    """What a person may change about their own account."""
    name = forms.CharField(label='Your name', max_length=80)
    cycle_hint = forms.IntegerField(label='Usual cycle length', required=False, min_value=15,
                                    max_value=90)
    new_password = forms.CharField(label='New password', required=False, strip=False,
                                   min_length=SignUpForm.MIN_PASSWORD,
                                   widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
                                   help_text='Leave blank to keep the one you have.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class StockItemForm(forms.ModelForm):
    opening = forms.IntegerField(label='How many now', required=False, min_value=0, initial=0)

    class Meta:
        model = StockItem
        fields = ['name', 'low_at']
        widgets = {'name': forms.TextInput(attrs={'placeholder': 'e.g. Pads, Sofy'})}
        labels = {'low_at': 'Tell me when it reaches'}
        help_texts = {'low_at': '0 turns the reminder off for this item.'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)


class StockMoveForm(forms.ModelForm):
    """A movement with its own date, so usage can be back-dated — the old ledger always said
    "today", which made it useless for reading alongside cycle days."""

    class Meta:
        model = StockMove
        fields = ['date', 'change', 'reason', 'note']
        widgets = {'date': forms.DateInput(attrs={'type': 'date'}),
                   'note': forms.TextInput(attrs={'placeholder': 'Optional'})}
        labels = {'change': 'How many (− used, + added)'}

    def __init__(self, *args, today=None, **kwargs):
        self.today = today or date.today()
        super().__init__(*args, **kwargs)
        self.fields['date'].initial = self.today
        _style(self)

    def clean_date(self):
        day = self.cleaned_data['date']
        if day > self.today:
            raise forms.ValidationError('That day is in the future.')
        return day

    def clean_change(self):
        change = self.cleaned_data['change']
        if change == 0:
            raise forms.ValidationError('Use a number other than zero.')
        return change


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Settings
        fields = ['display_name', 'time_zone', 'week_starts_sunday', 'default_cycle',
                  'default_period', 'luteal_days', 'bridge_days', 'signup_open', 'invite_code',
                  'keep_audit_days', 'syncup_base', 'syncup_notify_key', 'notify_late_hours']
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
            'syncup_base': 'SyncUp address',
            'syncup_notify_key': 'SyncUp notify key',
            'notify_late_hours': 'Drop a message later than (hours)',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
