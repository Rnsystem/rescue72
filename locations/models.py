from django.db import models

# Create your models here.
from django.utils import timezone

class Device(models.Model):
    device_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.device_id


class Location(models.Model):
    SOURCE_CHOICES = [
        ("pre", "pre-disaster"),
        ("alert", "alert"),
        ("manual", "manual"),
    ]

    device = models.ForeignKey(
        Device,
        on_delete=models.CASCADE,
        related_name="locations"
    )
    latitude = models.FloatField()
    longitude = models.FloatField()
    accuracy = models.FloatField(null=True, blank=True)
    source = models.CharField(
        max_length=16,
        choices=SOURCE_CHOICES,
        default="manual"
    )
    recorded_at = models.DateTimeField(auto_now_add=True)  # 端末側時刻
    captured_at = models.DateTimeField(null=True, blank=True) # サーバ保存時刻
    def __str__(self):
        return f"{self.device.device_id} @ {self.latitude}, {self.longitude}"