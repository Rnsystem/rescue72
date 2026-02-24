from django.urls import path
from .views import (
    create_alert,
    create_deliveries,
    list_deliveries,
    alert_stats,
)

urlpatterns = [
    path("alerts/", create_alert, name="create_alert"),
    path("alerts/deliveries/", create_deliveries, name="create_deliveries"),
    path("alerts/<int:alert_id>/deliveries/", list_deliveries, name="list_deliveries"),
    path("alerts/<int:alert_id>/stats/", alert_stats, name="alert_stats"),
]