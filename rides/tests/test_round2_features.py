from datetime import timedelta
from decimal import Decimal
import uuid

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Referral, ReferralCode
from drivers.models import DriverProfile, PayoutRequest, Vehicle
from payments.models import Payment
from rides.models import (
    Ride, RideMessage, RideQuote, RideStop, RideTip, SavedPlace,
)

User = get_user_model()


class Round2FeaturesTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rider = User.objects.create_user(
            username="round2_rider", email="r2_rider@example.com", password="password123", role="RIDER"
        )
        cls.driver = User.objects.create_user(
            username="round2_driver", email="r2_driver@example.com", password="password123", role="DRIVER"
        )
        cls.staff = User.objects.create_user(
            username="round2_staff", email="r2_staff@example.com", password="password123", is_staff=True
        )
        cls.profile = DriverProfile.objects.create(
            user=cls.driver,
            license_number="DL-R2-TEST",
            license_expiry_date=timezone.localdate() + timedelta(days=365),
            verification_status=DriverProfile.Verification.APPROVED,
            is_online=True,
            latitude=Decimal("23.8103"),
            longitude=Decimal("90.4125"),
            location_updated_at=timezone.now(),
        )
        cls.vehicle = Vehicle.objects.create(
            driver=cls.profile,
            license_plate="PLATE-R2-01",
            make="Toyota",
            model="Noah",
            color="Black",
            year=2022,
            seats=6,
            vehicle_class=Vehicle.VehicleClass.XL,
        )

    def setUp(self):
        cache.clear()

    def create_ride(self, status=Ride.Status.COMPLETED):
        quote = RideQuote.objects.create(
            rider=self.rider,
            pickup_label="Banani",
            destination_label="Gulshan",
            pickup_latitude=Decimal("23.7937"),
            pickup_longitude=Decimal("90.4066"),
            destination_latitude=Decimal("23.7925"),
            destination_longitude=Decimal("90.4178"),
            distance_km=Decimal("3.00"),
            estimated_minutes=10,
            fare=Decimal("200.00"),
            platform_fee=Decimal("30.00"),
            currency="BDT",
            expires_at=timezone.now() + timedelta(minutes=15),
            vehicle_class="XL",
        )
        ride = Ride.objects.create(
            rider=self.rider,
            driver=self.driver,
            quote=quote,
            request_id=uuid.uuid4(),
            status=status,
            accepted_at=timezone.now() - timedelta(minutes=5),
        )
        Payment.objects.create(
            ride=ride,
            amount=Decimal("200.00"),
            platform_fee=Decimal("30.00"),
            currency="BDT",
            method="CASH",
            status=Payment.Status.PAID,
        )
        return ride

    # 1. Driver Tipping
    def test_rider_tips_driver(self):
        ride = self.create_ride(status=Ride.Status.COMPLETED)
        self.client.force_authenticate(self.rider)

        tip_url = reverse("rides:tip", kwargs={"pk": str(ride.id)})
        response = self.client.post(tip_url, {"amount": "50.00"})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["amount"], "50.00")
        self.assertTrue(RideTip.objects.filter(ride=ride, amount=Decimal("50.00")).exists())

        # Duplicate tip prevented
        dup_resp = self.client.post(tip_url, {"amount": "20.00"})
        self.assertEqual(dup_resp.status_code, 409)

    # 2. In-App Chat / Messaging
    def test_ride_chat_messaging(self):
        ride = self.create_ride(status=Ride.Status.IN_PROGRESS)
        self.client.force_authenticate(self.rider)

        msg_url = reverse("rides:messages", kwargs={"pk": str(ride.id)})
        post_resp = self.client.post(msg_url, {"content": "I am standing near the entrance."})
        self.assertEqual(post_resp.status_code, 201)
        self.assertEqual(post_resp.data["content"], "I am standing near the entrance.")

        # Driver views messages
        self.client.force_authenticate(self.driver)
        get_resp = self.client.get(msg_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(len(get_resp.data), 1)

    # 3. Saved Places / Favourites
    def test_saved_places_crud(self):
        self.client.force_authenticate(self.rider)
        url = reverse("rides:saved-places")

        # Create
        post_resp = self.client.post(url, {
            "label": "Office",
            "address": "123 Tech Park, Dhaka",
            "latitude": "23.810300",
            "longitude": "90.412500",
        })
        self.assertEqual(post_resp.status_code, 201)
        place_id = post_resp.data["id"]

        # List
        get_resp = self.client.get(url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(len(get_resp.data["results"] if "results" in get_resp.data else get_resp.data), 1)

        # Delete
        del_url = reverse("rides:saved-place-detail", kwargs={"pk": place_id})
        del_resp = self.client.delete(del_url)
        self.assertEqual(del_resp.status_code, 200)
        self.assertFalse(SavedPlace.objects.filter(pk=place_id).exists())

    # 4. Ride Cancellation Fees
    def test_cancellation_fee_applied(self):
        ride = self.create_ride(status=Ride.Status.DRIVER_ARRIVED)
        self.client.force_authenticate(self.rider)

        action_url = reverse("rides:action", kwargs={"pk": str(ride.id)})
        resp = self.client.post(action_url, {"action": "cancel", "reason": "Changed my mind"})
        self.assertEqual(resp.status_code, 200)
        ride.refresh_from_db()
        self.assertEqual(ride.status, Ride.Status.CANCELLED)
        self.assertGreater(ride.cancellation_fee, Decimal("0.00"))

    # 5. Driver Payout Requests
    def test_driver_payout_lifecycle(self):
        # Driver earned 200 - 30 = 170 from create_ride()
        self.create_ride(status=Ride.Status.COMPLETED)
        self.client.force_authenticate(self.driver)

        payout_url = reverse("drivers:payouts")
        get_resp = self.client.get(payout_url)
        self.assertEqual(get_resp.status_code, 200)
        self.assertGreater(Decimal(get_resp.data["available_balance"]), Decimal("100.00"))

        # Request payout
        req_resp = self.client.post(payout_url, {
            "amount": "100.00",
            "payout_method": "BKASH",
            "account_details": "+8801700000000",
        })
        self.assertEqual(req_resp.status_code, 201)
        payout_id = req_resp.data["id"]

        # Staff approves payout
        self.client.force_authenticate(self.staff)
        proc_url = reverse("drivers:payout-process", kwargs={"pk": payout_id})
        proc_resp = self.client.post(proc_url, {"action": "pay", "admin_note": "TrxID: 9X8Y7Z"})
        self.assertEqual(proc_resp.status_code, 200)
        self.assertEqual(proc_resp.data["status"], "PAID")

    # 6. Multiple Vehicle Classes
    def test_vehicle_class_quote_and_dispatch(self):
        self.client.force_authenticate(self.rider)

        # Quote with XL class
        quote_resp = self.client.post(reverse("rides:quote"), {
            "pickup_label": "Airport",
            "destination_label": "Banani",
            "pickup_latitude": "23.843300",
            "pickup_longitude": "90.404800",
            "destination_latitude": "23.793700",
            "destination_longitude": "90.406600",
            "vehicle_class": "XL",
        })
        self.assertEqual(quote_resp.status_code, 201)
        self.assertEqual(quote_resp.data["vehicle_class"], "XL")

    # 7. Live ETA & Multi-stop Waypoints
    def test_multistop_quote_and_live_eta(self):
        self.client.force_authenticate(self.rider)

        # Multi-stop quote
        quote_resp = self.client.post(reverse("rides:quote"), {
            "pickup_label": "Stop A",
            "destination_label": "Stop C",
            "pickup_latitude": "23.793700",
            "pickup_longitude": "90.406600",
            "destination_latitude": "23.820000",
            "destination_longitude": "90.420000",
            "stops": [
                {"label": "Stop B (Midpoint)", "latitude": "23.805000", "longitude": "90.410000"}
            ],
        })
        self.assertEqual(quote_resp.status_code, 201)
        self.assertEqual(len(quote_resp.data["stops"]), 1)

        # Live ETA
        ride = self.create_ride(status=Ride.Status.IN_PROGRESS)
        eta_url = reverse("rides:eta", kwargs={"pk": str(ride.id)})
        eta_resp = self.client.get(eta_url)
        self.assertEqual(eta_resp.status_code, 200)
        self.assertIn("estimated_minutes", eta_resp.data)
        self.assertIn("remaining_km", eta_resp.data)

    # 8. Referral System
    def test_referral_system_workflow(self):
        referrer_code_obj, _ = ReferralCode.objects.get_or_create(
            user=self.rider, defaults={"code": "NORI1234"}
        )

        # Register referee with referral code
        reg_resp = self.client.post(reverse("accounts:register"), {
            "username": "new_referee_user",
            "email": "referee@example.com",
            "password": "Password123!",
            "referral_code": referrer_code_obj.code,
        })
        self.assertEqual(reg_resp.status_code, 201)
        referee = User.objects.get(username="new_referee_user")

        # Referral record exists in PENDING state
        referral = Referral.objects.get(referee=referee)
        self.assertEqual(referral.status, Referral.Status.PENDING)
        self.assertEqual(referral.referrer, self.rider)

        # Check referral overview endpoint
        self.client.force_authenticate(self.rider)
        overview_resp = self.client.get(reverse("accounts:referral"))
        self.assertEqual(overview_resp.status_code, 200)
        self.assertEqual(overview_resp.data["referral_code"], referrer_code_obj.code)
        self.assertEqual(overview_resp.data["total_referrals"], 1)
