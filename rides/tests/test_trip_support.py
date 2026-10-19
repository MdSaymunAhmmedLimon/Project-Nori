import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APITestCase

from notifications.models import Notification
from rides.models import FavouriteDriver, LostItem, Ride, RideQuote
from rides.services import Conflict
from rides.support import update_lost_item


class TripSupportTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model().objects
        cls.rider = users.create_user(username="support_rider", email="rider@example.com")
        cls.driver = users.create_user(username="support_driver", email="driver@example.com", role="DRIVER", phone_number="+8801712345678")
        cls.other = users.create_user(username="support_other", email="other@example.com", is_staff=True)
        cls.admin = users.create_superuser(username="support_admin", email="admin@example.com", password=None)
        quote = RideQuote.objects.create(
            rider=cls.rider, pickup_label="Banani", destination_label="Gulshan",
            pickup_latitude=23, pickup_longitude=90, destination_latitude=24, destination_longitude=91,
            distance_km=4, estimated_minutes=10, fare=200, platform_fee=30, currency="BDT",
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        cls.ride = Ride.objects.create(rider=cls.rider, driver=cls.driver, quote=quote,
                                      request_id=uuid.uuid4(), status="COMPLETED", completed_at=timezone.now())

    def setUp(self):
        cache.clear()
        self.client.force_authenticate(self.rider)
        self.favourite_url = f"/api/rides/{self.ride.pk}/favourite-driver/"
        self.report_url = f"/api/rides/{self.ride.pk}/lost-items/"

    def report(self):
        response = self.client.post(self.report_url, {"description": "Blue bag on the back seat"})
        self.assertEqual(response.status_code, 201, response.data)
        return LostItem.objects.get(pk=response.data["id"])

    def test_favourite_is_idempotent_and_does_not_expose_contact_details(self):
        first = self.client.post(self.favourite_url, {})
        second = self.client.post(self.favourite_url, {})
        self.assertEqual(first.status_code, 201)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(first.data["id"], second.data["id"])
        self.assertEqual(FavouriteDriver.objects.count(), 1)
        response = self.client.get("/api/rides/favourite-drivers/")
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["driver_name"], "support_driver")
        self.assertNotIn("driver_phone", response.data["results"][0])
        self.assertNotContains(response, self.driver.phone_number)
        self.assertNotContains(response, self.driver.email)

    def test_favourites_are_private_and_can_only_be_removed_by_owner(self):
        favourite = self.client.post(self.favourite_url, {}).data["id"]
        url = f"/api/rides/favourite-drivers/{favourite}/"
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get("/api/rides/favourite-drivers/").data["count"], 0)
        self.assertEqual(self.client.delete(url).status_code, 404)
        self.assertEqual(self.client.post(self.favourite_url, {}).status_code, 404)
        self.client.force_authenticate(self.rider)
        self.assertEqual(self.client.delete(url).status_code, 204)
        self.assertFalse(FavouriteDriver.objects.exists())

    def test_completed_trip_and_available_driver_are_required(self):
        for status in ("CANCELLED", "IN_PROGRESS"):
            Ride.objects.filter(pk=self.ride.pk).update(status=status)
            self.assertEqual(self.client.post(self.favourite_url, {}).status_code, 409)
            self.assertEqual(self.client.post(self.report_url, {"description": "Bag"}).status_code, 409)
        Ride.objects.filter(pk=self.ride.pk).update(status="COMPLETED")
        get_user_model().objects.filter(pk=self.driver.pk).update(is_active=False)
        self.assertEqual(self.client.post(self.favourite_url, {}).status_code, 409)

    def test_authentication_role_and_ownership_are_required(self):
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post(self.report_url, {"description": "Bag"}).status_code, 401)
        self.assertEqual(self.client.get("/api/rides/favourite-drivers/").status_code, 401)
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.post(self.favourite_url, {}).status_code, 403)
        self.assertEqual(self.client.post(self.report_url, {"description": "Bag"}).status_code, 403)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.post(self.report_url, {"description": "Bag"}).status_code, 404)

    def test_duplicate_reports_retry_safely_and_conflicting_report_is_rejected(self):
        report = self.report()
        duplicate = self.client.post(self.report_url, {"description": "  Blue bag on the back seat  "})
        self.assertEqual(duplicate.status_code, 200)
        self.assertEqual(duplicate.data["id"], report.pk)
        self.assertEqual(Notification.objects.filter(user=self.rider).count(), 1)
        conflict = self.client.post(self.report_url, {"description": "A different item"})
        self.assertEqual(conflict.status_code, 409)
        self.assertEqual(LostItem.objects.count(), 1)

    def test_report_validation_rejects_blank_long_and_protected_fields(self):
        for data in ({"description": " "}, {"description": "a" * 501},
                     {"description": "Bag", "status": "RETURNED"},
                     {"description": "Bag", "admin_note": "Tampered"},
                     {"description": "Bag", "reporter": self.other.pk}):
            self.assertEqual(self.client.post(self.report_url, data).status_code, 400)
        self.assertFalse(LostItem.objects.exists())

    def test_status_updates_notify_rider_but_internal_notes_stay_private(self):
        report = self.report()
        update_lost_item(self.admin, report.pk, status="FOUND", admin_note="Private driver contact details")
        report.refresh_from_db()
        self.assertIsNone(report.resolved_at)
        update_lost_item(self.admin, report.pk, status="RETURNED", admin_note="Private driver contact details")
        report.refresh_from_db()
        resolved_at = report.resolved_at
        self.assertIsNotNone(resolved_at)
        count = Notification.objects.count()
        update_lost_item(self.admin, report.pk, status="RETURNED", admin_note="Another internal note")
        report.refresh_from_db()
        self.assertEqual(report.resolved_at, resolved_at)
        self.assertEqual(Notification.objects.count(), count)
        response = self.client.get("/api/rides/lost-items/")
        self.assertEqual(response.data["results"][0]["status"], "RETURNED")
        self.assertNotIn("admin_note", response.data["results"][0])
        self.assertNotContains(response, "Another internal note")
        self.assertFalse(Notification.objects.filter(message__icontains="Private driver").exists())

    def test_only_authorized_staff_can_update_and_reopen_reports(self):
        report = self.report()
        for user in (self.rider, self.other):
            with self.assertRaises(PermissionDenied):
                update_lost_item(user, report.pk, status="RETURNED", admin_note="")
        update_lost_item(self.admin, report.pk, status="NOT_FOUND", admin_note="")
        update_lost_item(self.admin, report.pk, status="CONTACTING", admin_note="")
        report.refresh_from_db()
        self.assertIsNone(report.resolved_at)
        update_lost_item(self.admin, report.pk, status="RETURNED", admin_note="")
        new_report = self.client.post(self.report_url, {"description": "Another missing item"})
        self.assertEqual(new_report.status_code, 201)
        with self.assertRaises(Conflict):
            update_lost_item(self.admin, report.pk, status="CONTACTING", admin_note="")
        report.refresh_from_db()
        self.assertEqual(report.status, "RETURNED")

    def test_report_list_is_private_paginated_and_read_only(self):
        LostItem.objects.bulk_create([LostItem(ride=self.ride, reporter=self.rider, description=f"Item {n}", status="RETURNED") for n in range(21)])
        first = self.client.get("/api/rides/lost-items/").data
        second = self.client.get("/api/rides/lost-items/?page=2").data
        self.assertEqual(first["count"], 21)
        self.assertEqual(len(first["results"]), 20)
        self.assertEqual(len(second["results"]), 1)
        self.assertEqual(self.client.post("/api/rides/lost-items/", {}).status_code, 405)
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get("/api/rides/lost-items/").data["count"], 0)

    def test_admin_form_updates_status_without_changing_report_details(self):
        report = self.report()
        self.client.force_authenticate(None)
        self.client.force_login(self.admin)
        url = reverse("admin:rides_lostitem_change", args=[report.pk])
        self.assertContains(self.client.get(url), "Never shown to riders or drivers")
        response = self.client.post(url, {"status": "RETURNED", "admin_note": "Staff-only note", "description": "Tampered", "_save": "Save"}, format="multipart")
        self.assertEqual(response.status_code, 302)
        report.refresh_from_db()
        self.assertEqual(report.status, "RETURNED")
        self.assertEqual(report.description, "Blue bag on the back seat")
        self.assertEqual(report.admin_note, "Staff-only note")
        self.assertIsNotNone(report.resolved_at)
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(url).status_code, 403)
