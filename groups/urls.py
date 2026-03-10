from django.urls import path
from .views import (
    create_group,
    join_group,
    add_devices,
    list_group_devices,
    group_alert_deliveries,
    group_alert_stats,
    create_group_deliveries,
    group_alert_triage,
    group_alert_summary,
    group_alert_notify,
)

urlpatterns = [
    path("groups/", create_group, name="create_group"),
    path("groups/<int:group_id>/join/", join_group, name="join_group"),
    path("groups/<int:group_id>/devices/", add_devices, name="add_group_devices"),
    path("groups/<int:group_id>/devices/list/", list_group_devices, name="list_group_devices"),
    path("groups/<int:group_id>/alerts/<int:alert_id>/deliveries/", group_alert_deliveries, name="group_alert_deliveries"),
    path("groups/<int:group_id>/alerts/<int:alert_id>/stats/", group_alert_stats, name="group_alert_stats"),
    path("groups/<int:group_id>/alerts/deliveries/", create_group_deliveries, name="create_group_deliveries"),
    path("groups/<int:group_id>/alerts/<int:alert_id>/triage/", group_alert_triage, name="group_alert_triage"),
    path("groups/<int:group_id>/alerts/<int:alert_id>/summary/", group_alert_summary, name="group_alert_summary"),
    path("groups/<int:group_id>/alerts/<int:alert_id>/notify/", group_alert_notify, name="group_alert_notify"),
]