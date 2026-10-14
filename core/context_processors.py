"""Context every template gets: the product name and the module list for the sidebar."""
from django.conf import settings

from .modules import groups


def shell(request):
    ctx = {'app_name': settings.APP_NAME}
    user = getattr(request, 'user', None)
    if user and user.is_authenticated and user.is_superuser:
        ctx['nav_module_groups'] = groups()
    return ctx
