from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.utils import timezone
from rest_framework.exceptions import APIException

from accounts.access import position_for
from accounts.models import PositionAssignment, PositionCategory, PositionEvent, PositionInvitation
from accounts.positions import create_category, decide, invite, respond, review_version, revoke
from notifications.models import Notification


class PositionTests(TestCase):
    def setUp(self):
        User = get_user_model()
        self.admin = User.objects.create_superuser(username="position-admin", email="admin@positions.test", password="test-password")
        self.user = User.objects.create_user(username="candidate", email="candidate@positions.test")
        self.other = User.objects.create_user(username="stranger", email="stranger@positions.test")
        self.category = PositionCategory.objects.get(name="Moderator")

    def invitation(self, user=None, category=None):
        return invite(self.admin, user_id=(user or self.user).pk, category_id=(category or self.category).pk, note="Please join our support team.")

    def approve(self, invitation, actor=None):
        invitation.refresh_from_db()
        return decide(actor or self.admin, invitation.pk, decision="approve", note="Identity, consent and powers checked.", expected_version=review_version(invitation))

    def test_invite_accept_final_approve_grants_only_offered_powers(self):
        invitation = self.invitation()
        self.assertFalse(self.user.has_perm("rides.change_sosalert"))
        with self.assertRaises(APIException):
            self.approve(invitation)
        respond(self.user, invitation.pk, decision="accept")
        self.assertFalse(self.user.has_perm("rides.change_sosalert"))
        self.approve(invitation)
        self.assertTrue(self.user.has_perm("rides.change_sosalert"))
        self.assertFalse(self.user.has_perm("accounts.manage_positions"))
        self.assertFalse(self.user.is_staff)
        self.assertFalse(self.user.is_superuser)
        self.assertEqual(list(PositionEvent.objects.filter(invitation=invitation).values_list("action", flat=True)), ["INVITED", "ACCEPTED", "APPROVED"])
        self.assertEqual(Notification.objects.filter(user=self.user, category="POSITION").count(), 3)
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/app/"), "/team/")
        self.assertContains(self.client.get("/team/"), "MODERATOR WORKSPACE")
        self.assertEqual(self.client.get("/operations/support/").status_code, 200)
        self.assertEqual(self.client.get("/operations/users/").status_code, 403)
        self.assertEqual(self.client.get("/operations/payouts/").status_code, 403)

    def test_rejection_decline_withdraw_and_expiry_never_grant_powers(self):
        invitation = self.invitation()
        respond(self.user, invitation.pk, decision="decline", note="Not now.")
        with self.assertRaises(APIException):
            self.approve(invitation)
        invitation = self.invitation()
        respond(self.user, invitation.pk, decision="accept")
        invitation.refresh_from_db()
        decide(self.admin, invitation.pk, decision="reject", note="More experience required.", expected_version=review_version(invitation))
        self.assertIsNone(position_for(self.user))
        invitation = self.invitation()
        decide(self.admin, invitation.pk, decision="withdraw", note="Position closed.", expected_version=review_version(invitation))
        with self.assertRaises(APIException):
            respond(self.user, invitation.pk, decision="accept")
        invitation = self.invitation()
        PositionInvitation.objects.filter(pk=invitation.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(APIException):
            respond(self.user, invitation.pk, decision="accept")
        replacement = self.invitation()
        invitation.refresh_from_db()
        self.assertEqual(invitation.status, "EXPIRED")
        self.assertNotEqual(replacement.pk, invitation.pk)

    def test_only_owner_can_respond_and_only_admin_can_review(self):
        invitation = self.invitation()
        for actor in (self.other, self.admin):
            with self.assertRaises(APIException):
                respond(actor, invitation.pk, decision="accept")
        with self.assertRaises(APIException):
            invite(self.user, user_id=self.other.pk, category_id=self.category.pk)
        respond(self.user, invitation.pk, decision="accept")
        with self.assertRaises(APIException):
            self.approve(invitation, self.user)
        with self.assertRaises(APIException):
            invite(self.admin, user_id=self.admin.pk, category_id=self.category.pk)

    def test_stale_review_inactive_user_and_repeated_decisions_are_rejected(self):
        invitation = self.invitation()
        respond(self.user, invitation.pk, decision="accept")
        invitation.refresh_from_db()
        version = review_version(invitation)
        self.user.phone_number = "+8801712345678"
        self.user.save()
        with self.assertRaises(APIException):
            decide(self.admin, invitation.pk, decision="approve", note="Checked", expected_version=version)
        self.user.is_active = False
        self.user.save()
        with self.assertRaises(APIException):
            self.approve(invitation)
        self.user.is_active = True
        self.user.save()
        self.approve(invitation)
        with self.assertRaises(APIException):
            self.approve(invitation)
        self.assertEqual(PositionAssignment.objects.filter(user=self.user).count(), 1)

    def test_permissions_are_snapshotted_and_revocation_is_immediate(self):
        invitation = self.invitation()
        self.category.permissions.append("accounts.manage_positions")
        self.category.save()
        respond(self.user, invitation.pk, decision="accept")
        self.approve(invitation)
        self.assertFalse(self.user.has_perm("accounts.manage_positions"))
        self.assertTrue(self.user.has_perm("rides.change_sosalert"))
        revoke(self.admin, self.user.pk, reason="Assignment ended.")
        self.assertFalse(self.user.has_perm("rides.change_sosalert"))
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/operations/support/").status_code, 403)
        self.assertTrue(PositionEvent.objects.filter(action="REVOKED").exists())

    def test_custom_categories_have_bounded_permissions_and_read_dependencies(self):
        category = create_category(self.admin, name="Dispatch", description="Handle trip issues", dashboard="TEAM", permissions=["accounts.cancel_operational_ride"])
        self.assertEqual(set(category.permissions), {"accounts.cancel_operational_ride", "rides.view_ride"})
        for permissions, dashboard in [(["auth.change_permission"], "TEAM"), (["accounts.manage_positions"], "MODERATOR"), (["rides.view_ride"], "DRIVER")]:
            with self.assertRaises(APIException):
                create_category(self.admin, name="Invalid", description="", dashboard=dashboard, permissions=permissions)
        with self.assertRaises(APIException):
            create_category(self.admin, name="dispatch", description="", dashboard="TEAM", permissions=[])

    def test_replacement_requires_fresh_consent_and_replaces_previous_powers(self):
        invitation = self.invitation()
        respond(self.user, invitation.pk, decision="accept")
        self.approve(invitation)
        limited = create_category(self.admin, name="Observer", description="Read trips", dashboard="TEAM", permissions=["rides.view_ride"])
        replacement = self.invitation(category=limited)
        self.assertTrue(self.user.has_perm("rides.change_sosalert"))
        respond(self.user, replacement.pk, decision="accept")
        self.approve(replacement)
        self.assertFalse(self.user.has_perm("rides.change_sosalert"))
        self.assertTrue(self.user.has_perm("rides.view_ride"))

    def test_approved_administrator_can_manage_positions_without_django_admin_access(self):
        invitation = self.invitation(category=PositionCategory.objects.get(name="Administrator"))
        respond(self.user, invitation.pk, decision="accept")
        self.approve(invitation)
        self.assertTrue(self.user.has_perm("accounts.manage_positions"))
        self.client.force_login(self.user)
        self.assertRedirects(self.client.get("/app/"), "/operations/")
        self.assertEqual(self.client.get("/positions/categories/new/").status_code, 200)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        invite(self.user, user_id=self.other.pk, category_id=self.category.pk)

    def test_web_forms_csrf_privacy_confirmation_and_status_transitions(self):
        self.client.force_login(self.admin)
        for path in ("/positions/", "/positions/categories/new/", "/positions/invite/", "/notifications/", "/notifications/send/"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        response = self.client.post("/positions/invite/", {"username": self.user.username, "category": self.category.pk, "note": "Welcome"})
        invitation = PositionInvitation.objects.get(user=self.user)
        self.assertRedirects(response, f"/positions/{invitation.pk}/")
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(f"/positions/{invitation.pk}/").status_code, 403)
        self.assertEqual(self.client.get("/positions/categories/new/").status_code, 403)
        self.client.force_login(self.user)
        self.client.post(f"/positions/{invitation.pk}/respond/", {"decision": "accept"})
        invitation.refresh_from_db()
        self.client.force_login(self.admin)
        data = {"decision": "approve", "note": "Checked again", "version": review_version(invitation)}
        self.client.post(f"/positions/{invitation.pk}/decide/", data)
        self.assertIsNone(position_for(self.user))
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.admin)
        self.assertEqual(csrf.post(f"/positions/{invitation.pk}/decide/", {**data, "confirmed": "yes"}).status_code, 403)
        self.client.post(f"/positions/{invitation.pk}/decide/", {**data, "confirmed": "yes"})
        self.assertIsNotNone(position_for(self.user))

    def test_driver_invitation_requires_acceptance_and_final_credentials_review(self):
        from drivers.review import review_driver
        from rides.tests.test_workflow import make_driver
        driver = make_driver("position-driver")
        profile = driver.driver_profile
        profile.verification_status = "PENDING"
        profile.is_online = False
        profile.save()
        with self.assertRaises(APIException):
            review_driver(self.admin, profile.pk, decision="APPROVED")
        invitation = self.invitation(user=driver, category=PositionCategory.objects.get(name="Driver"))
        with self.assertRaises(APIException):
            self.approve(invitation)
        respond(driver, invitation.pk, decision="accept")
        self.approve(invitation)
        profile.refresh_from_db()
        self.assertEqual(profile.verification_status, "APPROVED")
        self.assertFalse(profile.is_online)
        self.client.force_login(driver)
        self.assertContains(self.client.get("/app/"), "DRIVER WORKSPACE")
        revoke(self.admin, driver.pk, reason="No longer driving.")
        profile.refresh_from_db()
        self.assertEqual(profile.verification_status, "PENDING")

    def test_notification_reads_are_private_and_announcements_require_power(self):
        own = Notification.objects.create(user=self.user, message="Your message")
        other = Notification.objects.create(user=self.other, message="Private message")
        self.client.force_login(self.user)
        self.assertNotContains(self.client.get("/notifications/"), "Private message")
        self.assertEqual(self.client.post("/notifications/", {"action": "read-one", "notification": other.pk}).status_code, 404)
        self.client.post("/notifications/", {"action": "read-one", "notification": own.pk})
        own.refresh_from_db()
        other.refresh_from_db()
        self.assertIsNotNone(own.read_at)
        self.assertIsNone(other.read_at)
        self.assertEqual(self.client.post("/notifications/send/", {}).status_code, 403)
        self.client.force_login(self.admin)
        self.client.post("/notifications/send/", {"username": self.user.username, "title": "Welcome", "message": "Please review your invitation."})
        self.assertTrue(Notification.objects.filter(user=self.user, title="Welcome", sent_by=self.admin).exists())
