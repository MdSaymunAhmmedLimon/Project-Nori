from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from drivers.models import DriverDocument, DriverProfile, Vehicle
from payments.models import Payment
from rides.models import Ride, RideQuote

User = get_user_model()


class DriverNewFeaturesTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.driver = User.objects.create_user(
            username="test_driver", email="test_driver@example.com", password="password123", role="DRIVER"
        )
        cls.rider = User.objects.create_user(
            username="test_rider", email="test_rider@example.com", password="password123", role="RIDER"
        )
        cls.profile = DriverProfile.objects.create(
            user=cls.driver,
            license_number="DL-FEATURE-01",
            license_expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status=DriverProfile.Verification.APPROVED,
        )
        cls.vehicle = Vehicle.objects.create(
            driver=cls.profile,
            license_plate="PLATE-01",
            make="Toyota",
            model="Aqua",
            color="White",
            year=2020,
            seats=4,
        )

    def setUp(self):
        cache.clear()

    def test_document_upload_and_listing(self):
        self.client.force_authenticate(self.driver)
        dummy_file = SimpleUploadedFile("license.jpg", b"fake-image-bytes", content_type="image/jpeg")

        # Upload document
        url = reverse("drivers:documents")
        response = self.client.post(url, {"doc_type": "LICENSE_FRONT", "file": dummy_file}, format="multipart")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["doc_type"], "LICENSE_FRONT")
        self.assertEqual(response.data["review_status"], "PENDING")

        # List documents
        list_response = self.client.get(url)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.data["results"] if "results" in list_response.data else list_response.data), 1)

    def test_document_reupload_resets_status(self):
        self.client.force_authenticate(self.driver)
        file1 = SimpleUploadedFile("license.jpg", b"first", content_type="image/jpeg")
        url = reverse("drivers:documents")
        self.client.post(url, {"doc_type": "LICENSE_FRONT", "file": file1}, format="multipart")

        # Mark as approved
        doc = DriverDocument.objects.get(driver=self.profile, doc_type="LICENSE_FRONT")
        doc.review_status = DriverDocument.ReviewStatus.ACCEPTED
        doc.save()

        # Re-upload replaces and resets to PENDING
        file2 = SimpleUploadedFile("license2.jpg", b"second", content_type="image/jpeg")
        response = self.client.post(url, {"doc_type": "LICENSE_FRONT", "file": file2}, format="multipart")
        self.assertEqual(response.status_code, 201)
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, DriverDocument.ReviewStatus.PENDING)

    def test_earnings_dashboard(self):
        self.client.force_authenticate(self.driver)

        # Create completed ride with payment
        quote = RideQuote.objects.create(
            rider=self.rider,
            pickup_label="A",
            destination_label="B",
            pickup_latitude=Decimal("23.8103"),
            pickup_longitude=Decimal("90.4125"),
            destination_latitude=Decimal("23.8203"),
            destination_longitude=Decimal("90.4225"),
            distance_km=Decimal("5.00"),
            estimated_minutes=15,
            fare=Decimal("200.00"),
            platform_fee=Decimal("30.00"),
            currency="BDT",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        import uuid
        ride = Ride.objects.create(
            rider=self.rider,
            driver=self.driver,
            quote=quote,
            request_id=uuid.uuid4(),
            status=Ride.Status.COMPLETED,
        )
        Payment.objects.create(
            ride=ride,
            amount=Decimal("200.00"),
            platform_fee=Decimal("30.00"),
            currency="BDT",
            method="CASH",
            status=Payment.Status.PAID,
        )

        url = reverse("drivers:earnings")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_revenue"], "200.00")
        self.assertEqual(response.data["total_platform_fees"], "30.00")
        self.assertEqual(response.data["net_earnings"], "170.00")
        self.assertEqual(response.data["ride_count"], 1)
        self.assertEqual(len(response.data["daily_breakdown"]), 1)
