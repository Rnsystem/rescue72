from django.db import models

# Create your models here.
from django.utils import timezone

class Device(models.Model):
    device_id = models.CharField(max_length=255, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_latitude = models.FloatField(null=True, blank=True)
    last_longitude = models.FloatField(null=True, blank=True)
    last_accuracy = models.FloatField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    watch_prefecture_code = models.CharField(max_length=8, blank=True, default="")
    watch_prefecture_name = models.CharField(max_length=32, blank=True, default="")
    watch_prefecture_updated_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        indexes = [
            # bbox検索（範囲検索）の基本：複合インデックス
            models.Index(fields=["last_latitude", "last_longitude"], name="idx_device_last_lat_lon"),
        ]
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
    alert = models.ForeignKey(
        "alerts.Alert",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="locations",
    )
    def __str__(self):
        return f"{self.device.device_id} @ {self.latitude}, {self.longitude}"