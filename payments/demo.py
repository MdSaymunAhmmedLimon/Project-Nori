"""Local payment simulation. This module never calls a payment provider."""
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from rides.services import Conflict, require_role
from .models import DEMO_CHANNELS, DEMO_METHODS, DemoPrepayment, Payment, Wallet, WalletTransaction


def check_demo(channel, outcome):
    if channel not in dict(DEMO_CHANNELS):
        raise ValidationError({"demo_channel": "Choose a demo payment option."})
    if outcome != "SUCCESS":
        raise ValidationError({"detail": "Demo payment cancelled. Nothing was charged." if outcome == "CANCELLED" else "Demo payment failed. Nothing was charged; please try again."})


def locked_wallet(user, currency):
    wallet, _ = Wallet.objects.get_or_create(user=user, defaults={"currency": currency})
    wallet = Wallet.objects.select_for_update().get(pk=wallet.pk)
    if wallet.currency != currency:
        raise Conflict("Your wallet currency does not match this fare.")
    return wallet


@transaction.atomic
def top_up(user, *, amount, request_id, demo_channel, demo_outcome):
    require_role(user, "RIDER")
    get_user_model().objects.select_for_update().get(pk=user.pk)
    wallet = locked_wallet(user, settings.RIDE_CURRENCY)
    existing = wallet.transactions.filter(request_id=request_id).first()
    if existing:
        if existing.amount != amount or existing.channel != demo_channel:
            raise Conflict("This top-up reference was already used for different details.")
        return existing
    check_demo(demo_channel, demo_outcome)
    if amount < Decimal("10") or amount > Decimal("10000"):
        raise ValidationError({"amount": "Choose between 10 and 10,000 demo credits."})
    if wallet.balance + amount > Decimal("1000000"):
        raise Conflict("The demo wallet balance limit is 1,000,000 credits.")
    wallet.balance += amount
    wallet.save(update_fields=["balance"])
    return WalletTransaction.objects.create(wallet=wallet, kind="TOPUP", amount=amount,
        balance_after=wallet.balance, request_id=request_id, channel=demo_channel,
        description=f"SSLCOMMERZ demo top-up · {dict(DEMO_CHANNELS)[demo_channel]}")


def prepay_quote(user, quote, method, *, demo_channel="", demo_outcome="", reuse=False):
    """Called inside booking's atomic transaction after quote/owner validation."""
    if method not in DEMO_METHODS:
        return None
    existing = DemoPrepayment.objects.select_for_update().filter(quote=quote).first()
    if existing:
        if reuse and existing.status == "PAID" and existing.method == method:
            return existing
        raise Conflict("This quote already has a demo payment. Get a new fare.")
    if reuse:
        raise Conflict("This reservation has no confirmed demo prepayment.")
    if method == "SSL_DEMO":
        check_demo(demo_channel, demo_outcome)
    wallet = None
    if method == "WALLET":
        wallet = locked_wallet(user, quote.currency)
        if wallet.balance < quote.fare:
            raise Conflict("Insufficient Nori Wallet credits. Top up your demo wallet or choose another payment option.")
    payment = DemoPrepayment.objects.create(quote=quote, user=user, method=method,
        channel=demo_channel if method == "SSL_DEMO" else "", amount=quote.fare, currency=quote.currency)
    if wallet:
        wallet.balance -= payment.amount
        wallet.save(update_fields=["balance"])
        WalletTransaction.objects.create(wallet=wallet, prepayment=payment, kind="RIDE",
            amount=-payment.amount, balance_after=wallet.balance,
            description=f"Prepaid ride to {quote.destination_label}"[:240])
    return payment


def attach_prepayment(ride, prepayment):
    if prepayment:
        Payment.objects.get_or_create(ride=ride, defaults={"amount": prepayment.amount,
            "platform_fee": ride.quote.platform_fee, "currency": prepayment.currency,
            "method": prepayment.method, "status": "PAID", "paid_at": prepayment.created_at})


@transaction.atomic
def refund_quote(quote_id):
    payment = DemoPrepayment.objects.select_for_update().filter(quote_id=quote_id).first()
    if not payment or payment.status == "REFUNDED":
        return
    if payment.method == "WALLET":
        wallet = Wallet.objects.select_for_update().get(user_id=payment.user_id)
        wallet.balance += payment.amount
        wallet.save(update_fields=["balance"])
        WalletTransaction.objects.create(wallet=wallet, prepayment=payment, kind="REFUND",
            amount=payment.amount, balance_after=wallet.balance, description="Cancelled booking · demo credits returned")
    payment.status, payment.refunded_at = "REFUNDED", timezone.now()
    payment.save(update_fields=["status", "refunded_at"])
    Payment.objects.filter(ride__quote_id=quote_id, method__in=DEMO_METHODS).update(status="REFUNDED")
