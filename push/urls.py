from django.urls import path
from .views import (
    subscribe, unsubscribe, setup_page,
    vapid_public_key, service_worker, send_push, respond_page, answer_page,
)

urlpatterns = [
    path("push/subscribe/", subscribe, name="push_subscribe"),
    path("push/unsubscribe/", unsubscribe, name="push_unsubscribe"),
    path("push/vapid_public_key/", vapid_public_key, name="vapid_public_key"),
    path("push/send/", send_push, name="push_send"),
    path("respond/", respond_page, name="respond_page"),
    path("setup/", setup_page, name="setup_page"),
    path("answer/", answer_page, name="answer_page"),
]