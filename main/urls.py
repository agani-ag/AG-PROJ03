"""URL routing for AG-PROJ03.

`core` owns sign-in, the dashboard and the shared layout. Each feature module gets its own
include() line below as it is built, so modules stay separate from one another.
"""
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('django-admin/', admin.site.urls),   # Django's own admin, kept off the product paths
    path('', include('core.urls')),           # sign-in, dashboard, profile, UI kit, cron
    # Feature modules, each one self-contained:
    path('hibiscus/', include('hibiscus.urls')),
    # path('chat/', include('chat.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
