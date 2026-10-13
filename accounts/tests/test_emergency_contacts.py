from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework.test import APITestCase


class EmergencyContactTests(APITestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(
            username="contact_owner", email="contact_owner@example.com", password="Strong-pass-123"
        )
        self.other_user = user_model.objects.create_user(
            username="other_owner", email="other_owner@example.com", password="Strong-pass-123"
        )
        self.client.force_authenticate(self.user)
        self.list_url = reverse("accounts:emergency-contacts")
        self.payload = {"name": "Sam", "phone_number": "+8801712345678", "relationship": "Friend"}

    def test_owner_can_create_list_and_delete_contact(self):
        created = self.client.post(self.list_url, self.payload)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data["name"], "Sam")
        self.assertEqual(self.client.get(self.list_url).data[0]["phone_number"], self.payload["phone_number"])

        deleted = self.client.delete(reverse("accounts:emergency-contact-detail", args=[created.data["id"]]))
        self.assertEqual(deleted.status_code, 204)
        self.assertEqual(self.client.get(self.list_url).data, [])

    def test_contact_list_and_delete_are_scoped_to_owner(self):
        created = self.client.post(self.list_url, self.payload)
        self.client.force_authenticate(self.other_user)
        self.assertEqual(self.client.get(self.list_url).data, [])
        self.assertEqual(
            self.client.delete(reverse("accounts:emergency-contact-detail", args=[created.data["id"]])).status_code,
            404,
        )

    def test_requires_international_phone_and_rejects_duplicates(self):
        self.assertEqual(self.client.post(self.list_url, {**self.payload, "phone_number": "01712345678"}).status_code, 400)
        self.assertEqual(self.client.post(self.list_url, self.payload).status_code, 201)
        duplicate = self.client.post(self.list_url, self.payload)
        self.assertEqual(duplicate.status_code, 400)

    def test_contact_limit_is_five(self):
        for number in range(5):
            payload = {**self.payload, "phone_number": f"+8801712345{number:03d}"}
            response = self.client.post(self.list_url, payload)
            self.assertEqual(response.status_code, 201, response.data)
        response = self.client.post(self.list_url, {**self.payload, "phone_number": "+8801712345999"})
        self.assertEqual(response.status_code, 400)
