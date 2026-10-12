import hashlib
import hmac
import json
import secrets
import time
import uuid
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from payments.models import Payment, StripeEvent
from rides.models import Ride, RideQuote
from rides.tests.test_workflow import make_driver


@override_settings(CARD_PAYMENTS_ENABLED=True)
class CheckoutTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.rider = get_user_model().objects.create_user(username="rider", email="rider@example.com")
        self.driver = make_driver("driver")
        quote = RideQuote.objects.create(rider=self.rider, pickup_label="A", destination_label="B",
            pickup_latitude=23.8, pickup_longitude=90.4, destination_latitude=23.81, destination_longitude=90.41,
            distance_km=2, estimated_minutes=5, fare=Decimal("100.00"), platform_fee=Decimal("15.00"),
            currency="BDT", expires_at=timezone.now()+timedelta(minutes=10))
        self.ride = Ride.objects.create(rider=self.rider, driver=self.driver, quote=quote, request_id=uuid.uuid4(), status="COMPLETED", payment_method="CARD", completed_at=timezone.now())
        self.payment = Payment.objects.create(ride=self.ride, amount=100, platform_fee=15, currency="BDT", method="CARD")
        self.secret = secrets.token_urlsafe(32)
        self.client.force_authenticate(self.rider)

    def event(self, **changes):
        session = {"id":"cs_test_example", "payment_status":"paid", "currency":"bdt", "amount_total":10000, "metadata":{"payment_id":str(self.payment.pk)}} | changes
        return {"id":"evt_example", "type":"checkout.session.completed", "data":{"object":session}}

    def webhook(self, event, *, valid=True):
        raw = json.dumps(event).encode()
        timestamp = int(time.time())
        signature = hmac.new(self.secret.encode(), f"{timestamp}.".encode()+raw, hashlib.sha256).hexdigest() if valid else "invalid"
        with override_settings(STRIPE_WEBHOOK_SECRET=self.secret):
            return self.client.post("/api/payments/webhook/stripe/", raw, content_type="application/json", HTTP_STRIPE_SIGNATURE=f"t={timestamp},v1={signature}")

    @patch("payments.services.stripe.checkout.Session.create")
    def test_checkout_creation_is_idempotent_and_server_priced(self, create):
        create.return_value = SimpleNamespace(id="cs_test_example", url="https://checkout.stripe.com/test/example", expires_at=int(time.time())+3600)
        first = self.client.post(f"/api/payments/{self.ride.pk}/checkout/", {})
        second = self.client.post(f"/api/payments/{self.ride.pk}/checkout/", {})
        self.assertEqual(first.status_code, 200, first.data)
        self.assertEqual(first.data, second.data)
        self.assertEqual(create.call_count, 1)
        args = create.call_args.kwargs
        self.assertEqual(args["line_items"][0]["price_data"]["unit_amount"], 10000)
        self.assertIn(str(self.payment.pk), args["idempotency_key"])
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PENDING")

    def test_signed_webhook_marks_paid_once(self):
        self.payment.checkout_session = "cs_test_example"
        self.payment.save()
        self.assertEqual(self.webhook(self.event()).status_code, 200)
        self.assertEqual(self.webhook(self.event()).status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PAID")
        self.assertEqual(StripeEvent.objects.count(), 1)
        self.assertEqual(self.rider.notifications.count(), 1)

    @patch("payments.services.stripe.checkout.Session.retrieve")
    @patch("payments.services.stripe.checkout.Session.create")
    def test_expired_checkout_is_reconciled_before_creating_another(self, create, retrieve):
        self.payment.checkout_session = "cs_previous"
        self.payment.checkout_expires_at = timezone.now() - timedelta(minutes=5)
        self.payment.save()
        retrieve.return_value = SimpleNamespace(payment_status="paid", amount_total=10000, currency="bdt", metadata={"payment_id":str(self.payment.pk)})
        response = self.client.post(f"/api/payments/{self.ride.pk}/checkout/", {})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data["url"])
        create.assert_not_called()
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PAID")

    def test_bad_signature_cannot_mark_paid(self):
        self.assertEqual(self.webhook(self.event(), valid=False).status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PENDING")

    def test_amount_currency_metadata_and_session_are_verified(self):
        self.payment.checkout_session = "cs_test_example"
        self.payment.save()
        for changes in ({"amount_total":1}, {"currency":"usd"}, {"metadata":{}}, {"id":"cs_wrong"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.webhook(self.event(**changes)).status_code, 400)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PENDING")

    def test_unpaid_session_does_not_mark_paid(self):
        self.payment.checkout_session = "cs_test_example"
        self.payment.save()
        self.assertEqual(self.webhook(self.event(payment_status="unpaid")).status_code, 200)
        self.payment.refresh_from_db()
        self.assertEqual(self.payment.status, "PENDING")

    def test_driver_cannot_create_checkout_or_mark_card_as_cash(self):
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.post(f"/api/payments/{self.ride.pk}/checkout/", {}).status_code, 404)
        self.assertEqual(self.client.post(f"/api/payments/{self.ride.pk}/cash/", {}).status_code, 409)
