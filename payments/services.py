from datetime import datetime, timezone as dt_timezone

import stripe
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from notifications.services import notify
from rides.services import Conflict

from .models import Payment, StripeEvent


def mark_paid(payment):
    if payment.status == Payment.Status.PAID:
        return
    payment.status, payment.paid_at = Payment.Status.PAID, timezone.now()
    payment.save(update_fields=["status", "paid_at"])
    for user_id in (payment.ride.rider_id, payment.ride.driver_id):
        notify(user_id, "Payment received. Your trip receipt is ready.", payment.ride_id)


@transaction.atomic
def confirm_cash(user, ride_id):
    payment = Payment.objects.select_for_update().select_related("ride").filter(ride_id=ride_id, ride__driver=user).first()
    if not payment:
        raise NotFound()
    if payment.method != "CASH" or payment.ride.status != "COMPLETED":
        raise Conflict("Only completed cash rides can be marked as collected.")
    mark_paid(payment)
    return payment


@transaction.atomic
def create_checkout(user, ride_id):
    if not settings.CARD_PAYMENTS_ENABLED:
        raise ValidationError({"detail": "Card payments are not configured."})
    payment = Payment.objects.select_for_update().select_related("ride").filter(ride_id=ride_id, ride__rider=user).first()
    if not payment:
        raise NotFound()
    if payment.method != "CARD" or payment.ride.status != "COMPLETED" or payment.status == "PAID":
        raise Conflict("This trip does not need a card payment.")
    if payment.checkout_url and payment.checkout_expires_at and payment.checkout_expires_at > timezone.now():
        return payment.checkout_url
    if payment.checkout_session:
        try:
            previous = stripe.checkout.Session.retrieve(payment.checkout_session, api_key=settings.STRIPE_SECRET_KEY)
        except stripe.StripeError as exc:
            raise ValidationError({"detail": "Could not check the previous payment. Please try again."}) from exc
        if previous.payment_status == "paid":
            if (previous.amount_total != int(payment.amount * 100) or previous.currency.upper() != payment.currency
                    or previous.metadata.get("payment_id") != str(payment.pk)):
                raise Conflict("The provider payment does not match this trip.")
            mark_paid(payment)
            return None
        if previous.status != "expired":
            raise Conflict("Your previous payment is still open or processing. Please wait for confirmation.")
    attempt = payment.checkout_attempt + 1
    try:
        session = stripe.checkout.Session.create(
            api_key=settings.STRIPE_SECRET_KEY, mode="payment", payment_method_types=["card"],
            client_reference_id=str(payment.pk), metadata={"payment_id": str(payment.pk)},
            line_items=[{"price_data": {"currency": payment.currency.lower(), "unit_amount": int(payment.amount * 100),
                "product_data": {"name": f"Ride {str(payment.ride_id)[:8]}"}}, "quantity": 1}],
            success_url=f"{settings.PUBLIC_BASE_URL}/app/?ride={payment.ride_id}&payment=returned",
            cancel_url=f"{settings.PUBLIC_BASE_URL}/app/?ride={payment.ride_id}",
            idempotency_key=f"ride-payment-{payment.pk}-{attempt}",
        )
    except stripe.StripeError as exc:
        raise ValidationError({"detail": "The payment provider is unavailable. Please try again."}) from exc
    payment.checkout_session, payment.checkout_url, payment.checkout_attempt = session.id, session.url, attempt
    payment.checkout_expires_at = datetime.fromtimestamp(session.expires_at, tz=dt_timezone.utc)
    payment.save(update_fields=["checkout_session", "checkout_url", "checkout_attempt", "checkout_expires_at"])
    return payment.checkout_url


@transaction.atomic
def process_stripe_event(event):
    if StripeEvent.objects.filter(event_id=event["id"]).exists():
        return
    if event["type"] not in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        return
    session = event["data"]["object"]
    payment = Payment.objects.select_for_update().select_related("ride").filter(checkout_session=session["id"]).first()
    if not payment:
        raise ValidationError({"detail": "Unknown payment session."})
    if session.get("payment_status") != "paid":
        return
    if (payment.method != "CARD" or session.get("currency", "").upper() != payment.currency
            or session.get("amount_total") != int(payment.amount * 100)
            or session.get("metadata", {}).get("payment_id") != str(payment.pk)):
        raise ValidationError({"detail": "Payment details do not match the trip."})
    mark_paid(payment)
    StripeEvent.objects.get_or_create(event_id=event["id"], defaults={"event_type": event["type"]})
