import uuid
from datetime import timedelta
from decimal import Decimal

from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.test import Client
from django.utils import timezone
from rest_framework.test import APITestCase

from notifications.models import Notification
from payments.models import Payment, Wallet, WalletTransaction
from rides.models import Ride, RideQuote
from rides.services import cancel_operational_ride
from rides.tests.test_workflow import make_driver


class CancellationReviewTests(APITestCase):
    def setUp(self):
        User = get_user_model()
        self.rider = User.objects.create_user(username="cancel-rider", email="cancel@review.test")
        self.other = User.objects.create_user(username="cancel-other", email="other@review.test")
        self.admin = User.objects.create_superuser(username="cancel-admin", email="admin@review.test", password="test-password")
        self.driver = make_driver("cancel-driver")
        self.quote = RideQuote.objects.create(rider=self.rider, pickup_label="Banani", destination_label="Gulshan",
            pickup_latitude="23.810300", pickup_longitude="90.412500", destination_latitude="23.792500", destination_longitude="90.416700",
            distance_km=3, estimated_minutes=10, fare=150, platform_fee=20, currency="BDT", expires_at=timezone.now() + timedelta(minutes=10))
        self.ride = Ride.objects.create(rider=self.rider, driver=self.driver, quote=self.quote, status="ACCEPTED", accepted_at=timezone.now(), request_id=uuid.uuid4())
        self.url = f"/api/rides/{self.ride.pk}/actions/"
        self.client.force_authenticate(self.rider)

    def test_preview_is_private_and_fee_changes_require_reconfirmation(self):
        self.assertEqual(self.client.get(self.url).data["fee"], "0.00")
        Ride.objects.filter(pk=self.ride.pk).update(accepted_at=timezone.now() - timedelta(minutes=10))
        response = self.client.post(self.url, {"action": "cancel", "expected_cancellation_fee": "0.00"}, format="json")
        self.assertEqual(response.status_code, 409)
        self.ride.refresh_from_db()
        self.assertEqual(self.ride.status, "ACCEPTED")
        self.assertFalse(Payment.objects.filter(ride=self.ride).exists())
        preview = self.client.get(self.url).data
        self.assertEqual(preview["fee"], "50.00")
        self.assertEqual(self.client.post(self.url, {"action": "cancel", "expected_cancellation_fee": preview["fee"], "reason": "Plans changed"}, format="json").status_code, 200)
        self.assertEqual(Payment.objects.get(ride=self.ride).amount, Decimal("50.00"))
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_driver_cancellation_has_no_fee_and_notifies_both_participants(self):
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.get(self.url).data["fee"], "0.00")
        self.assertEqual(self.client.post(self.url, {"action": "cancel", "expected_cancellation_fee": "0.00", "reason": "Vehicle problem"}, format="json").status_code, 200)
        self.assertEqual(Notification.objects.filter(ride_id=self.ride.pk, message__contains="Vehicle problem").count(), 2)
        self.assertFalse(Payment.objects.filter(ride=self.ride).exists())

    def test_operations_cancel_requires_permission_confirmation_and_reason(self):
        web = Client()
        url = f"/operations/rides/{self.ride.pk}/cancel/"
        web.force_login(self.other)
        self.assertEqual(web.post(url, {"reason": "Support", "confirmed": "yes"}).status_code, 403)
        web.force_login(self.admin)
        web.post(url, {"reason": "Support"})
        web.post(url, {"reason": "", "confirmed": "yes"})
        self.ride.refresh_from_db()
        self.assertEqual(self.ride.status, "ACCEPTED")
        web.post(url, {"reason": "Vehicle unavailable", "confirmed": "yes"})
        self.ride.refresh_from_db()
        self.assertEqual(self.ride.status, "CANCELLED")
        self.assertEqual(self.ride.cancelled_by_id, self.admin.pk)
        self.assertEqual(self.ride.cancellation_fee, 0)
        self.assertEqual(Notification.objects.filter(ride_id=self.ride.pk).count(), 2)
        web.post(url, {"reason": "Vehicle unavailable", "confirmed": "yes"})
        self.assertEqual(Notification.objects.filter(ride_id=self.ride.pk).count(), 2)
        self.assertTrue(LogEntry.objects.filter(user=self.admin, change_message__contains="Vehicle unavailable").exists())

    def test_started_completed_and_cancelled_trips_cannot_be_cancelled(self):
        web = Client()
        web.force_login(self.admin)
        for status in ("IN_PROGRESS", "COMPLETED", "CANCELLED"):
            Ride.objects.filter(pk=self.ride.pk).update(status=status)
            self.assertEqual(self.client.get(self.url).status_code, 409)
            web.post(f"/operations/rides/{self.ride.pk}/cancel/", {"reason": "Support", "confirmed": "yes"})
            self.ride.refresh_from_db()
            self.assertEqual(self.ride.status, status)

    def test_operational_demo_refund_happens_only_once(self):
        from payments.demo import prepay_quote
        Wallet.objects.create(user=self.rider, balance=200, currency="BDT")
        prepay_quote(self.rider, self.quote, "WALLET")
        self.ride.payment_method = "WALLET"
        self.ride.save()
        Payment.objects.create(ride=self.ride, amount=150, platform_fee=20, currency="BDT", method="WALLET", status="PAID")
        cancel_operational_ride(self.admin, self.ride.pk, reason="Unable to dispatch")
        cancel_operational_ride(self.admin, self.ride.pk, reason="Unable to dispatch")
        self.assertEqual(Wallet.objects.get(user=self.rider).balance, 200)
        self.assertEqual(WalletTransaction.objects.filter(kind="REFUND").count(), 1)
        self.assertEqual(Payment.objects.get(ride=self.ride).status, "REFUNDED")
