from django.conf import settings
from django.db import models


class Notification(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=250)
    title = models.CharField(max_length=100, blank=True)
    category = models.CharField(max_length=20, default="SYSTEM", choices=[("SYSTEM", "System"), ("RIDE", "Ride"), ("POSITION", "Position"), ("ANNOUNCEMENT", "Announcement")])
    action_url = models.CharField(max_length=250, blank=True)
    sent_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="sent_notifications")
    ride_id = models.UUIDField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "-created_at", "-id"], name="notification_user_feed_idx"),
            models.Index(fields=["user"], condition=models.Q(read_at__isnull=True), name="notification_unread_idx"),
        ]
