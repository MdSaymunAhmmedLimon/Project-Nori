from django.contrib import admin
from django.utils import timezone

from .models import DriverProfile, Vehicle


@admin.register(DriverProfile)
class DriverProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "license_number", "license_expiry_date", "verification_status", "is_online")
    list_filter = ("verification_status", "is_online")
    search_fields = ("user__username", "license_number")
    readonly_fields = ("created_at", "updated_at", "is_online", "latitude", "longitude", "location_updated_at")
    actions = ["approve_drivers"]

    @admin.action(description="Approve selected complete driver profiles")
    def approve_drivers(self, request, queryset):
        count = queryset.filter(license_expiry_date__gte=timezone.localdate(), vehicle__isnull=False, user__is_active=True).update(verification_status="APPROVED", review_note="")
        self.message_user(request, f"Approved {count} complete driver profiles.")

    def save_model(self, request, obj, form, change):
        if obj.verification_status != "APPROVED":
            obj.is_online = False
        super().save_model(request, obj, form, change)


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ("license_plate", "driver", "make", "model", "year")
    search_fields = ("license_plate", "driver__user__username", "make", "model")
    readonly_fields = ("created_at", "updated_at")
