"""Sign-in and profile forms, styled with app.css classes."""
from django import forms
from django.contrib.auth import password_validation
from django.contrib.auth.forms import AuthenticationForm

from .models import AdminPin


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
