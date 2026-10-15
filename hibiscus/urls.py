"""Hibiscus URLs — two groups in one file.

    /hibiscus/…        the admin side, inside SyncHub (admin login)
    /hibiscus/me/…     the people who keep a diary (their own sign-in; step 4)
"""
from django.urls import path

from . import views_admin, views_user

urlpatterns = [
    # ---- the diary, for the people who keep it. Listed first so /me/ never looks like an id.
    path('me/signup', views_user.sign_up, name='hibiscus_me_signup'),
    path('me/login', views_user.sign_in_view, name='hibiscus_me_login'),
    path('me/logout', views_user.sign_out_view, name='hibiscus_me_logout'),
    # The magic link the admin hands out. Short path on purpose: this gets pasted about.
    path('me/k/<slug:key>', views_user.magic_login, name='hibiscus_me_key'),
    path('me/', views_user.home, name='hibiscus_me_home'),
    path('me/calendar/', views_user.month, name='hibiscus_me_calendar'),
    path('me/day/<slug:on>/', views_user.day_sheet, name='hibiscus_me_day'),
    path('me/log/', views_user.log_period, name='hibiscus_me_log'),
    path('me/stocks/', views_user.stocks, name='hibiscus_me_stocks'),
    path('me/stocks/<int:pk>/move', views_user.stock_move, name='hibiscus_me_stock_move'),
    path('me/stocks/<int:pk>/remove', views_user.stock_item_remove,
         name='hibiscus_me_stock_remove'),
    path('me/settings/', views_user.my_settings, name='hibiscus_me_settings'),
    path('me/reminders/<int:pk>/toggle', views_user.toggle_slot, name='hibiscus_me_slot_toggle'),
    path('me/theme', views_user.set_theme, name='hibiscus_me_theme'),
    path('me/syncup-token', views_user.save_syncup_token, name='hibiscus_me_token'),

    # ---- the admin side, inside SyncHub
    path('', views_admin.people, name='hibiscus_people'),
    path('settings/', views_admin.settings_page, name='hibiscus_settings'),
    path('add/', views_admin.person_add, name='hibiscus_person_add'),
    path('<int:pk>/', views_admin.person_detail, name='hibiscus_person'),
    path('<int:pk>/edit/', views_admin.person_edit, name='hibiscus_person_edit'),
    path('<int:pk>/password', views_admin.person_password, name='hibiscus_person_password'),
    path('<int:pk>/link/<slug:what>', views_admin.person_link, name='hibiscus_person_link'),
    path('<int:pk>/state/<slug:what>', views_admin.person_state, name='hibiscus_person_state'),
    path('<int:pk>/delete/', views_admin.person_delete, name='hibiscus_person_delete'),
    path('<int:pk>/periods/', views_admin.person_periods, name='hibiscus_person_periods'),
    path('<int:pk>/day/<slug:on>/', views_admin.day_edit, name='hibiscus_day_edit'),
    path('<int:pk>/day/<slug:on>/delete', views_admin.day_delete, name='hibiscus_day_delete'),
    path('<int:pk>/period/<slug:start>/delete', views_admin.period_delete,
         name='hibiscus_period_delete'),
    path('<int:pk>/stocks/', views_admin.person_stocks, name='hibiscus_person_stocks'),
    path('<int:pk>/stocks/<int:item_pk>/toggle', views_admin.stock_item_toggle,
         name='hibiscus_item_toggle'),
    path('<int:pk>/stocks/<int:item_pk>/edit', views_admin.stock_item_edit,
         name='hibiscus_item_edit'),
    path('<int:pk>/export.csv', views_admin.person_export, name='hibiscus_person_export'),
    path('<int:pk>/activity/', views_admin.person_activity, name='hibiscus_person_activity'),
    path('notifications/', views_admin.notifications_page, name='hibiscus_notifications'),
    path('notifications/preview', views_admin.notifications_preview,
         name='hibiscus_notifications_preview'),
]
