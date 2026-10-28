import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.contrib.admin.models import LogEntry
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from drivers.models import DriverDocument, PayoutRequest
from drivers.review import review_version
from notifications.models import Notification
from rides.models import LostItem, Ride, RideQuote, SOSAlert
from rides.tests.test_workflow import make_driver


class OperationsTests(TestCase):
    def setUp(self):
        cache.clear()
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="ops-admin", email="ops@example.com", password="Operations-test-123!")
        self.viewer = User.objects.create_user(username="viewer", email="viewer@example.com", is_staff=True)
        self.rider = User.objects.create_user(username="ops-rider", email="ops-rider@example.com")
        self.driver = make_driver("ops-driver")
        self.profile = self.driver.driver_profile
        self.profile.verification_status = "PENDING"
        self.profile.is_online = False
        self.profile.save()
        self.client.force_login(self.admin)
        self.detail = f"/operations/drivers/{self.profile.pk}/"
        self.review = self.detail + "review/"

    def decision(self, decision="APPROVED", note="", version=None):
        self.profile.refresh_from_db()
        return self.client.post(self.review, {"decision": decision, "note": note,
            "expected_version": version or review_version(self.profile)})

    def final_approval(self):
        from accounts.models import PositionCategory, PositionInvitation
        from accounts.positions import invite, respond, review_version as position_version
        invitation = PositionInvitation.objects.filter(user=self.driver, status="ACCEPTED").first()
        if not invitation:
            invitation = invite(self.admin, user_id=self.driver.pk, category_id=PositionCategory.objects.get(name="Driver").pk)
            respond(self.driver, invitation.pk, decision="accept")
            invitation.refresh_from_db()
        return self.client.post(f"/positions/{invitation.pk}/decide/", {"decision": "approve", "note": "License and vehicle checked.", "confirmed": "yes", "version": position_version(invitation)})

    def ride(self, status="COMPLETED"):
        quote = RideQuote.objects.create(rider=self.rider, pickup_label="Banani", destination_label="Gulshan",
            pickup_latitude="23.810300", pickup_longitude="90.412500", destination_latitude="23.780600",
            destination_longitude="90.419300", distance_km="4.29", estimated_minutes=8, fare="150",
            platform_fee="22.50", currency="BDT", expires_at=timezone.now() + timedelta(minutes=10))
        return Ride.objects.create(rider=self.rider, driver=self.driver, quote=quote, status=status, request_id=uuid.uuid4())

    def test_dashboard_and_all_management_pages_render(self):
        ride = self.ride()
        SOSAlert.objects.create(ride=ride, raised_by=self.rider, message="Help")
        LostItem.objects.create(ride=ride, reporter=self.rider, description="Backpack")
        PayoutRequest.objects.create(driver=self.profile, amount="50", payout_method="BKASH", account_details="+8801700000000")
        for url in ["/operations/", self.detail, *[f"/operations/{section}/" for section in ("drivers", "rides", "users", "payouts", "support")], "/operations/support/?kind=lost"]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "Driver approvals")
                self.assertIn("no-store", response.headers["Cache-Control"])

    def test_only_staff_can_access_management_and_permissions_control_changes(self):
        self.client.force_login(self.rider)
        self.assertEqual(self.client.get(self.detail).status_code, 403)
        self.assertEqual(self.decision().status_code, 403)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(self.detail).status_code, 200)
        self.assertNotContains(self.client.get(self.detail), 'id="driver-review-form"')
        self.assertEqual(self.decision().status_code, 403)
        self.viewer.user_permissions.add(Permission.objects.get(content_type__app_label="drivers", codename="change_driverprofile"))
        self.assertEqual(self.decision().status_code, 302)

    def test_approval_enables_driving_and_records_audit_and_notification(self):
        api = APIClient()
        api.force_authenticate(self.driver)
        self.assertEqual(api.post("/api/drivers/availability/", {"is_online": True}).status_code, 409)
        response = self.final_approval()
        self.assertRedirects(response, response.url)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "APPROVED")
        self.assertFalse(self.profile.is_online)
        self.assertEqual(api.post("/api/drivers/availability/", {"is_online": True}).status_code, 200)
        self.assertTrue(Notification.objects.filter(user=self.driver, message__contains="Approved").exists())
        self.assertTrue(LogEntry.objects.filter(user=self.admin, object_id=str(self.profile.pk), change_message__contains="Driver review").exists())

    def test_rejection_requires_reason_and_takes_driver_offline(self):
        self.decision("REJECTED")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "PENDING")
        self.decision()
        self.profile.is_online = True
        self.profile.save()
        self.decision("REJECTED", "Please upload a clear license photo.")
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "REJECTED")
        self.assertFalse(self.profile.is_online)

    def test_approval_rejects_missing_vehicle_expired_license_and_inactive_account(self):
        self.profile.license_expiry_date = timezone.localdate() - timedelta(days=1)
        self.profile.save()
        self.decision()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "PENDING")
        self.profile.license_expiry_date = timezone.localdate() + timedelta(days=30)
        self.profile.save()
        self.driver.is_active = False
        self.driver.save()
        self.decision()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "PENDING")
        self.driver.is_active = True
        self.driver.save()
        self.profile.vehicle.delete()
        self.decision()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "PENDING")

    def test_submitted_documents_must_be_accepted_before_approval(self):
        doc = DriverDocument.objects.create(driver=self.profile, doc_type="LICENSE_FRONT", file="driver_docs/test.jpg")
        self.final_approval()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "PENDING")
        self.client.post(f"/operations/documents/{doc.pk}/review/", {"decision": "ACCEPTED", "expected_version": review_version(self.profile)})
        doc.refresh_from_db()
        self.assertEqual(doc.review_status, "ACCEPTED")
        self.final_approval()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "APPROVED")

    def test_stale_review_cannot_overwrite_updated_application(self):
        version = review_version(self.profile)
        self.profile.review_note = "Another reviewer is checking this."
        self.profile.save()
        result = self.decision(version=version)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "PENDING")
        self.assertContains(self.client.get(result.url), "changed after you opened it")

    def test_live_location_and_availability_do_not_invalidate_review(self):
        from drivers.services import set_availability, update_location
        version = review_version(self.profile)
        update_location(self.driver, latitude="23.809100", longitude="90.410000")
        self.assertEqual(review_version(self.profile), version)
        self.final_approval()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "APPROVED")
        version = review_version(self.profile)
        set_availability(self.driver, True)
        self.decision("REJECTED", "Please update the license photo.", version=version)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "REJECTED")
        self.assertFalse(self.profile.is_online)

    def test_document_download_requires_review_permission_and_handles_missing_file(self):
        document = DriverDocument.objects.create(driver=self.profile, doc_type="LICENSE_FRONT", file="driver_docs/missing.jpg")
        url = f"/operations/documents/{document.pk}/file/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.client.force_login(self.viewer)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_mutations_require_post_and_csrf(self):
        self.assertEqual(self.client.get(self.review).status_code, 405)
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        self.assertEqual(csrf_client.post(self.review, {"decision": "APPROVED"}).status_code, 403)

    def test_filters_and_pagination(self):
        self.assertContains(self.client.get("/operations/drivers/?q=ops-driver"), "ops-driver")
        self.assertNotContains(self.client.get("/operations/drivers/?q=missing"), 'class="panel ops-driver-card"')
        self.final_approval()
        self.assertNotContains(self.client.get("/operations/drivers/"), 'class="panel ops-driver-card"')
        self.assertContains(self.client.get("/operations/drivers/?status=APPROVED"), "ops-driver")
        for index in range(23):
            get_user_model().objects.create_user(username=f"search-{index}", email=f"search-{index}@example.com")
        response = self.client.get("/operations/users/?q=search-&page=2")
        self.assertEqual(len(response.context["page"]), 3)

    def test_user_access_protects_staff_and_active_trip_accounts(self):
        self.client.post(f"/operations/users/{self.admin.pk}/access/", {"action": "deactivate"})
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)
        ride = self.ride("ACCEPTED")
        self.client.post(f"/operations/users/{self.rider.pk}/access/", {"action": "deactivate"})
        self.rider.refresh_from_db()
        self.assertTrue(self.rider.is_active)
        ride.status = "COMPLETED"
        ride.save()
        self.client.post(f"/operations/users/{self.rider.pk}/access/", {"action": "deactivate"})
        self.rider.refresh_from_db()
        self.assertFalse(self.rider.is_active)

    def test_payout_workflow_and_final_status_protection(self):
        payout = PayoutRequest.objects.create(driver=self.profile, amount="50", payout_method="BKASH", account_details="01700000000")
        url = f"/operations/payouts/{payout.pk}/process/"
        self.client.post(url, {"action": "pay", "admin_note": "REF-1"})
        payout.refresh_from_db()
        self.assertEqual(payout.status, "PENDING")
        self.client.post(url, {"action": "approve"})
        self.client.post(url, {"action": "pay"})
        payout.refresh_from_db()
        self.assertEqual(payout.status, "APPROVED")
        self.client.post(url, {"action": "pay", "admin_note": "REF-1"})
        self.client.post(url, {"action": "reject", "admin_note": "Too late"})
        payout.refresh_from_db()
        self.assertEqual(payout.status, "PAID")
        self.assertEqual(payout.admin_note, "REF-1")

    def test_safety_and_lost_item_updates(self):
        ride = self.ride()
        alert = SOSAlert.objects.create(ride=ride, raised_by=self.rider, message="Help")
        report = LostItem.objects.create(ride=ride, reporter=self.rider, description="Backpack")
        self.client.post(f"/operations/sos/{alert.pk}/resolve/")
        self.client.post(f"/operations/lost-items/{report.pk}/update/", {"status": "FOUND", "admin_note": "At reception"})
        alert.refresh_from_db()
        report.refresh_from_db()
        self.assertEqual(alert.resolved_by, self.admin)
        self.assertEqual(report.status, "FOUND")

    def test_document_replacement_returns_approved_driver_to_review(self):
        self.decision()
        with tempfile.TemporaryDirectory(dir=settings.BASE_DIR / ".venv") as media:
            with override_settings(MEDIA_ROOT=Path(media)):
                api = APIClient()
                api.force_authenticate(self.driver)
                result = api.post("/api/drivers/documents/", {"doc_type": "LICENSE_FRONT",
                    "file": SimpleUploadedFile("license.jpg", b"photo", content_type="image/jpeg")}, format="multipart")
                self.assertEqual(result.status_code, 201)
                self.profile.refresh_from_db()
                self.assertEqual(self.profile.verification_status, "PENDING")
                self.assertFalse(self.profile.is_online)

    def test_staff_login_lands_on_dashboard(self):
        self.client.logout()
        result = self.client.post("/login/", {"username": self.admin.username, "password": "Operations-test-123!"})
        self.assertRedirects(result, "/operations/")
