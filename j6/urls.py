"""J6 URLs — three groups in one file.

    /j6/            the console, inside SyncHub (admin login)
    /j6/me/         the chat itself (its own session)
    /j6/api/        every non-browser client, authenticated by Self-ID header
    /j6/s/<id>      the one-tap sign-in link
"""
from django.urls import path

from . import views_admin, views_api, views_user

urlpatterns = [
    # ---- the chat. Listed first so /me/ and /api/ never look like an account id.
    path('me/', views_user.inbox, name='j6_inbox'),
    path('me/welcome', views_user.welcome, name='j6_welcome'),
    path('me/login', views_user.sign_in, name='j6_login'),
    path('me/logout', views_user.sign_out, name='j6_logout'),
    path('me/ids', views_user.ids, name='j6_ids'),
    path('me/new', views_user.new_chat, name='j6_new'),
    path('me/profile', views_user.profile, name='j6_profile'),
    path('me/c/<int:pk>', views_user.inbox, name='j6_chat'),
    path('me/c/<int:pk>/block', views_user.block, name='j6_block'),
    path('me/poll', views_user.poll, name='j6_poll'),
    path('me/send', views_user.send, name='j6_send'),
    path('me/typing', views_user.typing, name='j6_typing'),
    path('me/token', views_user.save_syncup_token, name='j6_token'),
    path('s/<slug:self_id>', views_user.magic, name='j6_magic'),

    # ---- desktop and terminal clients
    path('api/start', views_api.start, name='j6_api_start'),
    path('api/hello', views_api.hello, name='j6_api_hello'),
    path('api/poll', views_api.poll, name='j6_api_poll'),
    path('api/send', views_api.send, name='j6_api_send'),
    path('api/new', views_api.new_chat, name='j6_api_new'),
    path('api/read', views_api.read, name='j6_api_read'),
    path('api/version', views_api.version, name='j6_api_version'),

    # ---- the console
    path('', views_admin.accounts, name='j6_accounts'),
    path('settings/', views_admin.settings_page, name='j6_settings'),
    path('<int:pk>/', views_admin.account, name='j6_account'),
    path('<int:pk>/state/<slug:what>', views_admin.account_state, name='j6_account_state'),
    path('<int:pk>/delete', views_admin.account_delete, name='j6_account_delete'),
    path('<int:pk>/c/<int:chat_pk>/', views_admin.chat, name='j6_admin_chat'),
    path('<int:pk>/c/<int:chat_pk>/delete', views_admin.chat_delete, name='j6_chat_delete'),
]
