from django.urls import path

from . import views

urlpatterns = [
    path('login', views.login_view, name='login'),
    path('login/pin', views.pin_auth, name='pin_auth'),
    path('logout', views.logout_view, name='logout'),
    path('', views.dashboard, name='dashboard'),
    path('ui/', views.ui_kit, name='ui_kit'),
    path('profile/', views.profile, name='profile'),
    path('download/sqlite', views.download_sqlite, name='download_sqlite'),
    path('m/<slug:key>/', views.module_placeholder, name='module'),
]
