import uuid

from django.conf import settings
from django.db import models

DEMO_METHODS = ("WALLET", "SSL_DEMO")
PAYMENT_METHODS = [("CASH", "Cash"), ("CARD", "Card"), ("WALLET", "Nori Wallet (demo)"), ("SSL_DEMO", "SSLCOMMERZ (demo)")]
DEMO_CHANNELS = [("BKASH", "bKash"), ("NAGAD", "Nagad"), ("CARD", "Card"), ("BANK", "Internet banking")]


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Awaiting payment"
        PAID = "PAID", "Paid"
        REFUNDED = "REFUNDED", "Refunded (demo)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.OneToOneField("rides.Ride", on_delete=models.PROTECT, related_name="payment")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=10, choices=PAYMENT_METHODS)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    checkout_session = models.CharField(max_length=255, blank=True)
    checkout_url = models.URLField(max_length=2048, blank=True)
    checkout_expires_at = models.DateTimeField(null=True, blank=True)
    checkout_attempt = models.PositiveIntegerField(default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    @property
    def is_demo(self):
        return self.method in DEMO_METHODS


class Wallet(models.Model):
    """Non-withdrawable demo credits, never real stored value."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="nori_wallet")
    currency = models.CharField(max_length=3)
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    class Meta:
        constraints = [models.CheckConstraint(condition=models.Q(balance__gte=0), name="wallet_nonnegative")]


class DemoPrepayment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quote = models.OneToOneField("rides.RideQuote", on_delete=models.PROTECT, related_name="demo_prepayment")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    method = models.CharField(max_length=10, choices=[choice for choice in PAYMENT_METHODS if choice[0] in DEMO_METHODS])
    channel = models.CharField(max_length=8, choices=DEMO_CHANNELS, blank=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    status = models.CharField(max_length=10, choices=[("PAID", "Prepaid (demo)"), ("REFUNDED", "Refunded (demo)")], default="PAID")
    created_at = models.DateTimeField(auto_now_add=True)
    refunded_at = models.DateTimeField(null=True, blank=True)


class WalletTransaction(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.PROTECT, related_name="transactions")
    kind = models.CharField(max_length=8, choices=[("TOPUP", "Demo top-up"), ("RIDE", "Ride prepayment"), ("REFUND", "Cancellation refund")])
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    balance_after = models.DecimalField(max_digits=12, decimal_places=2)
    request_id = models.UUIDField(null=True, blank=True)
    prepayment = models.ForeignKey(DemoPrepayment, null=True, blank=True, on_delete=models.PROTECT)
    channel = models.CharField(max_length=8, choices=DEMO_CHANNELS, blank=True)
    description = models.CharField(max_length=240)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        constraints = [
            models.UniqueConstraint(fields=["wallet", "request_id"], name="wallet_topup_idempotent"),
            models.UniqueConstraint(fields=["prepayment", "kind"], name="wallet_booking_entry_once"),
        ]


class StripeEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    received_at = models.DateTimeField(auto_now_add=True)
