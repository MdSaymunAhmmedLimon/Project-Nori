import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from payments.models import Payment
from rides.models import Ride, RideQuote


class ReceiptTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rider = get_user_model().objects.create_user(username="receipt_rider", email="rider@example.com", password=secrets.token_urlsafe(24))
        cls.driver = get_user_model().objects.create_user(username="receipt_driver", email="driver@example.com", role="DRIVER")
        cls.stranger = get_user_model().objects.create_user(username="stranger", email="stranger@example.com", is_staff=True)
        quote = RideQuote.objects.create(
            rider=cls.rider, pickup_label="Banani", destination_label="Gulshan",
            pickup_latitude="23.810300", pickup_longitude="90.412500",
            destination_latitude="23.780600", destination_longitude="90.419300",
            distance_km="4.39", estimated_minutes=11, fare="150.00", platform_fee="22.50",
            currency="BDT", expires_at=timezone.now() + timedelta(minutes=10),
        )
        cls.ride = Ride.objects.create(rider=cls.rider, driver=cls.driver, quote=quote, request_id=uuid.uuid4(), status="COMPLETED", completed_at=timezone.now())
        cls.payment = Payment.objects.create(ride=cls.ride, amount="150.00", platform_fee="22.50", currency="BDT", method="CASH")
        cls.url = reverse("webapp:receipt", args=[cls.ride.pk])

    def test_receipt_requires_login_and_participation_even_for_staff(self):
        self.assertRedirects(self.client.get(self.url), f"/login/?next={self.url}")
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_pending_summary_does_not_claim_payment_was_received(self):
        self.client.force_login(self.rider)
        response = self.client.get(self.url)
        self.assertContains(response, "Trip summary")
        self.assertContains(response, "Payment is still pending")
        self.assertNotContains(response, "Payment received.")
        self.assertNotContains(response, "Platform fee")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_paid_receipt_uses_recorded_payment_and_only_driver_sees_net(self):
        self.payment.status = "PAID"
        self.payment.paid_at = timezone.now()
        self.payment.save()
        RideQuote.objects.filter(pk=self.ride.quote_id).update(fare=Decimal("999.00"))
        for user in (self.rider, self.driver):
            self.client.force_login(user)
            response = self.client.get(self.url)
            self.assertContains(response, "BDT 150.00")
            self.assertContains(response, "Payment received.")
            self.assertContains(response, "Banani")
            self.assertContains(response, "Gulshan")
            self.assertNotContains(response, "999.00")
            self.assertNotContains(response, self.rider.email)
            if user == self.driver:
                self.assertContains(response, "BDT 127.50")
                self.assertContains(response, "Platform fee")
            else:
                self.assertNotContains(response, "Your net earnings")

    def test_cancelled_or_missing_trip_has_no_receipt(self):
        self.client.force_login(self.rider)
        Ride.objects.filter(pk=self.ride.pk).update(status="CANCELLED")
        self.assertEqual(self.client.get(self.url).status_code, 404)
        self.assertEqual(self.client.get(reverse("webapp:receipt", args=[uuid.uuid4()])).status_code, 404)

    def test_labels_are_escaped_and_receipt_is_read_only(self):
        self.client.force_login(self.rider)
        RideQuote.objects.filter(pk=self.ride.quote_id).update(pickup_label="<script>alert(1)</script>")
        response = self.client.get(self.url)
        self.assertContains(response, "&lt;script&gt;")
        self.assertNotContains(response, "<script>alert")
        self.assertEqual(self.client.post(self.url).status_code, 405)
