import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.db import connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from rides.models import Ride, RideOffer
from rides.services import Conflict, accept_offer, create_quote, request_ride
from rides.tests.test_workflow import make_driver


class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
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
