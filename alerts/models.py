from django.db import models

# Create your models here.

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
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.alert_type}: {self.title}"


class AlertDelivery(models.Model):
    STATUS_CHOICES = [
        ("sent", "sent"),
        ("responded", "responded"),
        ("failed", "failed"),
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

    sent_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["alert", "device"], name="uniq_alert_device")
        ]
    
    def __str__(self):
        return f"{self.alert.id} -> {self.device.device_id} ({self.status})"