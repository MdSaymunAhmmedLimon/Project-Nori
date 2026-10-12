from django.contrib import admin
from django.utils import timezone

from .models import (
    CarpoolPassenger, CarpoolRide, PromoCode, PromoUsage, Rating, Ride,
    RideEvent, RideMessage, RideOffer, RideQuote, RideStop, RideTip,
    SavedPlace, ScheduledRide, SOSAlert, SurgePricingRule, TrackPoint,
)



class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Ride)
class RideAdmin(ReadOnlyAdmin):
    list_display = ("id", "rider", "driver", "status", "payment_method", "created_at")
    list_filter = ("status", "payment_method", "created_at")
    search_fields = ("id", "rider__username", "driver__username")


@admin.register(Rating)
class RatingAdmin(ReadOnlyAdmin):
    list_display = ("ride", "author", "recipient", "score", "created_at")
    list_filter = ("score",)


@admin.register(SurgePricingRule)
class SurgePricingRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "multiplier", "hour_start", "hour_end", "day_of_week", "is_active")
    list_filter = ("is_active", "day_of_week")
    search_fields = ("name",)


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ("code", "discount_percent", "discount_flat", "min_fare", "uses", "max_uses", "is_active", "expires_at")
    list_filter = ("is_active",)
    search_fields = ("code", "description")


@admin.register(PromoUsage)
class PromoUsageAdmin(ReadOnlyAdmin):
    list_display = ("promo", "rider", "ride", "discount_applied", "created_at")
    search_fields = ("promo__code", "rider__username")


@admin.register(SOSAlert)
class SOSAlertAdmin(admin.ModelAdmin):
    list_display = ("ride", "raised_by", "message", "created_at", "resolved_at", "resolved_by")
    list_filter = ("resolved_at",)
    search_fields = ("ride__id", "raised_by__username", "message")
    readonly_fields = ("ride", "raised_by", "message", "created_at")
    actions = ["resolve_alerts"]

    @admin.action(description="Mark selected alerts as resolved")
    def resolve_alerts(self, request, queryset):
        count = queryset.filter(resolved_at__isnull=True).update(
            resolved_at=timezone.now(),
            resolved_by=request.user,
        )
        self.message_user(request, f"Resolved {count} alert(s).")


@admin.register(ScheduledRide)
class ScheduledRideAdmin(ReadOnlyAdmin):
    list_display = ("id", "rider", "scheduled_for", "ride", "cancelled_at", "created_at")
    list_filter = ("scheduled_for", "cancelled_at")
    search_fields = ("rider__username",)


@admin.register(CarpoolRide)
class CarpoolRideAdmin(ReadOnlyAdmin):
    list_display = ("id", "ride", "total_seats", "available_seats", "fare_per_seat", "is_open", "created_at")
    list_filter = ("is_open",)


@admin.register(CarpoolPassenger)
class CarpoolPassengerAdmin(ReadOnlyAdmin):
    list_display = ("carpool", "rider", "seats", "fare_paid", "status", "joined_at")
    list_filter = ("status",)
    search_fields = ("rider__username",)


admin.site.register(RideQuote, ReadOnlyAdmin)
admin.site.register(RideOffer, ReadOnlyAdmin)
admin.site.register(RideEvent, ReadOnlyAdmin)
admin.site.register(TrackPoint, ReadOnlyAdmin)
admin.site.register(RideTip, ReadOnlyAdmin)
admin.site.register(RideMessage, ReadOnlyAdmin)
admin.site.register(SavedPlace, ReadOnlyAdmin)
admin.site.register(RideStop, ReadOnlyAdmin)

