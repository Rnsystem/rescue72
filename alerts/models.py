from django.db import models

# Create your models here.
from django.utils import timezone
from datetime import timedelta

class Alert(models.Model):
    ALERT_TYPE_CHOICES = [
        ("eq", "earthquake"),
        ("tsunami", "tsunami"),
        ("flood", "flood"),
        ("other", "other"),
    ]

    alert_type = models.CharField(max_length=16, choices=ALERT_TYPE_CHOICES)
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True)
    issued_at = models.DateTimeField()  # 発令時刻（外部APIの時刻など）
    expires_at = models.DateTimeField(db_index=True)  # ★追加：issued_at + 72h
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        # expires_at が未指定なら issued_at + 72h を自動設定
        if self.issued_at and not self.expires_at:
            self.expires_at = self.issued_at + timedelta(hours=72)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.alert_type}: {self.title}"


class AlertDelivery(models.Model):
    STATUS_CHOICES = [
        ("sent", "sent"),
        ("responded", "responded"),
        ("failed", "failed"),
    ]

    RESPONSE_CHOICES = [
        ("safe", "safe"),
        ("need_help", "need_help"),
        ("injured", "injured"),
        ("unknown", "unknown"),
    ]

    alert = models.ForeignKey(
        "alerts.Alert",
        on_delete=models.CASCADE,
        related_name="deliveries",
    )

    device = models.ForeignKey(
        "locations.Device",
        on_delete=models.CASCADE,
        related_name="alert_deliveries",
    )

    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default="sent",
    )

    response_code = models.CharField(
        max_length=16,
        choices=RESPONSE_CHOICES,
        null=True,
        blank=True,
    )
    response_note = models.TextField(blank=True, default="")

    sent_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    is_resolved = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_note = models.TextField(blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["alert", "device"], name="uniq_alert_device")
        ]
    
    def __str__(self):
        return f"{self.alert.id} -> {self.device.device_id} ({self.status})"