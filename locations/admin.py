from django.contrib import admin

# Register your models here.

from .models import Device, Location

@admin.register(Device)
class DeviceAdmin(admin.ModelAdmin):
    list_display = ("id", "device_id", "created_at")
    search_fields = ("device_id",)

@admin.register(Location)
class LocationAdmin(admin.ModelAdmin):
    list_display = ("id", "device", "latitude", "longitude", "accuracy", "recorded_at")
    list_filter = ("recorded_at",)