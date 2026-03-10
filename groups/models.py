from django.db import models

# Create your models here.
from django.contrib.auth.models import User
from django.conf import settings


class DeviceGroup(models.Model):
    name = models.CharField(max_length=100)

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="owned_groups",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name

class GroupMember(models.Model):
    group = models.ForeignKey(
        "groups.DeviceGroup",
        on_delete=models.CASCADE,
        related_name="members",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="group_memberships",
    )
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "user"], name="uniq_group_user")
        ]

    def __str__(self):
        return f"{self.user} in {self.group.name}"


class GroupDevice(models.Model):
    group = models.ForeignKey(
        "groups.DeviceGroup",
        on_delete=models.CASCADE,
        related_name="devices",
    )
    device = models.ForeignKey(
        "locations.Device",
        on_delete=models.CASCADE,
        related_name="group_links",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["group", "device"], name="uniq_group_device")
        ]

    def __str__(self):
        return f"{self.device.device_id} in {self.group.name}"