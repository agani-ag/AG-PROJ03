"""J6 URLs — three groups in one file.

    /j6/            the chat itself (its own session)
    /j6/s/<id>      the one-tap sign-in link
    /j6/api/        every non-browser client, authenticated by Self-ID header
    /j6/admin/      the console, inside SyncHub (admin login)

The short address belongs to the people using it, not to the three of us reading it: `/j6/` is
what gets pasted into a message, and the console takes the longer path.
"""
from django.urls import path

from . import views_admin, views_api, views_user

urlpatterns = [
    # ---- the chat. Listed first so none of it can be read as a console path.
    path('', views_user.inbox, name='j6_inbox'),
    path('welcome', views_user.welcome, name='j6_welcome'),
    path('login', views_user.sign_in, name='j6_login'),
    path('logout', views_user.sign_out, name='j6_logout'),
    path('ids', views_user.ids, name='j6_ids'),
    path('new', views_user.new_chat, name='j6_new'),
    path('profile', views_user.profile, name='j6_profile'),
    path('c/<int:pk>', views_user.inbox, name='j6_chat'),
    path('c/<int:pk>/block', views_user.block, name='j6_block'),
    path('poll', views_user.poll, name='j6_poll'),
    path('earlier', views_user.earlier, name='j6_earlier'),
    path('send', views_user.send, name='j6_send'),
    path('typing', views_user.typing, name='j6_typing'),
    path('token', views_user.save_syncup_token, name='j6_token'),
    path('s/<slug:self_id>', views_user.magic, name='j6_magic'),

    # ---- where the chat used to live
    path('me/', views_user.moved_from_me, name='j6_moved'),
    path('me/<path:rest>', views_user.moved_from_me),

    # ---- desktop and terminal clients
    path('api/start', views_api.start, name='j6_api_start'),
    path('api/hello', views_api.hello, name='j6_api_hello'),
    path('api/poll', views_api.poll, name='j6_api_poll'),
    path('api/send', views_api.send, name='j6_api_send'),
    path('api/new', views_api.new_chat, name='j6_api_new'),
    path('api/read', views_api.read, name='j6_api_read'),
    path('api/version', views_api.version, name='j6_api_version'),

    # ---- the console
    path('admin/', views_admin.accounts, name='j6_accounts'),
    path('admin/settings/', views_admin.settings_page, name='j6_settings'),
    path('admin/reset/', views_admin.factory_reset, name='j6_reset'),
    path('admin/<int:pk>/', views_admin.account, name='j6_account'),
    path('admin/<int:pk>/rename', views_admin.account_rename, name='j6_account_rename'),
    path('admin/<int:pk>/test-push', views_admin.account_test_push, name='j6_account_test_push'),
    path('admin/<int:pk>/state/<slug:what>', views_admin.account_state, name='j6_account_state'),
    path('admin/<int:pk>/delete', views_admin.account_delete, name='j6_account_delete'),
    path('admin/<int:pk>/c/<int:chat_pk>/', views_admin.chat, name='j6_admin_chat'),
    path('admin/<int:pk>/c/<int:chat_pk>/delete', views_admin.chat_delete, name='j6_chat_delete'),
    path('admin/<int:pk>/c/<int:chat_pk>/m/<int:seq>/delete', views_admin.message_delete,
         name='j6_message_delete'),
]
