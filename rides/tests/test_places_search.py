from rides.tests.routing_fixtures import mock_routing
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

from rides.models import SavedPlace

User = get_user_model()


class PlaceSearchAndBookingTests(TestCase):
    def setUp(self):
        mock_routing(self)
        self.rider = User.objects.create_user(
            username="places_rider",
            email="places_rider@example.com",
            password="StrongPassword123!",
            role="RIDER",
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.rider)

    def test_place_search_empty_query(self):
        response = self.client.get("/api/rides/places/search/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(len(response.data) > 0)
        first = response.data[0]
        self.assertIn("label", first)
        self.assertIn("latitude", first)
        self.assertIn("longitude", first)

    def test_place_search_query_banani(self):
        response = self.client.get("/api/rides/places/search/?q=banani")
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.data]
        self.assertTrue(any("Banani" in label for label in labels))

    def test_place_search_includes_user_saved_places(self):
        SavedPlace.objects.create(
            user=self.rider,
            label="My Secret Base",
            address="Near Lake, Banani",
            latitude="23.793700",
            longitude="90.404300",
        )
        response = self.client.get("/api/rides/places/search/?q=secret")
        self.assertEqual(response.status_code, 200)
        labels = [item["label"] for item in response.data]
        self.assertIn("My Secret Base", labels)

    def test_create_quote_by_location_names_without_coordinates(self):
        # Rider specifies names without raw latitude/longitude
        data = {
            "pickup_label": "Banani Road 11",
            "destination_label": "Hazrat Shahjalal International Airport",
        }
        response = self.client.post("/api/rides/quotes/", data)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["pickup_label"], "Banani Road 11")
        self.assertEqual(response.data["destination_label"], "Hazrat Shahjalal International Airport")
        self.assertIsNotNone(response.data["fare"])
        self.assertIsNotNone(response.data["distance_km"])
        self.assertTrue(float(response.data["distance_km"]) > 0)
