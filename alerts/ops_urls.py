# alerts/ops_urls.py
from django.urls import path
from . import ops_views

urlpatterns = [
    path("alerts/", ops_views.ops_alert_list, name="ops_alert_list"),
    path("alerts/<int:alert_id>/", ops_views.ops_alert_detail, name="ops_alert_detail"),
    path("alerts/<int:alert_id>/deliveries.csv", ops_views.ops_alert_deliveries_csv, name="ops_alert_deliveries_csv"),
    path("alerts/<int:alert_id>/resolve/", ops_views.ops_delivery_resolve, name="ops_delivery_resolve"),
    path("ops/alerts/<int:alert_id>/export.csv", ops_views.ops_alert_export_csv, name="ops_alert_export_csv"),
]