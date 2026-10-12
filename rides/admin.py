from django.contrib import admin

from .models import Rating, Ride, RideEvent, RideOffer, RideQuote, TrackPoint


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


admin.site.register(RideQuote, ReadOnlyAdmin)
admin.site.register(RideOffer, ReadOnlyAdmin)
admin.site.register(RideEvent, ReadOnlyAdmin)
admin.site.register(TrackPoint, ReadOnlyAdmin)
