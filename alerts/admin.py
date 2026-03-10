# alerts/admin.py
from django.contrib import admin
from django.db.models import Count, Q

from .models import Alert, AlertDelivery


class AlertDeliveryInline(admin.TabularInline):
    """
    Alert詳細画面に、端末ごとの配信/回答状況を表示
    """
    model = AlertDelivery
    extra = 0
    show_change_link = True

    # 表示項目（必要に応じて増減OK）
    fields = (
        "device",
        "status",
        "response_code",
        "response_note",
        "is_resolved",
        "resolved_note",
        "sent_at",
        "responded_at",
        "resolved_at",
    )
    readonly_fields = ("sent_at", "responded_at", "resolved_at")

    autocomplete_fields = ("device",)
    ordering = ("-id",)

    # 右側フィルタ（Inlineだと効かないこともあるけど一応）
    # list_filter = ("status", "response_code", "is_resolved")


@admin.register(Alert)
class AlertAdmin(admin.ModelAdmin):
    """
    Alerts 一覧で total/sent/responded/failed/pending を表示
    """
    inlines = [AlertDeliveryInline]

    list_display = (
        "id",
        "title",
        "alert_type",
        "issued_at",
        "expires_at",
        "total",
        "sent",
        "responded",
        "failed",
        "pending",
    )
    list_filter = ("alert_type",)
    search_fields = ("title",)
    ordering = ("-id",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # deliveries という related_name / accessor 前提
        return qs.annotate(
            total_count=Count("deliveries", distinct=True),
            sent_count=Count("deliveries", filter=Q(deliveries__status="sent"), distinct=True),
            responded_count=Count("deliveries", filter=Q(deliveries__status="responded"), distinct=True),
            failed_count=Count("deliveries", filter=Q(deliveries__status="failed"), distinct=True),
        )

    @admin.display(ordering="total_count", description="total")
    def total(self, obj):
        return getattr(obj, "total_count", 0) or 0

    @admin.display(ordering="sent_count", description="sent")
    def sent(self, obj):
        return getattr(obj, "sent_count", 0) or 0

    @admin.display(ordering="responded_count", description="responded")
    def responded(self, obj):
        return getattr(obj, "responded_count", 0) or 0

    @admin.display(ordering="failed_count", description="failed")
    def failed(self, obj):
        return getattr(obj, "failed_count", 0) or 0

    @admin.display(description="pending")
    def pending(self, obj):
        # 未回答（sent + 未送達等を pending 扱いにしたいならここを調整）
        total = self.total(obj)
        responded = self.responded(obj)
        failed = self.failed(obj)
        return max(total - responded - failed, 0)


@admin.register(AlertDelivery)
class AlertDeliveryAdmin(admin.ModelAdmin):
    """
    AlertDelivery単体でも検索・絞り込みできるように（運用で便利）
    """
    list_display = (
        "id",
        "alert",
        "device",
        "status",
        "response_code",
        "is_resolved",
        "sent_at",
        "responded_at",
        "resolved_at",
    )
    list_filter = ("status", "response_code", "is_resolved", "alert__alert_type")
    search_fields = ("device__device_id", "alert__title")
    ordering = ("-id",)
    list_select_related = ("alert", "device")
    readonly_fields = ("sent_at", "responded_at", "resolved_at")