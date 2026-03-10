from django.urls import path
from . import views
from .views import (
    create_alert,
    create_deliveries,
    list_deliveries,
    alert_stats,
    respond_alert,
    resolve_alert,
    push_prefecture,
    ingest_disaster,
)

urlpatterns = [
    path("alerts/", create_alert, name="create_alert"),
    path("alerts/deliveries/", create_deliveries, name="create_deliveries"),
    path("alerts/<int:alert_id>/deliveries/", list_deliveries, name="list_deliveries"),
    path("alerts/<int:alert_id>/stats/", alert_stats, name="alert_stats"),
    path("alerts/<int:alert_id>/respond/", respond_alert, name="respond_alert"),
    path("alerts/<int:alert_id>/resolve/", resolve_alert, name="resolve_alert"),
    path("alerts/<int:alert_id>/push_prefecture/", push_prefecture, name="push_prefecture"),
    path("disasters/ingest/", ingest_disaster, name="ingest_disaster"),
    path("respond/", views.respond_page, name="respond_page"),
]