from django.contrib import admin

from rides.admin import ReadOnlyAdmin

from .models import Payment, StripeEvent


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("ride", "amount", "currency", "method", "status", "paid_at")
    list_filter = ("status", "method", "currency")


admin.site.register(StripeEvent, ReadOnlyAdmin)
