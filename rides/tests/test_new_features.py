from datetime import timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from drivers.models import DriverProfile, Vehicle
from payments.models import Payment
from rides.models import (
    CarpoolPassenger, CarpoolRide, PromoCode, PromoUsage, Ride,
    RideQuote, ScheduledRide, SOSAlert, SurgePricingRule,
)

User = get_user_model()


class RidesNewFeaturesTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rider = User.objects.create_user(
            username="rider_user", email="rider@example.com", password="password123", role="RIDER"
        )
        cls.driver = User.objects.create_user(
            username="driver_user", email="driver@example.com", password="password123", role="DRIVER"
        )
        cls.staff_user = User.objects.create_user(
            username="staff_user", email="staff@example.com", password="password123", is_staff=True
        )
        cls.profile = DriverProfile.objects.create(
            user=cls.driver,
            license_number="DL-RIDE-TEST",
            license_expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status=DriverProfile.Verification.APPROVED,
            is_online=True,
            latitude=Decimal("23.8103"),
            longitude=Decimal("90.4125"),
            location_updated_at=timezone.now(),
        )
        cls.vehicle = Vehicle.objects.create(
            driver=cls.profile,
            license_plate="PLATE-RIDE-01",
            make="Toyota",
            model="Aqua",
            color="White",
            year=2021,
            seats=4,
        )

    def setUp(self):
        cache.clear()

    def create_quote(self, user=None, fare=Decimal("150.00"), surge_multiplier=Decimal("1.00")):
        return RideQuote.objects.create(
            rider=user or self.rider,
            pickup_label="Banani",
            destination_label="Gulshan",
            pickup_latitude=Decimal("23.7937"),
            pickup_longitude=Decimal("90.4066"),
            destination_latitude=Decimal("23.7925"),
            destination_longitude=Decimal("90.4178"),
            distance_km=Decimal("3.00"),
            estimated_minutes=10,
            fare=fare,
            platform_fee=Decimal("22.50"),
            currency="BDT",
            expires_at=timezone.now() + timedelta(minutes=15),
            surge_multiplier=surge_multiplier,
        )

    # 1. Scheduled Rides
    def test_schedule_and_cancel_ride(self):
        self.client.force_authenticate(self.rider)
        quote = self.create_quote()
        target_time = timezone.now() + timedelta(hours=2)

        # Schedule ride
        url = reverse("rides:schedule-list")
        response = self.client.post(url, {
            "quote_id": str(quote.id),
            "payment_method": "CASH",
            "scheduled_for": target_time.isoformat(),
        })
        self.assertEqual(response.status_code, 201)
        scheduled_id = response.data["id"]

        # Cancel scheduled ride
        cancel_url = reverse("rides:schedule-detail", kwargs={"pk": scheduled_id})
        del_resp = self.client.delete(cancel_url)
        self.assertEqual(del_resp.status_code, 200)
        self.assertIsNotNone(del_resp.data["cancelled_at"])

    def test_dispatch_scheduled_rides_command(self):
        quote = self.create_quote()
        past_due = timezone.now() - timedelta(minutes=1)
        sr = ScheduledRide.objects.create(
            rider=self.rider,
            quote=quote,
            payment_method="CASH",
            scheduled_for=past_due,
        )
        call_command("dispatch_scheduled_rides")
        sr.refresh_from_db()
        self.assertIsNotNone(sr.ride)
        self.assertEqual(sr.ride.status, Ride.Status.SEARCHING)

    # 2. Surge Pricing
    def test_surge_pricing_applied_to_quote(self):
        self.client.force_authenticate(self.rider)
        current_hour = timezone.now().hour
        SurgePricingRule.objects.create(
            name="Peak Hours",
            multiplier=Decimal("1.50"),
            hour_start=current_hour,
            hour_end=(current_hour + 1) % 24 or 24,
            is_active=True,
        )

        response = self.client.post(reverse("rides:quote"), {
            "pickup_label": "A",
            "destination_label": "B",
            "pickup_latitude": "23.793700",
            "pickup_longitude": "90.406600",
            "destination_latitude": "23.792500",
            "destination_longitude": "90.417800",
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(str(response.data["surge_multiplier"])), Decimal("1.50"))

    # 3. Rider Stats
    def test_rider_stats(self):
        self.client.force_authenticate(self.rider)
        quote = self.create_quote()
        ride = Ride.objects.create(
            rider=self.rider,
            driver=self.driver,
            quote=quote,
            request_id=uuid.uuid4(),
            status=Ride.Status.COMPLETED,
        )
        Payment.objects.create(
            ride=ride,
            amount=Decimal("150.00"),
            platform_fee=Decimal("22.50"),
            currency="BDT",
            method="CASH",
            status=Payment.Status.PAID,
        )

        response = self.client.get(reverse("rides:stats"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total_rides"], 1)
        self.assertEqual(response.data["total_spent"], "150.00")
        self.assertTrue(len(response.data["favourite_routes"]) > 0)

    # 4. Promo Codes
    def test_promo_validation_and_application(self):
        self.client.force_authenticate(self.rider)
        promo = PromoCode.objects.create(
            code="WELCOME20",
            discount_percent=Decimal("20.00"),
            min_fare=Decimal("100.00"),
            is_active=True,
        )

        # Validate endpoint
        val_resp = self.client.post(reverse("rides:promo-validate"), {
            "code": "WELCOME20",
            "fare": "150.00",
        })
        self.assertEqual(val_resp.status_code, 200)
        self.assertEqual(val_resp.data["discount_applied"], "30.00")
        self.assertEqual(val_resp.data["discounted_fare"], "120.00")

        # Request ride with promo
        quote = self.create_quote(fare=Decimal("150.00"))
        req_resp = self.client.post(reverse("rides:list"), {
            "quote_id": str(quote.id),
            "request_id": str(uuid.uuid4()),
            "payment_method": "CASH",
            "promo_code": "WELCOME20",
        })
        self.assertEqual(req_resp.status_code, 201)
        ride_id = req_resp.data["id"]
        self.assertTrue(PromoUsage.objects.filter(ride_id=ride_id, promo=promo).exists())

    # 5. SOS Alerts
    def test_sos_raise_and_resolve(self):
        quote = self.create_quote()
        ride = Ride.objects.create(
            rider=self.rider,
            driver=self.driver,
            quote=quote,
            request_id=uuid.uuid4(),
            status=Ride.Status.IN_PROGRESS,
        )

        # Rider raises SOS
        self.client.force_authenticate(self.rider)
        sos_url = reverse("rides:sos", kwargs={"pk": str(ride.id)})
        response = self.client.post(sos_url, {"message": "Need emergency assistance!"})
        self.assertEqual(response.status_code, 201)
        alert_id = response.data["id"]

        # Staff resolves SOS
        self.client.force_authenticate(self.staff_user)
        resolve_url = reverse("rides:sos-resolve", kwargs={"pk": alert_id})
        resolve_resp = self.client.post(resolve_url)
        self.assertEqual(resolve_resp.status_code, 200)
        self.assertIsNotNone(resolve_resp.data["resolved_at"])

    # 6. Carpooling / Ride Sharing
    def test_carpool_workflow(self):
        quote = self.create_quote(fare=Decimal("300.00"))
        ride = Ride.objects.create(
            rider=self.rider,
            driver=self.driver,
            quote=quote,
            request_id=uuid.uuid4(),
            status=Ride.Status.ACCEPTED,
        )

        # Enable carpool
        self.client.force_authenticate(self.rider)
        enable_url = reverse("rides:enable-carpool", kwargs={"pk": str(ride.id)})
        carpool_resp = self.client.post(enable_url, {"total_seats": 3, "fare_per_seat": "100.00"})
        self.assertEqual(carpool_resp.status_code, 201)
        carpool_id = carpool_resp.data["id"]
        self.assertEqual(carpool_resp.data["available_seats"], 3)

        # List carpools
        list_resp = self.client.get(reverse("rides:carpool-list"))
        self.assertEqual(list_resp.status_code, 200)

        # Another rider joins
        second_rider = User.objects.create_user(
            username="second_rider", email="second@example.com", role="RIDER"
        )
        self.client.force_authenticate(second_rider)
        join_url = reverse("rides:join-carpool", kwargs={"pk": carpool_id})
        join_resp = self.client.post(join_url, {
            "seats": 2,
            "pickup_label": "Mohakhali",
            "destination_label": "Gulshan",
        })
        self.assertEqual(join_resp.status_code, 201)
        self.assertEqual(join_resp.data["status"], "CONFIRMED")

        carpool = CarpoolRide.objects.get(pk=carpool_id)
        self.assertEqual(carpool.available_seats, 1)

        # Second rider leaves carpool
        leave_url = reverse("rides:leave-carpool", kwargs={"pk": carpool_id})
        leave_resp = self.client.post(leave_url)
        self.assertEqual(leave_resp.status_code, 200)
        self.assertEqual(leave_resp.data["status"], "CANCELLED")

        carpool.refresh_from_db()
        self.assertEqual(carpool.available_seats, 3)
