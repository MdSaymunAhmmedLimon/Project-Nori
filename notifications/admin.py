from django.contrib import admin

from rides.admin import ReadOnlyAdmin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(ReadOnlyAdmin):
    list_display = ("user", "message", "created_at", "read_at")
