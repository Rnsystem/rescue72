from django.urls import path
from .views import create_alert

urlpatterns = [
    path("alerts/", create_alert, name="create_alert"),
]