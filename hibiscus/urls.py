"""Hibiscus URLs — two groups in one file.

    /hibiscus/…        the diary, for the people who keep it (their own sign-in)
    /hibiscus/admin/…  the console side, inside SyncHub (admin login)

The people using this are the public; the console is three of us. So the short address belongs
to them — `/hibiscus/` is what gets written on a note or pasted into a chat, and the console
takes the longer one.
"""
from django.urls import path

from . import views_admin, views_user

urlpatterns = [
    # ---- the diary itself. Listed first so none of these can look like a console path.
    path('join', views_user.join, name='hibiscus_join'),
    path('signup', views_user.join, name='hibiscus_me_signup'),   # old address, kept
    path('login', views_user.sign_in_view, name='hibiscus_me_login'),
    path('logout', views_user.sign_out_view, name='hibiscus_me_logout'),
    # The magic link the admin hands out. Short path on purpose: this gets pasted about.
    path('k/<slug:key>', views_user.magic_login, name='hibiscus_me_key'),
    path('', views_user.home, name='hibiscus_me_home'),
    path('calendar/', views_user.month, name='hibiscus_me_calendar'),
    path('day/<slug:on>/', views_user.day_sheet, name='hibiscus_me_day'),
    path('log/', views_user.log_period, name='hibiscus_me_log'),
    path('stocks/', views_user.stocks, name='hibiscus_me_stocks'),
    path('stocks/<int:pk>/move', views_user.stock_move, name='hibiscus_me_stock_move'),
    path('stocks/<int:pk>/remove', views_user.stock_item_remove,
         name='hibiscus_me_stock_remove'),
    path('settings/', views_user.my_settings, name='hibiscus_me_settings'),
    path('reminders/<int:pk>/toggle', views_user.toggle_slot, name='hibiscus_me_slot_toggle'),
    path('theme', views_user.set_theme, name='hibiscus_me_theme'),
    path('syncup-token', views_user.save_syncup_token, name='hibiscus_me_token'),
    path('reminder', views_user.my_reminder, name='hibiscus_me_reminder'),

    # ---- where the diary used to live. Links were handed out with these in them.
    path('me/', views_user.moved_from_me, name='hibiscus_me_moved'),
    path('me/<path:rest>', views_user.moved_from_me),

    # ---- the console side, inside SyncHub
    path('admin/', views_admin.people, name='hibiscus_people'),
    path('admin/settings/', views_admin.settings_page, name='hibiscus_settings'),
    path('admin/add/', views_admin.person_add, name='hibiscus_person_add'),
    path('admin/<int:pk>/', views_admin.person_detail, name='hibiscus_person'),
    path('admin/<int:pk>/edit/', views_admin.person_edit, name='hibiscus_person_edit'),
    path('admin/<int:pk>/password', views_admin.person_password, name='hibiscus_person_password'),
    path('admin/<int:pk>/link/<slug:what>', views_admin.person_link, name='hibiscus_person_link'),
    path('admin/<int:pk>/test-push', views_admin.person_test_push, name='hibiscus_person_test_push'),
    path('admin/<int:pk>/state/<slug:what>', views_admin.person_state, name='hibiscus_person_state'),
    path('admin/<int:pk>/delete/', views_admin.person_delete, name='hibiscus_person_delete'),
    path('admin/<int:pk>/periods/', views_admin.person_periods, name='hibiscus_person_periods'),
    path('admin/<int:pk>/day/<slug:on>/', views_admin.day_edit, name='hibiscus_day_edit'),
    path('admin/<int:pk>/day/<slug:on>/delete', views_admin.day_delete, name='hibiscus_day_delete'),
    path('admin/<int:pk>/period/<slug:start>/delete', views_admin.period_delete,
         name='hibiscus_period_delete'),
    path('admin/<int:pk>/stocks/', views_admin.person_stocks, name='hibiscus_person_stocks'),
    path('admin/<int:pk>/stocks/<int:item_pk>/toggle', views_admin.stock_item_toggle,
         name='hibiscus_item_toggle'),
    path('admin/<int:pk>/stocks/<int:item_pk>/edit', views_admin.stock_item_edit,
         name='hibiscus_item_edit'),
    path('admin/<int:pk>/export.csv', views_admin.person_export, name='hibiscus_person_export'),
    path('admin/<int:pk>/activity/', views_admin.person_activity, name='hibiscus_person_activity'),
    path('admin/notifications/', views_admin.notifications_page, name='hibiscus_notifications'),
    path('admin/notifications/preview', views_admin.notifications_preview,
         name='hibiscus_notifications_preview'),
]
