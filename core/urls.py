from django.urls import path

from . import cron_views, views

urlpatterns = [
    # The two scheduled jobs for the whole hub, plus a read-only status URL. Keyed, not
    # session-authenticated, so they sit above the admin pages below.
    path('cron/tick', cron_views.tick, name='cron_tick'),
    path('cron/cleanup', cron_views.cleanup, name='cron_cleanup'),
    path('cron/health', cron_views.health, name='cron_health'),
    path('login', views.login_view, name='login'),
    path('login/pin', views.pin_auth, name='pin_auth'),
    path('logout', views.logout_view, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('ui/', views.ui_kit, name='ui_kit'),
    path('profile/', views.profile, name='profile'),
    path('download/sqlite', views.download_sqlite, name='download_sqlite'),
    path('system/', views.system_settings, name='system_settings'),
    path('cron/', views.cron_page, name='cron_page'),
    path('cron/run/<slug:job>', views.cron_run_now, name='cron_run_now'),
    path('m/<slug:key>/', views.module_placeholder, name='module'),
]
