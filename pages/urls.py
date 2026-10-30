"""Two halves, as every module here has.

    /p/<slug>        the page itself, for anybody
    /pages/…         the console, inside SyncHub (admin login)

The public half gets the short address because it is the one that gets pasted about.
"""
from django.urls import path

from . import views_admin, views_user

urlpatterns = [
    path('p/<slug:slug>', views_user.show, name='page_show'),

    path('pages/', views_admin.index, name='pages'),
    path('pages/new/', views_admin.new, name='page_new'),
    path('pages/<int:pk>/', views_admin.edit, name='page_edit'),
    path('pages/<int:pk>/toggle/<slug:what>', views_admin.toggle, name='page_toggle'),
    path('pages/<int:pk>/delete', views_admin.delete, name='page_delete'),
]
