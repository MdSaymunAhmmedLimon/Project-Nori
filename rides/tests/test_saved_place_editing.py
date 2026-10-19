from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework.test import APITestCase

from rides.models import SavedPlace


class SavedPlaceEditingTests(APITestCase):
    def setUp(self):
        cache.clear()
        users = get_user_model().objects
        self.rider = users.create_user(username="places_rider", email="rider@example.com")
        self.other = users.create_user(username="places_other", email="other@example.com", is_staff=True)
        self.driver = users.create_user(username="places_driver", email="driver@example.com", role="DRIVER")
        self.place = SavedPlace.objects.create(user=self.rider, label="Home", address="Banani",
                                               latitude="23.810300", longitude="90.412500")
        self.url = f"/api/rides/saved-places/{self.place.pk}/"
        self.client.force_authenticate(self.rider)

    def test_partial_edit_preserves_other_fields_and_owner(self):
        response = self.client.patch(self.url, {"address": "  Gulshan 2  ", "latitude": "23.792500"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["address"], "Gulshan 2")
        self.assertEqual(response.data["label"], "Home")
        self.assertEqual(response.data["longitude"], "90.412500")
        self.place.refresh_from_db()
        self.assertEqual(self.place.user_id, self.rider.pk)
        self.assertEqual(self.client.patch(self.url, {}).status_code, 200)

    def test_only_owner_can_edit_even_if_other_user_is_staff(self):
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.patch(self.url, {"label": "Stolen"}).status_code, 404)
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.patch(self.url, {"label": "Stolen"}).status_code, 403)
        self.client.force_authenticate(None)
        self.assertEqual(self.client.patch(self.url, {"label": "Stolen"}).status_code, 401)
        self.place.refresh_from_db()
        self.assertEqual(self.place.label, "Home")

    def test_label_conflicts_are_case_insensitive_and_atomic(self):
        SavedPlace.objects.create(user=self.rider, label="Work", address="Office", latitude=23, longitude=90)
        response = self.client.patch(self.url, {"label": " work ", "address": "Should not change"})
        self.assertEqual(response.status_code, 409)
        self.place.refresh_from_db()
        self.assertEqual(self.place.address, "Banani")
        self.assertEqual(self.place.label, "Home")
        self.assertEqual(self.client.patch(self.url, {"label": "HOME"}).status_code, 200)
        response = self.client.post("/api/rides/saved-places/", {
            "label": "home", "address": "Another home", "latitude": "23", "longitude": "90",
        })
        self.assertEqual(response.status_code, 409)

    def test_invalid_and_protected_fields_cannot_be_changed(self):
        for data in ({"latitude": "90.1"}, {"longitude": "-180.1"}, {"latitude": "invalid"},
                     {"label": " "}, {"address": ""}, {"label": "a" * 51},
                     {"user": self.other.pk}, {"id": 99}, {"created_at": "2026-01-01"}):
            with self.subTest(data=data):
                self.assertEqual(self.client.patch(self.url, data).status_code, 400)
        self.place.refresh_from_db()
        self.assertEqual(self.place.label, "Home")
        self.assertEqual(self.place.user_id, self.rider.pk)

    def test_missing_and_deleted_places_return_404(self):
        self.place.delete()
        self.assertEqual(self.client.patch(self.url, {"label": "Home"}).status_code, 404)

    def test_list_remains_paginated_and_private_after_edits(self):
        SavedPlace.objects.bulk_create([
            SavedPlace(user=self.rider, label=f"Place {number}", address="Dhaka", latitude=23, longitude=90)
            for number in range(20)
        ])
        SavedPlace.objects.create(user=self.other, label="Private", address="Private address", latitude=23, longitude=90)
        self.client.patch(self.url, {"label": "Updated home"})
        first = self.client.get("/api/rides/saved-places/").data
        second = self.client.get("/api/rides/saved-places/?page=2").data
        self.assertEqual(first["count"], 21)
        self.assertEqual(len(first["results"]), 20)
        self.assertEqual(len(second["results"]), 1)
        places = first["results"] + second["results"]
        self.assertEqual(len({place["id"] for place in places}), 21)
        self.assertIn("Updated home", [place["label"] for place in places])
        self.assertNotIn("Private", [place["label"] for place in places])
