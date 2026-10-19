import uuid
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from notifications.models import Notification
from rides.models import Ride, RideQuote, ScheduledRide
from rides.services import dispatch_due_scheduled_rides, dispatch_scheduled_ride, schedule_ride


class SchedulingTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.rider = get_user_model().objects.create_user(username="planner", email="planner@example.com", role="RIDER")
        self.other = get_user_model().objects.create_user(username="other", email="other@example.com", role="RIDER")
        self.client.force_authenticate(self.rider)
        self.quote = RideQuote.objects.create(
            rider=self.rider, pickup_label="Banani", destination_label="Airport",
            pickup_latitude="23.793700", pickup_longitude="90.406600",
            destination_latitude="23.843300", destination_longitude="90.397800",
            distance_km="8.00", estimated_minutes=25, fare="250.00",
            platform_fee="37.50", currency="BDT", expires_at=timezone.now() + timedelta(minutes=10),
        )
        self.pickup = timezone.now() + timedelta(hours=2)
        self.reservation = schedule_ride(
            self.rider, quote_id=self.quote.pk, payment_method="CASH", scheduled_for=self.pickup,
        )
        self.detail_url = reverse("rides:schedule-detail", args=[self.reservation.pk])
        self.calendar_url = reverse("rides:schedule-calendar", args=[self.reservation.pk])

    def make_due(self, minutes=1):
        ScheduledRide.objects.filter(pk=self.reservation.pk).update(scheduled_for=timezone.now() - timedelta(minutes=minutes))
        RideQuote.objects.filter(pk=self.quote.pk).update(expires_at=timezone.now() - timedelta(hours=1))

    def test_change_pickup_preserves_quote_and_payment(self):
        target = timezone.now() + timedelta(days=2)
        response = self.client.patch(self.detail_url, {"scheduled_for": target.isoformat()}, format="json")
        self.assertEqual(response.status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.scheduled_for, target)
        self.assertEqual(self.reservation.quote_id, self.quote.pk)
        self.assertEqual(response.data["quote"]["fare"], "250.00")
        self.assertEqual(response.data["payment_method"], "CASH")

    def test_timezone_offset_is_preserved_as_an_instant(self):
        target = (timezone.now() + timedelta(days=1)).astimezone(dt_timezone(timedelta(hours=6)))
        response = self.client.patch(self.detail_url, {"scheduled_for": target.isoformat()}, format="json")
        self.assertEqual(response.status_code, 200)
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.scheduled_for, target)

    def test_invalid_time_limits_and_fields(self):
        now = timezone.now()
        with patch("rides.services.timezone.now", return_value=now):
            for value in (now - timedelta(hours=1), now + timedelta(minutes=15), now + timedelta(days=7, seconds=1)):
                with self.subTest(value=value):
                    response = self.client.patch(self.detail_url, {"scheduled_for": value.isoformat()}, format="json")
                    self.assertEqual(response.status_code, 400)
            valid = (now + timedelta(days=7)).isoformat()
            self.assertEqual(self.client.patch(self.detail_url, {"scheduled_for": valid}, format="json").status_code, 200)
        for data in ({}, {"scheduled_for": "invalid"}, {"scheduled_for": None}, {"payment_method": "CARD"}, {"rider": self.other.pk}):
            with self.subTest(data=data):
                self.assertEqual(self.client.patch(self.detail_url, data, format="json").status_code, 400)

    def test_other_user_cannot_change_cancel_or_download(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(self.detail_url, {"scheduled_for": self.pickup.isoformat()}).status_code, 404)
        self.assertEqual(self.client.delete(self.detail_url).status_code, 404)
        self.assertEqual(self.client.get(self.calendar_url).status_code, 404)

    def test_authentication_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get(self.calendar_url).status_code, 401)
        self.assertEqual(self.client.patch(self.detail_url, {"scheduled_for": self.pickup.isoformat()}).status_code, 401)

    def test_cancelled_reservations_cannot_be_edited_exported_or_dispatched(self):
        self.assertEqual(self.client.delete(self.detail_url).status_code, 200)
        self.assertEqual(self.client.patch(self.detail_url, {"scheduled_for": self.pickup.isoformat()}).status_code, 409)
        self.assertEqual(self.client.get(self.calendar_url).status_code, 409)
        self.make_due()
        self.assertIsNone(dispatch_scheduled_ride(self.reservation.pk))
        self.assertEqual(Ride.objects.count(), 0)

    def test_due_and_dispatched_reservations_cannot_be_edited_or_exported(self):
        self.make_due()
        for dispatched in (False, True):
            if dispatched:
                dispatch_scheduled_ride(self.reservation.pk)
            self.assertEqual(self.client.patch(self.detail_url, {"scheduled_for": self.pickup.isoformat()}).status_code, 409)
            self.assertEqual(self.client.get(self.calendar_url).status_code, 409)

    def test_calendar_utc_alarm_private_headers_and_stable_uid(self):
        pickup = datetime(2027, 1, 1, 14, 30, tzinfo=dt_timezone(timedelta(hours=6)))
        ScheduledRide.objects.filter(pk=self.reservation.pk).update(scheduled_for=pickup)
        with patch("rides.views.timezone.now", return_value=pickup - timedelta(days=1)):
            response = self.client.get(self.calendar_url)
            again = self.client.get(self.calendar_url)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("text/calendar", response["Content-Type"])
        self.assertIn("attachment;", response["Content-Disposition"])
        self.assertIn("private", response["Cache-Control"])
        self.assertIn("no-store", response["Cache-Control"])
        self.assertIn("DTSTART:20270101T083000Z\r\n", content)
        self.assertIn("DTEND:20270101T085500Z\r\n", content)
        self.assertIn("TRIGGER:-PT15M\r\n", content)
        self.assertIn("CLASS:PRIVATE\r\n", content)
        self.assertIn(f"UID:{self.reservation.pk}@nori-reservations\r\n", again.content.decode())
        self.assertNotIn(self.rider.email, content)
        self.assertTrue(content.endswith("END:VCALENDAR\r\n"))

    def test_calendar_escapes_injection_and_folds_unicode_by_bytes(self):
        self.quote.pickup_label = "Gate, 2; left\\right\r\nBEGIN:VEVENT"
        self.quote.destination_label = "\u09a2\u09be\u0995\u09be" * 40
        self.quote.save()
        response = self.client.get(self.calendar_url)
        content = response.content.decode()
        self.assertEqual(content.count("\r\nBEGIN:VEVENT\r\n"), 1)
        for line in response.content.split(b"\r\n"):
            self.assertLessEqual(len(line), 75)
            line.decode("utf-8")
        unfolded = content.replace("\r\n ", "")
        self.assertIn("LOCATION:Gate\\, 2\\; left\\\\right\\nBEGIN:VEVENT\r\n", unfolded)
        self.assertIn(self.quote.destination_label, unfolded)

    def test_duplicate_booking_is_idempotent_and_different_time_conflicts(self):
        data = {"quote_id": str(self.quote.pk), "scheduled_for": self.pickup.isoformat(), "payment_method": "CASH"}
        self.assertEqual(self.client.post(reverse("rides:schedule-list"), data).data["id"], str(self.reservation.pk))
        data["scheduled_for"] = (self.pickup + timedelta(hours=1)).isoformat()
        self.assertEqual(self.client.post(reverse("rides:schedule-list"), data).status_code, 409)
        self.assertEqual(ScheduledRide.objects.count(), 1)

    def test_expired_quote_cannot_create_a_reservation(self):
        self.client.delete(self.detail_url)
        self.make_due()
        response = self.client.post(reverse("rides:schedule-list"), {
            "quote_id": str(self.quote.pk), "scheduled_for": self.pickup.isoformat(),
        })
        self.assertEqual(response.status_code, 400)

    def test_reserved_quote_cannot_be_used_for_an_immediate_ride(self):
        response = self.client.post(reverse("rides:list"), {
            "quote_id": str(self.quote.pk), "request_id": str(uuid.uuid4()),
        })
        self.assertEqual(response.status_code, 409)

    def test_dispatch_honours_reserved_fare_after_quote_expiry_exactly_once(self):
        self.make_due()
        ride = dispatch_scheduled_ride(self.reservation.pk)
        self.assertIsNone(dispatch_scheduled_ride(self.reservation.pk))
        self.reservation.refresh_from_db()
        self.assertEqual(self.reservation.ride_id, ride.pk)
        self.assertEqual(Ride.objects.count(), 1)
        self.quote.refresh_from_db()
        self.assertEqual(ride.quote.fare, self.quote.fare)
        self.assertLess(self.quote.expires_at, timezone.now())

    def test_polling_dispatches_own_due_reservation(self):
        self.make_due()
        response = self.client.get(reverse("rides:current"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["status"], "SEARCHING")
        self.reservation.refresh_from_db()
        self.assertEqual(str(self.reservation.ride_id), response.data["id"])

    def test_polling_does_not_dispatch_another_riders_reservation(self):
        self.make_due()
        self.client.force_authenticate(self.other)
        self.assertIsNone(self.client.get(reverse("rides:current")).data)
        self.assertEqual(Ride.objects.count(), 0)

    def test_future_reservations_are_not_dispatched(self):
        self.assertIsNone(dispatch_scheduled_ride(self.reservation.pk))
        self.assertEqual(dispatch_due_scheduled_rides(), 0)

    def test_missed_window_cancels_once_and_notifies(self):
        self.make_due(minutes=16)
        self.assertEqual(dispatch_due_scheduled_rides(), 0)
        self.assertEqual(dispatch_due_scheduled_rides(), 0)
        self.reservation.refresh_from_db()
        self.assertIsNotNone(self.reservation.cancelled_at)
        self.assertEqual(Ride.objects.count(), 0)
        self.assertEqual(Notification.objects.filter(user=self.rider).count(), 1)

    def test_failed_dispatch_rolls_back_and_can_retry(self):
        self.make_due()
        with patch("rides.services.dispatch_ride", side_effect=RuntimeError("database unavailable")):
            with self.assertRaises(RuntimeError):
                dispatch_due_scheduled_rides()
        self.assertEqual(Ride.objects.count(), 0)
        self.reservation.refresh_from_db()
        self.assertIsNone(self.reservation.ride_id)
        self.assertEqual(dispatch_due_scheduled_rides(), 1)

    def test_pending_filter_excludes_archived_reservations_and_other_users(self):
        for _ in range(21):
            ScheduledRide.objects.create(rider=self.rider, quote=self.quote, scheduled_for=self.pickup - timedelta(days=2), cancelled_at=timezone.now())
        ScheduledRide.objects.create(rider=self.other, quote=self.quote, scheduled_for=self.pickup)
        response = self.client.get(reverse("rides:schedule-list"), {"pending": "1"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], str(self.reservation.pk))
