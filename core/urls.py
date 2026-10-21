from django.urls import path

from . import cron_views, views, views_member, views_people

urlpatterns = [
    # The two scheduled jobs for the whole hub, plus a read-only status URL. Keyed, not
    # session-authenticated, so they sit above the admin pages below.
    path('cron/tick', cron_views.tick, name='cron_tick'),
    path('cron/cleanup', cron_views.cleanup, name='cron_cleanup'),
    path('cron/health', cron_views.health, name='cron_health'),
    # ---- the public side: one sign-in for every module, one session
    path('in/', views_member.sign_in, name='member_login'),
    path('join/', views_member.join, name='member_join'),
    path('in/link', views_member.ask_for_link, name='member_link'),
    path('in/k/<str:key>', views_member.magic, name='member_magic'),
    path('in/syncup', views_member.sso_sign_in, name='member_sso'),
    path('in/prove', views_member.prove, name='member_prove'),
    path('in/separate', views_member.separate, name='member_separate'),
    path('in/changed', views_member.sso_account_changed, name='member_sso_changed'),
    path('out', views_member.sign_out, name='member_logout'),
    path('apps/', views_member.apps, name='member_apps'),
    path('you/', views_member.you, name='member_you'),

    # ---- the console's own sign-in, which is a Django superuser and a different thing
    path('login', views.login_view, name='login'),
    path('login/pin', views.pin_auth, name='pin_auth'),
    path('logout', views.logout_view, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('ui/', views.ui_kit, name='ui_kit'),
    path('profile/', views.profile, name='profile'),
    path('download/sqlite', views.download_sqlite, name='download_sqlite'),
    path('people/', views_people.people, name='people'),
    path('people/<int:pk>/', views_people.person, name='person'),
    path('people/<int:pk>/delete', views_people.person_delete, name='person_delete'),
    path('people/<int:pk>/<slug:what>', views_people.person_action, name='person_action'),
    path('system/', views.system_settings, name='system_settings'),
    path('system/syncup-links/send-all', views.syncup_links_send_all, name='syncup_links_send_all'),
    path('system/syncup-links/<slug:key>/send', views.syncup_link_send, name='syncup_link_send'),
    path('system/syncup-links/<slug:key>/visibility', views.syncup_link_visibility,
         name='syncup_link_visibility'),
    path('cron/', views.cron_page, name='cron_page'),
    path('cron/run/<slug:job>', views.cron_run_now, name='cron_run_now'),
    path('m/<slug:key>/', views.module_placeholder, name='module'),
]
