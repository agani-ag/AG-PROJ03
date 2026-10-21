"""Pocket Money URLs.

    /pocket/              the four tabs, with their own session
    /pocket/i/<code>      an invitation to one line
    /pocket/j/<code>      an invitation to a whole folder
    /pocket/admin/        the console, inside SyncHub (admin login)

The short address belongs to the people using it; the console takes the longer path. Same shape
as Hibiscus and J6, for the same reason.
"""
from django.urls import path

from . import views_admin, views_user

urlpatterns = [
    # ---- getting in
    path('', views_user.home, name='pocket_home'),
    path('signup', views_user.signup, name='pocket_signup'),
    path('login', views_user.login, name='pocket_login'),
    path('logout', views_user.logout, name='pocket_logout'),
    path('link', views_user.send_magic_link, name='pocket_magic_send'),
    path('s/<str:key>', views_user.magic, name='pocket_magic'),

    # ---- half one, the Tracker
    path('tracker', views_user.tracker, name='pocket_tracker'),
    path('tracker/add', views_user.tracker_add, name='pocket_tracker_add'),
    path('tracker/<int:pk>/void', views_user.tracker_void, name='pocket_tracker_void'),
    path('tracker/csv', views_user.tracker_csv, name='pocket_tracker_csv'),
    path('categories', views_user.categories, name='pocket_categories'),
    path('categories/<int:pk>/archive', views_user.category_archive,
         name='pocket_category_archive'),

    # ---- half two, the Ledger
    path('ledger', views_user.ledger, name='pocket_ledger'),
    path('ledger/new', views_user.folder_new, name='pocket_folder_new'),
    path('f/<int:pk>', views_user.folder, name='pocket_folder'),
    path('f/<int:pk>/people/add', views_user.person_add, name='pocket_person_add'),
    path('f/<int:pk>/share', views_user.folder_share, name='pocket_folder_share'),
    path('f/<int:pk>/csv', views_user.folder_csv, name='pocket_folder_csv'),
    path('f/<int:pk>/p/<int:person_pk>', views_user.person, name='pocket_person'),
    path('f/<int:pk>/p/<int:person_pk>/add', views_user.ledger_add, name='pocket_ledger_add'),
    path('f/<int:pk>/p/<int:person_pk>/settle', views_user.settle, name='pocket_settle'),
    path('f/<int:pk>/p/<int:person_pk>/invite', views_user.person_invite,
         name='pocket_person_invite'),
    path('f/<int:pk>/p/<int:person_pk>/edit', views_user.person_edit, name='pocket_person_edit'),
    path('f/<int:pk>/e/<int:entry_pk>/void', views_user.ledger_void, name='pocket_ledger_void'),

    # ---- the other side of a line, for the person it belongs to
    path('line/<int:pk>', views_user.line, name='pocket_line'),
    path('line/<int:pk>/add', views_user.line_add, name='pocket_line_add'),
    path('line/<int:pk>/leave', views_user.line_leave, name='pocket_line_leave'),

    # ---- invitations
    path('shared', views_user.shared, name='pocket_shared'),
    path('i/<str:code>', views_user.accept_person, name='pocket_accept_person'),
    path('j/<str:code>', views_user.accept_folder, name='pocket_accept_folder'),
    path('suggestion/<int:pk>/<str:answer>', views_user.suggestion, name='pocket_suggestion'),

    # ---- you
    path('me', views_user.me, name='pocket_me'),
    path('me/upi', views_user.save_upi, name='pocket_upi'),
    path('me/scan', views_user.scan_upi, name='pocket_scan'),
    path('me/token', views_user.save_syncup_token, name='pocket_token'),

    # ---- the console
    path('admin/', views_admin.accounts, name='pocket_accounts'),
    path('admin/settings/', views_admin.settings_page, name='pocket_settings'),
    path('admin/<int:pk>/', views_admin.account, name='pocket_admin_account'),
    path('admin/<int:pk>/state/<slug:what>', views_admin.account_state,
         name='pocket_admin_state'),
    path('admin/<int:pk>/test-push', views_admin.account_test_push, name='pocket_admin_push'),
    path('admin/<int:pk>/delete', views_admin.account_delete, name='pocket_admin_delete'),
    path('admin/f/<int:pk>/', views_admin.folder, name='pocket_admin_folder'),
    path('admin/f/<int:pk>/e/<int:entry_pk>/delete', views_admin.entry_delete,
         name='pocket_admin_entry_delete'),
]
