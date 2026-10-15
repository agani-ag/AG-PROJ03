"""Hibiscus URLs — two groups in one file.

    /hibiscus/…        the admin side, inside SyncHub (admin login)
    /hibiscus/me/…     the people who keep a diary (their own sign-in; step 4)
"""
from django.urls import path

from . import views_admin

urlpatterns = [
    path('', views_admin.people, name='hibiscus_people'),
    path('settings/', views_admin.settings_page, name='hibiscus_settings'),
    path('add/', views_admin.person_add, name='hibiscus_person_add'),
    path('<int:pk>/', views_admin.person_detail, name='hibiscus_person'),
    path('<int:pk>/edit/', views_admin.person_edit, name='hibiscus_person_edit'),
    path('<int:pk>/password', views_admin.person_password, name='hibiscus_person_password'),
    path('<int:pk>/state/<slug:what>', views_admin.person_state, name='hibiscus_person_state'),
    path('<int:pk>/delete/', views_admin.person_delete, name='hibiscus_person_delete'),
    path('<int:pk>/activity/', views_admin.person_activity, name='hibiscus_person_activity'),
]
