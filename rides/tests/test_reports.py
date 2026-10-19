import csv
import io
import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from payments.models import Payment
from rides.models import Ride, RideQuote


class TripReportTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.rider = users.create_user(username="report_rider", email="rider@example.com")
        cls.driver = users.create_user(username="report_driver", email="driver@example.com", role="DRIVER")
        cls.outsider = users.create_user(username="report_outsider", email="outsider@example.com", is_staff=True)

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(self.rider)

    def make_ride(self, *, rider=None, driver=None, currency="BDT", method="CASH", status="COMPLETED",
                  paid=False, at=None, pickup="Banani", destination="Gulshan"):
        rider = rider or self.rider
        quote = RideQuote.objects.create(
            rider=rider, pickup_label=pickup, destination_label=destination,
            pickup_latitude="23.81", pickup_longitude="90.41",
            destination_latitude="23.78", destination_longitude="90.42",
            distance_km=4, estimated_minutes=10, fare="200.00", platform_fee="30.00",
            currency=currency, expires_at=timezone.now() + timedelta(minutes=10),
        )
        ride = Ride.objects.create(
            rider=rider, driver=driver or self.driver, quote=quote, request_id=uuid.uuid4(),
            status=status, payment_method=method,
            completed_at=(at or timezone.now()) if status == "COMPLETED" else None,
        )
        if at:
            Ride.objects.filter(pk=ride.pk).update(created_at=at)
        if status == "COMPLETED":
            Payment.objects.create(
                ride=ride, amount="150.00", platform_fee="22.50", currency=currency,
                method=method, status="PAID" if paid else "PENDING",
                paid_at=timezone.now() if paid else None,
            )
        return ride

    def csv_rows(self, params=None):
        response = self.client.get("/api/rides/export/", params or {})
        self.assertEqual(response.status_code, 200)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertIn("attachment", response.headers["Content-Disposition"])
        return list(csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"))))

    def test_combined_filters_match_export_and_include_end_of_day(self):
        at = datetime(2026, 10, 18, 23, 59, 59, tzinfo=dt_timezone.utc)
        wanted = self.make_ride(at=at, method="CARD")
        self.make_ride(at=at, method="CASH")
        self.make_ride(at=at + timedelta(seconds=1), method="CARD")
        self.make_ride(at=at, method="CARD", rider=self.outsider)
        params = {"q": "gUlShAn", "date_from": "2026-10-18", "date_to": "2026-10-18",
                  "status": "COMPLETED", "payment_method": "CARD"}
        response = self.client.get("/api/rides/", params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual([row["id"] for row in response.data["results"]], [str(wanted.pk)])
        self.assertEqual([row["Trip ID"] for row in self.csv_rows(params)], [str(wanted.pk)])
        self.assertEqual(self.client.get("/api/rides/", {"q": "BANANI"}).data["count"], 3)

    def test_invalid_filters_return_400_for_history_and_export(self):
        for params in ({"date_from": "yesterday"}, {"date_to": "2026-02-30"},
                       {"date_from": "2026-10-18", "date_to": "2026-10-17"},
                       {"status": "INVALID"}, {"payment_method": "INVALID"}, {"q": "a" * 201}):
            for path in ("/api/rides/", "/api/rides/export/"):
                with self.subTest(path=path, params=params):
                    self.assertEqual(self.client.get(path, params).status_code, 400)

    def test_date_filters_respect_daylight_saving_and_half_open_boundaries(self):
        # New York's spring-forward day is 23 hours long.
        start = datetime(2026, 3, 8, 5, tzinfo=dt_timezone.utc)
        end = datetime(2026, 3, 9, 4, tzinfo=dt_timezone.utc)
        self.make_ride(at=start - timedelta(microseconds=1))
        first = self.make_ride(at=start)
        last = self.make_ride(at=end - timedelta(microseconds=1))
        self.make_ride(at=end)
        with timezone.override("America/New_York"):
            params = {"date_from": "2026-03-08", "date_to": "2026-03-08"}
            response = self.client.get("/api/rides/", params)
            self.assertEqual(response.status_code, 200)
            self.assertEqual({row["id"] for row in response.data["results"]}, {str(first.pk), str(last.pk)})
            self.assertEqual({row["Trip ID"] for row in self.csv_rows(params)}, {str(first.pk), str(last.pk)})

    def test_maximum_end_date_does_not_overflow(self):
        ride = self.make_ride()
        response = self.client.get("/api/rides/", {"date_to": "9999-12-31"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["results"][0]["id"], str(ride.pk))

    def test_export_ownership_applies_to_drivers_and_staff(self):
        own = self.make_ride()
        other_driver = get_user_model().objects.create_user(username="other_report_driver", email="other@example.com", role="DRIVER")
        self.make_ride(rider=self.outsider, driver=other_driver)
        for user in (self.rider, self.driver):
            self.client.force_authenticate(user)
            self.assertEqual([row["Trip ID"] for row in self.csv_rows()], [str(own.pk)])
        self.client.force_authenticate(self.outsider)
        self.assertNotIn(str(own.pk), [row["Trip ID"] for row in self.csv_rows()])
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/rides/export/").status_code, 401)

    def test_export_preserves_unicode_and_distinguishes_quotes_from_payments(self):
        self.make_ride(pickup='=HYPERLINK("https://example.com")', destination="ঢাকা, বাংলাদেশ")
        self.make_ride(status="CANCELLED", pickup="  +SUM(1,2)")
        rows = self.csv_rows()
        completed = next(row for row in rows if row["Status"] == "Completed")
        self.assertTrue(completed["Pickup"].startswith("'="))
        self.assertEqual(completed["Destination"], "ঢাকা, বাংলাদেশ")
        self.assertEqual(completed["Quoted fare"], "200.00")
        self.assertEqual(completed["Final fare"], "150.00")
        self.assertEqual(completed["Payment status"], "PENDING")
        cancelled = next(row for row in rows if row["Status"] == "Cancelled")
        self.assertTrue(cancelled["Pickup"].startswith("'  +"))
        self.assertEqual(cancelled["Final fare"], "")
        self.assertEqual(cancelled["Payment status"], "")

    def test_export_includes_more_than_one_page_and_empty_report_has_headers(self):
        for _ in range(21):
            self.make_ride()
        self.assertEqual(len(self.client.get("/api/rides/").data["results"]), 20)
        self.assertEqual(len(self.csv_rows()), 21)
        self.assertEqual(self.csv_rows({"q": "no matching route"}), [])
        self.assertEqual(self.client.post("/api/rides/export/", {}).status_code, 405)

    def test_earnings_group_currencies_and_separate_pending_fares(self):
        at = datetime(2026, 10, 18, 23, 59, 59, tzinfo=dt_timezone.utc)
        self.make_ride(at=at, paid=True)
        self.make_ride(at=at, method="CARD")
        self.make_ride(at=at, currency="USD", method="CARD", paid=True)
        self.make_ride(at=at - timedelta(days=1))
        self.make_ride(at=at, driver=self.outsider)
        self.make_ride(at=at, status="CANCELLED")
        self.client.force_authenticate(self.driver)
        response = self.client.get("/api/payments/earnings/", {"date_from": "2026-10-18", "date_to": "2026-10-18"})
        self.assertEqual(response.status_code, 200)
        totals = {row["currency"]: row for row in response.data["totals"]}
        self.assertEqual(set(totals), {"BDT", "USD"})
        self.assertEqual(totals["BDT"]["trips"], 2)
        self.assertEqual(totals["BDT"]["net"], Decimal("255.00"))
        self.assertEqual(totals["BDT"]["paid_net"], Decimal("127.50"))
        self.assertEqual(totals["BDT"]["pending_net"], Decimal("127.50"))
        self.assertEqual(totals["BDT"]["average_net"], "127.50")
        self.assertEqual(totals["USD"]["card_net"], Decimal("127.50"))
        self.assertEqual(len(response.data["daily"]), 2)

    def test_earnings_use_completion_date_and_bound_daily_breakdown(self):
        at = datetime(2026, 10, 18, tzinfo=dt_timezone.utc)
        ride = self.make_ride(at=at)
        Ride.objects.filter(pk=ride.pk).update(created_at=at - timedelta(days=2))
        self.make_ride(at=at - timedelta(days=31))
        self.client.force_authenticate(self.driver)
        data = self.client.get("/api/payments/earnings/", {"date_to": "2026-10-18"}).data
        self.assertEqual(data["totals"][0]["trips"], 2)
        self.assertEqual(len(data["daily"]), 1)
        self.assertEqual(data["daily"][0]["date"].isoformat(), "2026-10-18")
        data = self.client.get("/api/payments/earnings/", {"date_from": "2026-10-18", "date_to": "2026-10-18"}).data
        self.assertEqual(data["totals"][0]["trips"], 1)

    def test_earnings_validation_access_and_empty_period(self):
        self.assertEqual(self.client.get("/api/payments/earnings/").status_code, 403)
        self.client.force_authenticate(self.driver)
        for params in ({"date_from": "invalid"}, {"date_from": "2026-10-18", "date_to": "2026-10-01"}):
            self.assertEqual(self.client.get("/api/payments/earnings/", params).status_code, 400)
        data = self.client.get("/api/payments/earnings/").data
        self.assertEqual(data["totals"], [])
        self.assertEqual(data["daily"], [])
        response = self.client.get("/api/payments/earnings/", {"date_to": "0001-01-01"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["daily"], [])

    def test_large_export_requires_narrower_filters(self):
        quotes = RideQuote.objects.bulk_create([
            RideQuote(rider=self.rider, pickup_label="Banani", destination_label="Gulshan",
                      pickup_latitude=23, pickup_longitude=90, destination_latitude=24,
                      destination_longitude=91, distance_km=4, estimated_minutes=10,
                      fare=200, platform_fee=30, currency="BDT", expires_at=timezone.now())
            for _ in range(5001)
        ])
        Ride.objects.bulk_create([
            Ride(rider=self.rider, driver=self.driver, quote=quote, request_id=uuid.uuid4(), status="COMPLETED")
            for quote in quotes
        ])
        response = self.client.get("/api/rides/export/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("Narrow the date range", response.data["detail"])
        self.assertEqual(self.csv_rows({"status": "CANCELLED"}), [])
        Ride.objects.filter(quote=quotes[0]).update(status="CANCELLED")
        RideQuote.objects.update(pickup_label="ঢ" * 200, destination_label="ঢ" * 200)
        response = self.client.get("/api/rides/export/", {"status": "COMPLETED"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("too large", response.data["detail"])
