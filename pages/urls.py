"""One address, one folder. See `views.py`."""
from django.urls import path

from . import views

urlpatterns = [
    path('p/<slug:name>', views.show, name='page_show'),
    path('pages/', views.index, name='pages'),       # the console's read-only list
]
