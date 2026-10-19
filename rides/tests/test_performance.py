"""Query budgets protect hot paths from per-row database round trips."""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APITestCase

from payments.models import Payment
from rides.models import Rating, Ride, RideEvent, RideMessage, RideQuote, RideStop, RideTip, ScheduledRide, TripShareToken
from rides.tests.test_workflow import make_driver


class ReadPerformanceTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.rider = get_user_model().objects.create_user(username="perf_rider", email="perf@example.com")
        cls.driver = make_driver("perf_driver")
        cls.other = get_user_model().objects.create_user(username="perf_other", email="otherperf@example.com")
        cls.rides = []
        for number in range(20):
            quote = RideQuote.objects.create(
                rider=cls.rider, pickup_label="Origin", destination_label=f"Destination {number}",
                pickup_latitude="23.810300", pickup_longitude="90.412500",
                destination_latitude="23.780600", destination_longitude="90.419300",
                distance_km="4.00", estimated_minutes=12, fare="150.00",
                platform_fee="22.50", currency="BDT", expires_at=timezone.now() + timedelta(minutes=10),
            )
            RideStop.objects.create(quote=quote, label="Stop", latitude="23.800000", longitude="90.410000")
            ride = Ride.objects.create(rider=cls.rider, driver=cls.driver, quote=quote, request_id=uuid.uuid4(), status="COMPLETED")
            RideEvent.objects.create(ride=ride, status="COMPLETED")
            Rating.objects.create(ride=ride, author=cls.rider, recipient=cls.driver, score=3 if number % 2 else 5)
            Rating.objects.create(ride=ride, author=cls.driver, recipient=cls.rider, score=5)
            Payment.objects.create(ride=ride, amount="150.00", platform_fee="22.50", currency="BDT", method="CASH")
            RideTip.objects.create(ride=ride, amount="10.00")
            ScheduledRide.objects.create(rider=cls.rider, quote=quote, scheduled_for=timezone.now() + timedelta(hours=1))
            cls.rides.append(ride)
        RideMessage.objects.bulk_create([
            RideMessage(ride=cls.rides[0], sender=cls.rider if n % 2 else cls.driver, content=f"Message {n}")
            for n in range(20)
        ])

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(self.rider)

    def test_twenty_trip_history_query_budget_and_contents(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/rides/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertLessEqual(len(queries), 5, f"History database queries: {len(queries)}")
        for ride in response.data["results"]:
            self.assertEqual(ride["driver"]["rating"], 4)
            self.assertEqual(ride["rider"]["rating"], 5)
            self.assertEqual(ride["driver"]["vehicle"]["make"], "Toyota")
            self.assertNotIn("phone_number", ride["driver"])
            self.assertNotIn("location", ride["driver"])
            self.assertEqual(ride["tip"], "10.00")
            self.assertEqual(len(ride["quote"]["stops"]), 1)
            self.assertEqual(len(ride["ratings"]), 2)
            self.assertEqual(len(ride["events"]), 1)

    def test_twenty_reservations_query_budget(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get("/api/rides/schedule/?pending=1")
        self.assertEqual(len(response.data["results"]), 20)
        self.assertEqual(len(response.data["results"][0]["quote"]["stops"]), 1)
        self.assertLessEqual(len(queries), 3, f"Reservation database queries: {len(queries)}")

    def test_chat_does_not_query_each_sender(self):
        with CaptureQueriesContext(connection) as queries:
            response = self.client.get(f"/api/rides/{self.rides[0].pk}/messages/")
        self.assertEqual(len(response.data), 20)
        self.assertEqual({item["sender_name"] for item in response.data}, {"perf_driver", "perf_rider"})
        self.assertLessEqual(len(queries), 2, f"Chat database queries: {len(queries)}")

    def test_chat_and_tracking_remain_private(self):
        self.client.force_authenticate(self.other)
        for endpoint in ("messages", "tracking"):
            self.assertEqual(self.client.get(f"/api/rides/{self.rides[0].pk}/{endpoint}/").status_code, 404)

    def test_missing_driver_relations_and_rating_are_supported(self):
        ride = self.rides[0]
        Ride.objects.filter(pk=ride.pk).update(driver=None)
        Rating.objects.filter(recipient=self.rider).delete()
        response = self.client.get(f"/api/rides/{ride.pk}/")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data["driver"])
        self.assertIsNone(response.data["rider"]["rating"])

    def test_shared_trip_uses_one_query_without_exposing_private_fields(self):
        share = TripShareToken.objects.create(
            ride=self.rides[0], token="performance-share", created_by=self.rider,
            expires_at=timezone.now() + timedelta(hours=1),
        )
        with self.assertNumQueries(1):
            response = self.client.get(f"/api/rides/shared/{share.token}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["driver"]["vehicle"]["make"], "Toyota")
        self.assertNotIn("rider", response.data)
        self.assertNotIn("phone_number", response.data["driver"])
