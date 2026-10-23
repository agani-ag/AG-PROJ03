"""Sign-in and profile forms, styled with app.css classes."""
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from . import auth as hub
from .models import AdminPin, HubSettings


def _style(form):
    """app.css class for every widget (these forms have no checkboxes)."""
    for field in form.fields.values():
        field.widget.attrs.setdefault('class', 'input')


class AuthForm(AuthenticationForm):
    """Password sign-in. Only the admin (an active superuser) gets through."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
        self.fields['username'].widget.attrs.setdefault('autofocus', True)
        self.fields['username'].widget.attrs.setdefault('autocomplete', 'username')
        self.fields['password'].widget.attrs.setdefault('autocomplete', 'current-password')

    def clean_username(self):
        username = self.cleaned_data.get('username')
        return username.lower() if username else username

    def confirm_login_allowed(self, user):
        super().confirm_login_allowed(user)
        if not user.is_superuser:
            # Same message as a wrong password, so this doesn't reveal that the account exists.
            raise self.get_invalid_login_error()


class ProfileForm(forms.Form):
    """Display name, and a new password or PIN when one is typed."""

    name = forms.CharField(max_length=150, label='Name')
    new_pin = forms.CharField(
        label='New PIN', required=False, min_length=5, max_length=5,
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'autocapitalize': 'characters',
                                      'placeholder': 'e.g. A1B2C'}),
        help_text='Exactly 5 letters or digits. Leave blank to keep the current PIN.',
    )
    new_password = forms.CharField(
        label='New password', required=False, strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
        help_text='Leave blank to keep the current password.',
    )
    confirm_password = forms.CharField(
        label='Confirm new password', required=False, strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )

    def __init__(self, *args, user=None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        _style(self)

    def clean_new_pin(self):
        pin = (self.cleaned_data.get('new_pin') or '').strip().upper()
        if pin and not AdminPin.PATTERN.fullmatch(pin):
            raise forms.ValidationError('Use exactly 5 letters or digits (A-Z, 0-9).')
        return pin

    def clean(self):
        data = super().clean()
        new = data.get('new_password')
        confirm = data.get('confirm_password')
        if new or confirm:
            if new != confirm:
                self.add_error('confirm_password', "The two passwords don't match.")
            else:
                password_validation.validate_password(new, self.user)
        return data


class MemberForm(forms.Form):
    """Somebody on the public side, added or edited from the console.

    Email and phone are both optional *fields* with one of the two required, because the rule
    is "something to sign in with", not "an email". That rule lives in
    `core.auth.check_identity` alongside the public sign-up form rather than here — the console
    is the one that would otherwise quietly create a row nobody can sign in as.

    The password works the way the admin's own profile page works: typed means change it, blank
    means leave it. Adding somebody is the exception, where it is required, because a new
    account with no password can only be opened by a link and the brief says every new person
    gets a password.
    """

    name = forms.CharField(max_length=80, label='Name')
    email = forms.EmailField(
        required=False, label='Email',
        widget=forms.EmailInput(attrs={'autocomplete': 'off', 'inputmode': 'email'}))
    phone = forms.CharField(
        required=False, max_length=15, label='Phone',
        widget=forms.TextInput(attrs={'autocomplete': 'off', 'inputmode': 'tel'}))
    password = forms.CharField(
        required=False, strip=False, label='Password',
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}))

    def __init__(self, *args, member=None, adding=False, **kwargs):
        self.member = member
        self.adding = adding
        super().__init__(*args, **kwargs)
        self.fields['password'].help_text = (
            f'At least {hub.PASSWORD_MIN} characters. You hand it over yourself — this server '
            'sends no mail.' if adding else 'Leave blank to keep their current password.')
        if not adding:
            self.fields['password'].label = 'New password'
        _style(self)

    def clean_email(self):
        return (self.cleaned_data.get('email') or '').strip().lower()

    def clean_phone(self):
        return hub.normalise_phone(self.cleaned_data.get('phone'))

    def clean(self):
        data = super().clean()
        field, problem = hub.check_identity(data.get('name', ''), data.get('email', ''),
                                            data.get('phone', ''),
                                            exclude=self.member.pk if self.member else None)
        if problem:
            self.add_error(field or None, problem)
        password = data.get('password') or ''
        if self.adding or password:
            problem = hub.check_password_value(password)
            if problem:
                self.add_error('password', problem)
        return data


class HubSettingsForm(forms.ModelForm):
    """The hub's own settings. Every module reads these; none of them owns them."""

    class Meta:
        model = HubSettings
        fields = ['site_base', 'syncup_base', 'syncup_notify_key', 'syncup_links_key']
        labels = {
            'site_base': "This site's address",
            'syncup_base': 'SyncUp address',
            'syncup_notify_key': 'SyncUp notify key',
            'syncup_links_key': 'SyncUp links key',
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _style(self)
