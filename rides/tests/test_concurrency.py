from rides.tests.routing_fixtures import mock_routing
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature
from django.utils import timezone
from rest_framework.exceptions import NotFound

from rides.models import Ride, RideOffer, ScheduledRide
from rides.services import Conflict, accept_offer, cancel_scheduled_ride, create_quote, dispatch_scheduled_ride, request_ride
from rides.tests.test_workflow import make_driver


class PostgreSQLConcurrencyTests(TransactionTestCase):
    def due_reservation(self):
        quote = create_quote(self.rider, self.route)
        return ScheduledRide.objects.create(
            rider=self.rider, quote=quote, scheduled_for=timezone.now() - timedelta(minutes=1),
        )

    @skipUnlessDBFeature("has_select_for_update")
    def test_simultaneous_scheduled_dispatch_creates_one_ride(self):
        reservation = self.due_reservation()
        self.run_race([lambda: dispatch_scheduled_ride(reservation.pk)] * 2)
        reservation.refresh_from_db()
        self.assertIsNotNone(reservation.ride_id)
        self.assertEqual(Ride.objects.count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_cancel_cannot_race_with_scheduled_dispatch(self):
        reservation = self.due_reservation()

        def cancel():
            try:
                cancel_scheduled_ride(self.rider, reservation.pk)
            except NotFound:
                pass  # Dispatch won; the active ride must be cancelled separately.

        self.run_race([cancel, lambda: dispatch_scheduled_ride(reservation.pk)])
        reservation.refresh_from_db()
        self.assertNotEqual(bool(reservation.cancelled_at), bool(reservation.ride_id))
        self.assertEqual(Ride.objects.count(), int(bool(reservation.ride_id)))

    def setUp(self):
        mock_routing(self)
        self.rider = get_user_model().objects.create_user(username="rider", email="rider@example.com")
        self.driver = make_driver("first_driver")
        self.other_driver = make_driver("second_driver")
        self.route = {"pickup_label":"A", "destination_label":"B", "pickup_latitude":"23.810300", "pickup_longitude":"90.412500", "destination_latitude":"23.780600", "destination_longitude":"90.419300"}

    def run_race(self, tasks):
        barrier = Barrier(len(tasks))
        def run(task):
            try:
                barrier.wait(timeout=10)
                task()
                return "accepted"
            except Conflict:
                return "conflict"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
            return list(pool.map(run, tasks))

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_drivers_accept_same_ride(self):
        quote = create_quote(self.rider, self.route)
        ride, _ = request_ride(self.rider, quote_id=quote.pk, request_id=uuid.uuid4(), payment_method="CASH")
        first_offer = RideOffer.objects.get(ride=ride, driver=self.driver)
        second_offer = RideOffer.objects.get(ride=ride, driver=self.other_driver)
        results = self.run_race([
            lambda: accept_offer(self.driver, first_offer.pk),
            lambda: accept_offer(self.other_driver, second_offer.pk),
        ])
        self.assertCountEqual(results, ["accepted", "conflict"])
        self.assertEqual(Ride.objects.filter(status="ACCEPTED").count(), 1)

    @skipUnlessDBFeature("has_select_for_update")
    def test_same_driver_accepts_two_rides(self):
        second_rider = get_user_model().objects.create_user(username="second_rider", email="second_rider@example.com")
        offers = []
        for rider in (self.rider, second_rider):
            quote = create_quote(rider, self.route)
            ride, _ = request_ride(rider, quote_id=quote.pk, request_id=uuid.uuid4(), payment_method="CASH")
            offers.append(RideOffer.objects.get(ride=ride, driver=self.driver))
        results = self.run_race([
            lambda: accept_offer(self.driver, offers[0].pk),
            lambda: accept_offer(self.driver, offers[1].pk),
        ])
        self.assertCountEqual(results, ["accepted", "conflict"])
        self.assertEqual(Ride.objects.filter(driver=self.driver, status="ACCEPTED").count(), 1)
