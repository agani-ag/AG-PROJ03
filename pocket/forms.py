"""The one form the console needs. Everything on the user side is a plain POST."""
from django import forms

from .models import Settings


class SettingsForm(forms.ModelForm):
    class Meta:
        model = Settings
        fields = ['signup_open', 'invite_code', 'currency_symbol', 'time_zone',
                  'push_enabled', 'notify_quiet_from', 'notify_quiet_to', 'reminder_hour',
                  'coalesce_minutes', 'link_days', 'keep_audit_days']
        widgets = {
            'notify_quiet_from': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
            'notify_quiet_to': forms.TimeInput(attrs={'type': 'time'}, format='%H:%M'),
        }

    def clean_reminder_hour(self):
        hour = self.cleaned_data['reminder_hour']
        if hour > 23:
            raise forms.ValidationError('There is no hour after 23.')
        return hour

    def clean_coalesce_minutes(self):
        minutes = self.cleaned_data['coalesce_minutes']
        if minutes > 180:
            raise forms.ValidationError('Three hours of silence is not coalescing, it is off.')
        return minutes
