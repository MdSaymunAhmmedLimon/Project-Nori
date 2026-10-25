from django.contrib import admin

from rides.admin import ReadOnlyAdmin

from .models import DemoPrepayment, Payment, StripeEvent, Wallet, WalletTransaction


@admin.register(Payment)
class PaymentAdmin(ReadOnlyAdmin):
    list_display = ("ride", "amount", "currency", "method", "status", "paid_at")
    list_filter = ("status", "method", "currency")


admin.site.register(StripeEvent, ReadOnlyAdmin)


@admin.register(Wallet)
class WalletAdmin(ReadOnlyAdmin):
    list_display = ("user", "balance", "currency")
    search_fields = ("user__username",)


@admin.register(WalletTransaction)
class WalletTransactionAdmin(ReadOnlyAdmin):
    list_display = ("wallet", "kind", "amount", "balance_after", "channel", "created_at")
    list_filter = ("kind", "channel")


@admin.register(DemoPrepayment)
class DemoPrepaymentAdmin(ReadOnlyAdmin):
    list_display = ("user", "quote", "method", "amount", "currency", "status", "created_at")
    list_filter = ("method", "status", "channel")
