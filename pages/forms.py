"""The one form the console needs. The file is not in it — the file is in git."""
from django import forms

from .models import Page


class PageForm(forms.ModelForm):
    """Title, wording and switches. What a page *is* lives in `pages/html/<slug>.html`."""

    class Meta:
        model = Page
        fields = ['slug', 'title', 'description', 'icon', 'colour',
                  'is_enabled', 'in_syncup', 'sandboxed', 'sort_order']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, forms.CheckboxInput):
                field.widget.attrs.setdefault('class', 'input')
        self.fields['slug'].widget.attrs.setdefault('placeholder', 'photo-stamp')
        if self.instance.pk:
            # The slug is the file name as well as the address, so changing it here would
            # point the row at a file that is not there. Rename the file and make a new row.
            self.fields['slug'].disabled = True
            self.fields['slug'].help_text = (
                'Fixed once set: it names the file as well as the address. To change it, '
                'rename the file in the repository and set the page up again.')

    def clean_slug(self):
        slug = (self.cleaned_data.get('slug') or '').strip().lower()
        if not self.instance.pk and slug:
            from .models import files_on_disk
            if slug not in files_on_disk():
                raise forms.ValidationError(
                    f'There is no pages/html/{slug}.html on this server. Commit the file, '
                    'pull, and Reload — then set it up here.')
        return slug
