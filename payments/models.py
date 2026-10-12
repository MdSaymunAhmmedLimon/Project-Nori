import uuid

from django.db import models


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "PENDING", "Awaiting payment"
        PAID = "PAID", "Paid"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.OneToOneField("rides.Ride", on_delete=models.PROTECT, related_name="payment")
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    method = models.CharField(max_length=4, choices=[("CASH", "Cash"), ("CARD", "Card")])
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    checkout_session = models.CharField(max_length=255, blank=True)
    checkout_url = models.URLField(max_length=2048, blank=True)
    checkout_expires_at = models.DateTimeField(null=True, blank=True)
    checkout_attempt = models.PositiveIntegerField(default=0)
    paid_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class StripeEvent(models.Model):
    event_id = models.CharField(max_length=255, unique=True)
    event_type = models.CharField(max_length=100)
    received_at = models.DateTimeField(auto_now_add=True)
