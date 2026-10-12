import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from drivers.models import DriverProfile, Vehicle

User = get_user_model()


class DriverAPITests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.driver = User.objects.create_user(
            username="driver", email="driver@example.com", password=cls.password, role="DRIVER"
        )
        cls.other_driver = User.objects.create_user(
            username="other", email="other@example.com", role="DRIVER"
        )
        cls.rider = User.objects.create_user(username="rider", email="rider@example.com")

    def setUp(self):
        cache.clear()
        self.profile_data = {
            "license_number": "DL-123456",
            "license_expiry_date": (timezone.localdate() + timedelta(days=365)).isoformat(),
        }
        self.vehicle_data = {
            "license_plate": "DHAKA-1234", "make": "Toyota", "model": "Corolla",
            "color": "Silver", "year": timezone.localdate().year, "seats": 4,
        }

    def create_profile(self, user=None, license_number="DL-123456"):
        return DriverProfile.objects.create(
            user=user or self.driver, license_number=license_number,
            license_expiry_date=timezone.localdate() + timedelta(days=365),
        )

    def test_anonymous_users_cannot_access_driver_endpoints(self):
        for endpoint in ("drivers:profile", "drivers:vehicle"):
            for method in ("get", "post", "patch"):
                with self.subTest(endpoint=endpoint, method=method):
                    response = getattr(self.client, method)(reverse(endpoint))
                    self.assertEqual(response.status_code, 401)

    def test_riders_cannot_access_driver_endpoints(self):
        self.client.force_authenticate(self.rider)
        for endpoint in ("drivers:profile", "drivers:vehicle"):
            for method in ("get", "post", "patch"):
                with self.subTest(endpoint=endpoint, method=method):
                    response = getattr(self.client, method)(reverse(endpoint))
                    self.assertEqual(response.status_code, 403)

    def test_driver_onboarding_with_real_jwt(self):
        login = self.client.post(
            reverse("accounts:login"), {"username": "driver", "password": self.password}
        )
        self.assertEqual(login.status_code, 200, login.data)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {login.data['access']}")
        profile = self.client.post(reverse("drivers:profile"), self.profile_data)
        self.assertEqual(profile.status_code, 201, profile.data)
        self.assertEqual(profile.data["user"], self.driver.pk)
        vehicle = self.client.post(reverse("drivers:vehicle"), self.vehicle_data)
        self.assertEqual(vehicle.status_code, 201, vehicle.data)
        self.assertEqual(vehicle.data["driver"], profile.data["id"])
        self.assertEqual(self.client.get(reverse("drivers:profile")).data["id"], profile.data["id"])
        self.assertEqual(self.client.get(reverse("drivers:vehicle")).data["id"], vehicle.data["id"])

    def test_missing_resources_return_404(self):
        self.client.force_authenticate(self.driver)
        for endpoint in ("drivers:profile", "drivers:vehicle"):
            self.assertEqual(self.client.get(reverse(endpoint)).status_code, 404)
            self.assertEqual(self.client.patch(reverse(endpoint), {}).status_code, 404)

    def test_vehicle_requires_profile(self):
        self.client.force_authenticate(self.driver)
        response = self.client.post(reverse("drivers:vehicle"), self.vehicle_data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Vehicle.objects.count(), 0)

    def test_only_one_profile_per_driver(self):
        self.create_profile()
        self.client.force_authenticate(self.driver)
        self.profile_data["license_number"] = "DL-OTHER"
        response = self.client.post(reverse("drivers:profile"), self.profile_data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DriverProfile.objects.count(), 1)

    def test_only_one_vehicle_per_driver(self):
        profile = self.create_profile()
        Vehicle.objects.create(driver=profile, **self.vehicle_data)
        self.client.force_authenticate(self.driver)
        self.vehicle_data["license_plate"] = "DIFFERENT-1234"
        response = self.client.post(reverse("drivers:vehicle"), self.vehicle_data)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Vehicle.objects.count(), 1)

    def test_profile_validation(self):
        self.client.force_authenticate(self.driver)
        for changes in (
            {"license_number": " "},
            {"license_expiry_date": (timezone.localdate() - timedelta(days=1)).isoformat()},
            {"license_expiry_date": "invalid-date"},
        ):
            with self.subTest(changes=changes):
                response = self.client.post(reverse("drivers:profile"), self.profile_data | changes)
                self.assertEqual(response.status_code, 400, response.data)

    def test_profile_required_fields(self):
        self.client.force_authenticate(self.driver)
        response = self.client.post(reverse("drivers:profile"), {})
        self.assertEqual(response.status_code, 400)
        self.assertIn("license_number", response.data)
        self.assertIn("license_expiry_date", response.data)

    def test_license_length_is_checked_after_uppercasing(self):
        self.client.force_authenticate(self.driver)
        response = self.client.post(
            reverse("drivers:profile"), self.profile_data | {"license_number": "\u00df" * 40}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("license_number", response.data)

    def test_plate_length_is_checked_after_uppercasing(self):
        self.create_profile()
        self.client.force_authenticate(self.driver)
        response = self.client.post(
            reverse("drivers:vehicle"), self.vehicle_data | {"license_plate": "\u00df" * 20}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("license_plate", response.data)

    def test_vehicle_validation(self):
        self.create_profile()
        self.client.force_authenticate(self.driver)
        for changes in (
            {"license_plate": " "}, {"make": ""}, {"model": ""}, {"color": ""},
            {"seats": 0}, {"seats": 9}, {"year": 1899},
            {"year": timezone.localdate().year + 2},
        ):
            with self.subTest(changes=changes):
                response = self.client.post(reverse("drivers:vehicle"), self.vehicle_data | changes)
                self.assertEqual(response.status_code, 400, response.data)

    def test_cannot_assign_profile_to_another_user(self):
        self.client.force_authenticate(self.driver)
        response = self.client.post(
            reverse("drivers:profile"), self.profile_data | {"user": self.other_driver.pk}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(DriverProfile.objects.count(), 0)

    def test_cannot_assign_vehicle_to_another_driver(self):
        self.create_profile()
        other_profile = self.create_profile(self.other_driver, "DL-OTHER")
        self.client.force_authenticate(self.driver)
        response = self.client.post(
            reverse("drivers:vehicle"), self.vehicle_data | {"driver": other_profile.pk}
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(Vehicle.objects.count(), 0)

    def test_drivers_cannot_read_or_update_others_resources(self):
        other_profile = self.create_profile(self.other_driver)
        Vehicle.objects.create(driver=other_profile, **self.vehicle_data)
        self.client.force_authenticate(self.driver)
        for endpoint in ("drivers:profile", "drivers:vehicle"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.get(reverse(endpoint)).status_code, 404)
                response = self.client.patch(reverse(endpoint), {"color": "Red"})
                self.assertEqual(response.status_code, 404)
        self.assertEqual(Vehicle.objects.get().color, "Silver")

    def test_driver_can_update_own_profile_and_vehicle(self):
        profile = self.create_profile()
        vehicle = Vehicle.objects.create(driver=profile, **self.vehicle_data)
        self.client.force_authenticate(self.driver)
        response = self.client.patch(reverse("drivers:profile"), {"license_number": "dl-updated"})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["license_number"], "DL-UPDATED")
        response = self.client.patch(reverse("drivers:vehicle"), {"color": "Blue"})
        self.assertEqual(response.status_code, 200, response.data)
        vehicle.refresh_from_db()
        self.assertEqual(vehicle.color, "Blue")

    def test_cannot_reassign_existing_profile_or_vehicle(self):
        profile = self.create_profile()
        vehicle = Vehicle.objects.create(driver=profile, **self.vehicle_data)
        other_profile = self.create_profile(self.other_driver, "DL-OTHER")
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.patch(
            reverse("drivers:profile"), {"user": self.other_driver.pk}
        ).status_code, 400)
        self.assertEqual(self.client.patch(
            reverse("drivers:vehicle"), {"driver": other_profile.pk}
        ).status_code, 400)
        profile.refresh_from_db()
        vehicle.refresh_from_db()
        self.assertEqual(profile.user_id, self.driver.pk)
        self.assertEqual(vehicle.driver_id, profile.pk)

    def test_license_and_plate_are_unique_case_insensitively(self):
        profile = self.create_profile(self.other_driver)
        Vehicle.objects.create(driver=profile, **self.vehicle_data)
        self.client.force_authenticate(self.driver)
        response = self.client.post(
            reverse("drivers:profile"), self.profile_data | {"license_number": "dl-123456"}
        )
        self.assertEqual(response.status_code, 400)
        own_profile = self.create_profile(license_number="DL-OWN")
        response = self.client.post(
            reverse("drivers:vehicle"), self.vehicle_data | {"license_plate": "dhaka-1234"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Vehicle.objects.filter(driver=own_profile).exists())

    def test_update_rejects_another_drivers_license_or_plate(self):
        other_profile = self.create_profile(self.other_driver)
        Vehicle.objects.create(driver=other_profile, **self.vehicle_data)
        own_profile = self.create_profile(license_number="DL-OWN")
        Vehicle.objects.create(driver=own_profile, **(self.vehicle_data | {"license_plate": "OWN-1"}))
        self.client.force_authenticate(self.driver)
        self.assertEqual(self.client.patch(
            reverse("drivers:profile"), {"license_number": "dl-123456"}
        ).status_code, 400)
        self.assertEqual(self.client.patch(
            reverse("drivers:vehicle"), {"license_plate": "dhaka-1234"}
        ).status_code, 400)

    def test_model_validation_rejects_rider_profile(self):
        profile = DriverProfile(
            user=self.rider, license_number="RIDER-LICENSE",
            license_expiry_date=timezone.localdate() + timedelta(days=365),
        )
        with self.assertRaises(ValidationError):
            profile.full_clean()
