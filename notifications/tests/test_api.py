from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from notifications.models import Notification


class NotificationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="activity_rider", email="activity@example.com")
        cls.other = get_user_model().objects.create_user(username="other_rider", email="other@example.com")

    def setUp(self):
        self.client.force_authenticate(self.user)

    def test_pagination_counts_unread_across_all_pages_and_only_own_notifications(self):
        Notification.objects.bulk_create([
            Notification(user=self.user, message=f"Update {number}") for number in range(25)
        ])
        Notification.objects.create(user=self.other, message="Private update")
        # The newest page is entirely read; five unread items remain on page two.
        newest = list(Notification.objects.filter(user=self.user).values_list("pk", flat=True)[:20])
        Notification.objects.filter(pk__in=newest).update(read_at=timezone.now())

        first = self.client.get("/api/notifications/")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.data["count"], 25)
        self.assertEqual(first.data["unread_count"], 5)
        self.assertEqual(len(first.data["results"]), 20)
        self.assertIsNotNone(first.data["next"])
        second = self.client.get(first.data["next"])
        self.assertEqual(second.data["unread_count"], 5)
        self.assertEqual(len(second.data["results"]), 5)
        self.assertIsNone(second.data["next"])
        ids = {item["id"] for item in first.data["results"] + second.data["results"]}
        self.assertSetEqual(ids, set(Notification.objects.filter(user=self.user).values_list("pk", flat=True)))

    def test_equal_timestamps_have_stable_page_order(self):
        Notification.objects.bulk_create([Notification(user=self.user, message="Update") for _ in range(21)])
        Notification.objects.update(created_at=timezone.now())
        first = self.client.get("/api/notifications/")
        second = self.client.get(first.data["next"])
        ids = [item["id"] for item in first.data["results"] + second.data["results"]]
        self.assertEqual(ids, list(Notification.objects.order_by("-pk").values_list("pk", flat=True)))

    @patch("notifications.views.publish")
    def test_mark_all_read_is_private_idempotent_and_publishes_update(self, publish):
        own = Notification.objects.create(user=self.user, message="Own update")
        other = Notification.objects.create(user=self.other, message="Other update")
        self.assertEqual(self.client.post("/api/notifications/read/", {}).status_code, 200)
        own.refresh_from_db()
        other.refresh_from_db()
        first_read = own.read_at
        self.assertIsNotNone(first_read)
        self.assertIsNone(other.read_at)
        publish.assert_called_once_with(self.user.pk, "notifications_read")
        self.client.post("/api/notifications/read/", {})
        own.refresh_from_db()
        self.assertEqual(own.read_at, first_read)
        publish.assert_called_once()
        self.assertEqual(self.client.get("/api/notifications/").data["unread_count"], 0)

    def test_empty_activity_and_authentication(self):
        response = self.client.get("/api/notifications/")
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["unread_count"], 0)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.get("/api/notifications/").status_code, 401)
        self.assertEqual(self.client.post("/api/notifications/read/", {}).status_code, 401)
