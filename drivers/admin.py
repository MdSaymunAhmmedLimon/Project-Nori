from django.contrib import admin
from django.utils import timezone

from .models import DriverDocument, DriverProfile, PayoutRequest, Vehicle



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
    list_display = ("license_plate", "driver", "vehicle_class", "make", "model", "year")
    list_filter = ("vehicle_class",)
    search_fields = ("license_plate", "driver__user__username", "make", "model")
    readonly_fields = ("created_at", "updated_at")



@admin.register(DriverDocument)
class DriverDocumentAdmin(admin.ModelAdmin):
    list_display = ("driver", "doc_type", "review_status", "uploaded_at", "reviewed_at")
    list_filter = ("review_status", "doc_type")
    search_fields = ("driver__user__username",)
    readonly_fields = ("uploaded_at",)
    actions = ["accept_documents", "reject_documents"]

    @admin.action(description="Accept selected documents")
    def accept_documents(self, request, queryset):
        count = queryset.update(
            review_status="ACCEPTED",
            review_note="",
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Accepted {count} document(s).")

    @admin.action(description="Reject selected documents")
    def reject_documents(self, request, queryset):
        count = queryset.update(
            review_status="REJECTED",
            review_note="Document could not be verified. Please re-upload a clear copy.",
            reviewed_at=timezone.now(),
        )
        self.message_user(request, f"Rejected {count} document(s).")


@admin.register(PayoutRequest)
class PayoutRequestAdmin(admin.ModelAdmin):
    list_display = ("driver", "amount", "payout_method", "status", "requested_at", "processed_at")
    list_filter = ("status", "payout_method")
    search_fields = ("driver__user__username", "account_details")
    readonly_fields = ("requested_at",)
    actions = ["approve_payouts", "mark_as_paid", "reject_payouts"]

    @admin.action(description="Approve selected payout requests")
    def approve_payouts(self, request, queryset):
        count = queryset.filter(status="PENDING").update(
            status="APPROVED", processed_at=timezone.now()
        )
        self.message_user(request, f"Approved {count} payout request(s).")

    @admin.action(description="Mark selected payout requests as paid")
    def mark_as_paid(self, request, queryset):
        count = queryset.filter(status__in=["PENDING", "APPROVED"]).update(
            status="PAID", processed_at=timezone.now()
        )
        self.message_user(request, f"Marked {count} payout request(s) as paid.")

    @admin.action(description="Reject selected payout requests")
    def reject_payouts(self, request, queryset):
        count = queryset.filter(status="PENDING").update(
            status="REJECTED", processed_at=timezone.now()
        )
        self.message_user(request, f"Rejected {count} payout request(s).")


