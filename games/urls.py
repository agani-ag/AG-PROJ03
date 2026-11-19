"""Games URLs — the tables for the people playing, the console for the three of us.

The short address belongs to the players: `/games/` is what gets pasted into a chat along with
a room code, and the console takes the longer one. Same arrangement as the other three modules.
"""
from django.urls import path

from . import views_admin, views_user

urlpatterns = [
    # ---- the tables
    path('join', views_user.join, name='games_join'),
    path('', views_user.home, name='games_home'),
    path('new', views_user.new_room, name='games_new'),
    path('enter', views_user.join_room, name='games_enter'),
    path('settings/', views_user.settings_page, name='games_settings'),
    path('r/<slug:code>/', views_user.room, name='games_room'),
    path('r/<slug:code>/seat', views_user.seat_room, name='games_seat'),
    path('r/<slug:code>/poll', views_user.poll, name='games_poll'),
    path('r/<slug:code>/move', views_user.move, name='games_move'),

    # ---- the console, inside SyncHub
    path('admin/', views_admin.rooms, name='games_rooms'),
    path('admin/players/', views_admin.players, name='games_players'),
    path('admin/r/<slug:code>/', views_admin.room_detail, name='games_room_admin'),
    path('admin/r/<slug:code>/end', views_admin.room_end, name='games_room_end'),
    path('admin/settings/', views_admin.settings_page, name='games_settings_admin'),
]
