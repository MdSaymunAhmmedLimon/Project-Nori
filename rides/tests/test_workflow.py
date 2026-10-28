from rides.tests.routing_fixtures import mock_routing
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APITestCase

from drivers.models import DriverProfile, Vehicle
from notifications.models import Notification
from payments.models import Payment
from rides.models import Rating, Ride, RideOffer, RideQuote, TrackPoint

User = get_user_model()


def make_driver(username, **profile_changes):
    driver = User.objects.create_user(username=username, email=f"{username}@example.com", role="DRIVER")
    data = {
        "license_number": f"DL-{username}", "license_expiry_date": timezone.localdate() + timedelta(days=365),
        "verification_status": "APPROVED", "is_online": True, "latitude": "23.810300", "longitude": "90.412500",
        "location_updated_at": timezone.now(),
    }
    profile = DriverProfile.objects.create(user=driver, **(data | profile_changes))
    Vehicle.objects.create(driver=profile, license_plate=f"CAR-{username}", make="Toyota", model="Corolla", color="Blue", year=2024)
    return driver


class RideWorkflowTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rider = User.objects.create_user(username="rider", email="rider@example.com")
        cls.other_rider = User.objects.create_user(username="other_rider", email="other@example.com")
        cls.driver = make_driver("driver")
        cls.other_driver = make_driver("other_driver")

    def setUp(self):
        mock_routing(self)
        cache.clear()
        self.client.force_authenticate(self.rider)
        self.route = {"pickup_label": "Banani", "destination_label": "Gulshan",
                      "pickup_latitude": "23.810300", "pickup_longitude": "90.412500",
                      "destination_latitude": "23.780600", "destination_longitude": "90.419300"}

    def quote(self, **changes):
        response = self.client.post("/api/rides/quotes/", self.route | changes)
        self.assertEqual(response.status_code, 201, response.data)
        return response.data

    def request_ride(self, **changes):
        self.client.force_authenticate(self.rider)
        data = {"quote_id": self.quote()["id"], "request_id": str(uuid.uuid4()), "payment_method": "CASH"} | changes
        response = self.client.post("/api/rides/", data)
        self.assertEqual(response.status_code, 201, response.data)
        return Ride.objects.get(pk=response.data["id"]), data

    def accept(self, ride, driver=None):
        driver = driver or self.driver
        self.client.force_authenticate(driver)
        offer = RideOffer.objects.get(ride=ride, driver=driver)
        response = self.client.post(f"/api/rides/offers/{offer.pk}/accept/", {})
        self.assertEqual(response.status_code, 200, response.data)
        return response

    def action(self, ride, action, status=200, **data):
        response = self.client.post(f"/api/rides/{ride.pk}/actions/", {"action": action, **data})
        self.assertEqual(response.status_code, status, response.data)
        return response

    def complete(self, ride):
        self.accept(ride)
        for action in ("arriving", "arrived", "start", "complete"):
            self.action(ride, action)
        ride.refresh_from_db()

    def test_full_cash_trip_tracking_payment_ratings_and_earnings(self):
        ride, _ = self.request_ride()
        self.assertEqual(ride.status, "SEARCHING")
        self.assertEqual(ride.offers.count(), 2)
        self.assertTrue(Notification.objects.filter(user=self.rider).exists())
        self.accept(ride)
        response = self.client.post("/api/drivers/location/", {"latitude": "23.809000", "longitude": "90.411000"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(TrackPoint.objects.filter(ride=ride).count(), 1)
        self.client.force_authenticate(self.rider)
        detail = self.client.get(f"/api/rides/{ride.pk}/").data
        self.assertEqual(detail["driver"]["id"], self.driver.pk)
        self.assertIn("location", detail["driver"])
        self.assertEqual(len(self.client.get(f"/api/rides/{ride.pk}/tracking/").data["points"]), 1)
        self.client.force_authenticate(self.driver)
        for action in ("arriving", "arrived", "start", "complete"):
            self.action(ride, action)
        self.assertEqual(self.client.get("/api/rides/current/").data, None)
        response = self.client.post(f"/api/payments/{ride.pk}/cash/", {})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["status"], "PAID")
        self.client.post(f"/api/payments/{ride.pk}/cash/", {})
        self.assertEqual(Payment.objects.filter(ride=ride).count(), 1)
        for user in (self.driver, self.rider):
            self.client.force_authenticate(user)
            result = self.client.post(f"/api/rides/{ride.pk}/rating/", {"score": 5, "comment": "A smooth trip."})
            self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(Rating.objects.filter(ride=ride).count(), 2)
        self.assertNotIn("location", self.client.get(f"/api/rides/{ride.pk}/").data["driver"])
        self.client.force_authenticate(self.driver)
        totals = self.client.get("/api/payments/earnings/").data["totals"][0]
        self.assertEqual(totals["trips"], 1)
        self.assertEqual(totals["gross"], ride.quote.fare)
        self.assertEqual(totals["net"], ride.quote.fare - ride.quote.platform_fee)
        self.assertEqual(self.client.get("/api/rides/?status=COMPLETED").data["count"], 1)

    def test_quotes_validate_coordinates_distance_and_roles(self):
        for changes in ({"pickup_latitude": "91"}, {"destination_longitude": "181"},
                        {"pickup_latitude": "NaN"}, {"destination_latitude": "23.810300", "destination_longitude": "90.412500"},
                        {"destination_latitude": "0", "destination_longitude": "0"}, {"fare": "1"}):
            with self.subTest(changes=changes):
                self.assertEqual(self.client.post("/api/rides/quotes/", self.route | changes).status_code, 400)
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.post("/api/rides/quotes/", self.route).status_code, 403)

    def test_quote_expiry_ownership_and_fixed_price(self):
        quote = self.quote()
        self.client.force_authenticate(self.other_rider)
        data = {"quote_id": quote["id"], "request_id": str(uuid.uuid4())}
        self.assertEqual(self.client.post("/api/rides/", data).status_code, 404)
        self.client.force_authenticate(self.rider)
        with override_settings(FARE_BASE="900.00"):
            response = self.client.post("/api/rides/", data)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["quote"]["fare"], quote["fare"])
        self.action(Ride.objects.get(), "cancel")
        quote = self.quote()
        RideQuote.objects.filter(pk=quote["id"]).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.post("/api/rides/", {"quote_id": quote["id"], "request_id": str(uuid.uuid4())}).status_code, 400)

    def test_request_idempotency_and_active_ride_conflict(self):
        ride, data = self.request_ride()
        response = self.client.post("/api/rides/", data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(ride.pk), response.data["id"])
        self.assertEqual(self.client.post("/api/rides/", data | {"request_id": str(uuid.uuid4())}).status_code, 409)
        self.assertEqual(self.client.post("/api/rides/", data | {"payment_method": "CARD"}).status_code, 409)
        self.assertEqual(Ride.objects.count(), 1)

    def test_declined_offers_are_not_reoffered(self):
        ride, _ = self.request_ride()
        self.client.force_authenticate(self.driver)
        offer = RideOffer.objects.get(ride=ride, driver=self.driver)
        self.assertEqual(self.client.post(f"/api/rides/offers/{offer.pk}/reject/", {}).status_code, 200)
        self.assertEqual(self.client.get("/api/rides/offers/").data["count"], 0)
        self.assertEqual(self.client.post(f"/api/rides/offers/{offer.pk}/accept/", {}).status_code, 409)

    def test_dispatch_fills_rejected_slots_with_other_nearby_drivers(self):
        from rides.services import dispatch_ride
        for index in range(5):
            make_driver(f"extra_driver{index}")
        ride, _ = self.request_ride()
        self.assertEqual(ride.offers.count(), 5)
        ride.offers.update(rejected_at=timezone.now())
        dispatch_ride(ride.pk)
        self.assertEqual(ride.offers.count(), 7)

    def test_driver_without_profile_gets_not_found_for_trip_action(self):
        driver = User.objects.create_user(username="no_profile", email="noprofile@example.com", role="DRIVER")
        self.client.force_authenticate(driver)
        self.assertEqual(self.client.post(f"/api/rides/{uuid.uuid4()}/actions/", {"action":"complete"}).status_code, 404)

    def test_accept_checks_offer_expiry_and_owner(self):
        ride, _ = self.request_ride()
        offer = RideOffer.objects.get(ride=ride, driver=self.driver)
        self.client.force_authenticate(self.other_driver)
        self.assertEqual(self.client.post(f"/api/rides/offers/{offer.pk}/accept/", {}).status_code, 404)
        self.client.force_authenticate(self.driver)
        RideOffer.objects.filter(pk=offer.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.client.post(f"/api/rides/offers/{offer.pk}/accept/", {}).status_code, 409)

    def test_only_one_driver_can_accept(self):
        ride, _ = self.request_ride()
        self.accept(ride)
        self.accept(ride)
        self.client.force_authenticate(self.other_driver)
        offer = RideOffer.objects.get(ride=ride, driver=self.other_driver)
        self.assertEqual(self.client.post(f"/api/rides/offers/{offer.pk}/accept/", {}).status_code, 409)

    def test_rider_cannot_advance_trip_and_driver_cannot_skip_states(self):
        ride, _ = self.request_ride()
        self.accept(ride)
        self.action(ride, "complete", 409)
        self.action(ride, "start", 409)
        self.client.force_authenticate(self.rider)
        self.action(ride, "arriving", 403)
        self.client.force_authenticate(self.other_rider)
        self.action(ride, "cancel", 404)

    def test_cancellation_before_start_and_after_start_restriction(self):
        ride, _ = self.request_ride()
        self.accept(ride)
        self.client.force_authenticate(self.rider)
        self.action(ride, "cancel", reason="Plans changed")
        self.action(ride, "cancel", reason="Plans changed")
        self.assertFalse(Payment.objects.filter(ride=ride).exists())
        another, _ = self.request_ride()
        self.accept(another)
        for action in ("arriving", "arrived", "start"):
            self.action(another, action)
        self.action(another, "cancel", 409)
        self.client.force_authenticate(self.rider)
        self.action(another, "cancel", 409)

    def test_matching_excludes_unapproved_stale_offline_and_far_drivers(self):
        cases = ({"verification_status": "PENDING"}, {"is_online": False}, {"latitude": "0", "longitude": "0"},
                 {"location_updated_at": timezone.now() - timedelta(minutes=5)})
        for index, changes in enumerate(cases):
            make_driver(f"unavailable{index}", **changes)
        ride, _ = self.request_ride()
        self.assertEqual(set(ride.offers.values_list("driver_id", flat=True)), {self.driver.pk, self.other_driver.pk})

    def test_driver_online_requires_approval_vehicle_and_fresh_location(self):
        self.client.force_authenticate(self.driver)
        DriverProfile.objects.filter(user=self.driver).update(verification_status="PENDING", is_online=False)
        self.assertEqual(self.client.post("/api/drivers/availability/", {"is_online": True}).status_code, 409)
        DriverProfile.objects.filter(user=self.driver).update(verification_status="APPROVED", location_updated_at=timezone.now()-timedelta(minutes=5))
        self.assertEqual(self.client.post("/api/drivers/availability/", {"is_online": True}).status_code, 409)
        self.client.post("/api/drivers/location/", {"latitude": "23.810300", "longitude": "90.412500"})
        self.assertEqual(self.client.post("/api/drivers/availability/", {"is_online": True}).status_code, 200)

    def test_driver_cannot_self_approve(self):
        self.client.force_authenticate(self.driver)
        response = self.client.patch("/api/drivers/profile/", {"verification_status": "APPROVED", "is_online": True})
        self.assertEqual(response.status_code, 400)

    def test_profile_and_vehicle_changes_require_new_review(self):
        self.client.force_authenticate(self.driver)
        response = self.client.patch("/api/drivers/vehicle/", {"color": "Red"})
        self.assertEqual(response.status_code, 200, response.data)
        profile = DriverProfile.objects.get(user=self.driver)
        self.assertEqual(profile.verification_status, "PENDING")
        self.assertFalse(profile.is_online)

    def test_cannot_edit_driver_details_or_go_offline_mid_trip(self):
        ride, _ = self.request_ride()
        self.accept(ride)
        self.assertEqual(self.client.patch("/api/drivers/vehicle/", {"color": "Red"}).status_code, 409)
        self.assertEqual(self.client.patch("/api/drivers/profile/", {"license_number": "DIFFERENT"}).status_code, 409)
        self.assertEqual(self.client.post("/api/drivers/availability/", {"is_online": False}).status_code, 409)

    def test_trip_history_and_tracking_are_private(self):
        ride, _ = self.request_ride()
        self.accept(ride)
        for user in (self.other_rider, self.other_driver):
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get(f"/api/rides/{ride.pk}/").status_code, 404)
            self.assertEqual(self.client.get(f"/api/rides/{ride.pk}/tracking/").status_code, 404)
            self.assertEqual(self.client.get("/api/rides/").data["count"], 0)

    def test_ratings_require_completion_and_correct_participant(self):
        ride, _ = self.request_ride()
        self.assertEqual(self.client.post(f"/api/rides/{ride.pk}/rating/", {"score": 5}).status_code, 409)
        self.complete(ride)
        self.client.force_authenticate(self.other_rider)
        self.assertEqual(self.client.post(f"/api/rides/{ride.pk}/rating/", {"score": 5}).status_code, 404)
        self.client.force_authenticate(self.rider)
        for score in (0, 6):
            self.assertEqual(self.client.post(f"/api/rides/{ride.pk}/rating/", {"score": score}).status_code, 400)
        self.assertEqual(self.client.post(f"/api/rides/{ride.pk}/rating/", {"score": 5}).status_code, 201)
        self.assertEqual(self.client.post(f"/api/rides/{ride.pk}/rating/", {"score": 5}).status_code, 409)

    def test_search_timeout_frees_rider_for_a_new_trip(self):
        ride, _ = self.request_ride()
        Ride.objects.filter(pk=ride.pk).update(created_at=timezone.now()-timedelta(minutes=16))
        self.assertIsNone(self.client.get("/api/rides/current/").data)
        ride.refresh_from_db()
        self.assertEqual(ride.status, "CANCELLED")
        self.request_ride()

    def test_location_validation_and_tracking_sample_limit(self):
        ride, _ = self.request_ride()
        self.accept(ride)
        for latitude in ("91", "NaN", "23.1234567"):
            self.assertEqual(self.client.post("/api/drivers/location/", {"latitude": latitude, "longitude": "90"}).status_code, 400)
        for _ in range(3):
            self.client.post("/api/drivers/location/", {"latitude": "23.8", "longitude": "90.4"})
        self.assertEqual(TrackPoint.objects.filter(ride=ride).count(), 1)

    def test_notifications_are_private_and_mark_read_only_for_owner(self):
        Notification.objects.create(user=self.rider, message="Rider update")
        Notification.objects.create(user=self.other_rider, message="Private update")
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.data["count"], 1)
        self.client.post("/api/notifications/read/", {})
        self.assertIsNotNone(Notification.objects.get(user=self.rider).read_at)
        self.assertIsNone(Notification.objects.get(user=self.other_rider).read_at)

    def test_rider_cannot_confirm_cash_or_view_driver_earnings(self):
        ride, _ = self.request_ride()
        self.complete(ride)
        self.client.force_authenticate(self.rider)
        self.assertEqual(self.client.post(f"/api/payments/{ride.pk}/cash/", {}).status_code, 403)
        self.assertEqual(self.client.get("/api/payments/earnings/").status_code, 403)
        self.client.force_authenticate(self.other_driver)
        self.assertEqual(self.client.post(f"/api/payments/{ride.pk}/cash/", {}).status_code, 404)

    @override_settings(CARD_PAYMENTS_ENABLED=False)
    def test_card_payment_cannot_be_selected_without_configuration(self):
        quote = self.quote()
        response = self.client.post("/api/rides/", {"quote_id": quote["id"], "request_id": str(uuid.uuid4()), "payment_method": "CARD"})
        self.assertEqual(response.status_code, 400)

    def test_active_ride_database_constraint(self):
        ride, _ = self.request_ride()
        quote = RideQuote.objects.get(pk=self.quote()["id"])
        with self.assertRaises(IntegrityError), transaction.atomic():
            Ride.objects.create(rider=self.rider, quote=quote, request_id=uuid.uuid4(), status="SEARCHING")
