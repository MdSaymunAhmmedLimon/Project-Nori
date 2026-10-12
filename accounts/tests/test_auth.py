import secrets
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

User = get_user_model()


class AuthenticationTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = secrets.token_urlsafe(24)
        cls.user = User.objects.create_user(
            username="existing_rider", email="existing@example.com", password=cls.password
        )

    def setUp(self):
        cache.clear()

    def registration_data(self, **changes):
        data = {
            "username": "new_rider",
            "email": "new@example.com",
            "password": secrets.token_urlsafe(24),
            "role": "RIDER",
        }
        data.update(changes)
        return data

    def login(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": self.user.username, "password": self.password},
        )
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_register_both_roles_and_hash_password(self):
        for role in User.Role.values:
            with self.subTest(role=role):
                data = self.registration_data(
                    username=role.lower(), email=f"{role.lower()}@example.com", role=role
                )
                response = self.client.post(reverse("accounts:register"), data)
                self.assertEqual(response.status_code, 201, response.data)
                self.assertNotIn("password", response.data)
                user = User.objects.get(username=data["username"])
                self.assertTrue(user.check_password(data["password"]))
                self.assertNotEqual(user.password, data["password"])
                self.assertEqual(user.role, role)
                self.assertFalse(user.is_staff)
                self.assertFalse(user.is_superuser)

    def test_default_role_is_rider(self):
        data = self.registration_data()
        del data["role"]
        response = self.client.post(reverse("accounts:register"), data)
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["role"], "RIDER")

    def test_registration_requires_username_email_and_password(self):
        for field in ("username", "email", "password"):
            with self.subTest(field=field):
                data = self.registration_data()
                del data[field]
                response = self.client.post(reverse("accounts:register"), data)
                self.assertEqual(response.status_code, 400)
                self.assertIn(field, response.data)

    def test_duplicate_username_is_rejected(self):
        response = self.client.post(
            reverse("accounts:register"), self.registration_data(username=self.user.username)
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.data)

    def test_username_length_is_checked_after_unicode_normalization(self):
        response = self.client.post(
            reverse("accounts:register"), self.registration_data(username="\ufb03" * 60)
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("username", response.data)

    def test_duplicate_email_is_case_insensitive(self):
        response = self.client.post(
            reverse("accounts:register"), self.registration_data(email="EXISTING@EXAMPLE.COM")
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("email", response.data)

    def test_email_is_normalized(self):
        response = self.client.post(
            reverse("accounts:register"), self.registration_data(email="New@Example.COM")
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["email"], "new@example.com")

    def test_invalid_registration_values_are_rejected(self):
        cases = [
            ("role", "ADMIN"),
            ("email", "not-an-email"),
            ("phone_number", "01712345678"),
            ("password", "123"),
            ("password", "password"),
            ("password", "new_rider"),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                response = self.client.post(
                    reverse("accounts:register"), self.registration_data(**{field: value})
                )
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn(field, response.data)

    def test_registration_rejects_privilege_fields(self):
        for field in ("is_staff", "is_superuser", "is_active", "groups"):
            with self.subTest(field=field):
                response = self.client.post(
                    reverse("accounts:register"), self.registration_data(**{field: True})
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(User.objects.count(), 1)

    def test_login_and_current_user_with_bearer_token(self):
        tokens = self.login()
        self.assertIn("refresh", tokens)
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.get(reverse("accounts:me"))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["id"], self.user.pk)
        self.assertEqual(response.data["role"], "RIDER")
        self.assertNotIn("password", response.data)

    def test_login_rejects_wrong_password(self):
        response = self.client.post(
            reverse("accounts:login"),
            {"username": self.user.username, "password": secrets.token_urlsafe(24)},
        )
        self.assertEqual(response.status_code, 401)

    def test_inactive_user_cannot_login(self):
        self.user.is_active = False
        self.user.save()
        response = self.client.post(
            reverse("accounts:login"),
            {"username": self.user.username, "password": self.password},
        )
        self.assertEqual(response.status_code, 401)

    def test_inactive_user_cannot_use_existing_access_or_refresh(self):
        tokens = self.login()
        self.user.is_active = False
        self.user.save()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(self.client.get(reverse("accounts:me")).status_code, 401)
        response = self.client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]})
        self.assertEqual(response.status_code, 401)

    def test_deleted_user_cannot_refresh(self):
        tokens = self.login()
        self.user.delete()
        response = self.client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]})
        self.assertEqual(response.status_code, 401)

    def test_anonymous_current_user_is_rejected(self):
        self.assertEqual(self.client.get(reverse("accounts:me")).status_code, 401)

    def test_invalid_or_expired_access_is_rejected(self):
        expired_access = RefreshToken.for_user(self.user).access_token
        expired_access.set_exp(lifetime=timedelta(seconds=-1))
        for token in ("invalid-token", str(expired_access)):
            with self.subTest(token_type="expired" if token != "invalid-token" else "invalid"):
                self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
                self.assertEqual(self.client.get(reverse("accounts:me")).status_code, 401)

    def test_current_user_can_update_profile(self):
        tokens = self.login()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        response = self.client.patch(
            reverse("accounts:me"),
            {"first_name": "Rider", "last_name": "Example", "phone_number": "+8801712345678"},
        )
        self.assertEqual(response.status_code, 200, response.data)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Rider")
        self.assertEqual(self.user.phone_number, "+8801712345678")

    def test_current_user_cannot_change_role_identity_or_privileges(self):
        self.client.force_authenticate(self.user)
        for field, value in (
            ("role", "DRIVER"), ("username", "changed"), ("email", "changed@example.com"),
            ("is_staff", True), ("is_superuser", True), ("password", secrets.token_urlsafe(24)),
        ):
            with self.subTest(field=field):
                response = self.client.patch(reverse("accounts:me"), {field: value})
                self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertEqual(self.user.role, "RIDER")
        self.assertFalse(self.user.is_staff)

    def test_refresh_rotates_and_revokes_old_refresh(self):
        tokens = self.login()
        response = self.client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotEqual(response.data["refresh"], tokens["refresh"])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(self.client.get(reverse("accounts:me")).status_code, 200)
        reused = self.client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]})
        self.assertEqual(reused.status_code, 401)
        refreshed = self.client.post(
            reverse("accounts:refresh"), {"refresh": response.data["refresh"]}
        )
        self.assertEqual(refreshed.status_code, 200)

    def test_logout_revokes_refresh_without_requiring_access_token(self):
        tokens = self.login()
        response = self.client.post(reverse("accounts:logout"), {"refresh": tokens["refresh"]})
        self.assertEqual(response.status_code, 200, response.data)
        response = self.client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]})
        self.assertEqual(response.status_code, 401)
        # Access tokens remain valid for their documented five-minute lifetime.
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        self.assertEqual(self.client.get(reverse("accounts:me")).status_code, 200)

    def test_logout_does_not_revoke_other_sessions(self):
        first, second = self.login(), self.login()
        self.client.post(reverse("accounts:logout"), {"refresh": first["refresh"]})
        response = self.client.post(reverse("accounts:refresh"), {"refresh": second["refresh"]})
        self.assertEqual(response.status_code, 200)

    def test_refresh_and_logout_validate_tokens(self):
        tokens = self.login()
        for endpoint in ("accounts:refresh", "accounts:logout"):
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.client.post(reverse(endpoint), {}).status_code, 400)
                for token in ("invalid-token", tokens["access"]):
                    response = self.client.post(reverse(endpoint), {"refresh": token})
                    self.assertEqual(response.status_code, 401, response.data)

    def test_login_is_throttled(self):
        for _ in range(30):
            self.client.post(reverse("accounts:login"), {})
        response = self.client.post(reverse("accounts:login"), {})
        self.assertEqual(response.status_code, 429)

    def test_password_change_revokes_old_access_and_refresh(self):
        tokens = self.login()
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
        new_password = secrets.token_urlsafe(24)
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": self.password, "new_password1": new_password, "new_password2": new_password,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn("sessionid", response.cookies)
        self.assertEqual(self.client.get(reverse("accounts:me")).status_code, 401)
        self.assertEqual(self.client.post(reverse("accounts:refresh"), {"refresh": tokens["refresh"]}).status_code, 401)
        self.client.credentials()
        self.assertEqual(self.client.post(reverse("accounts:login"), {"username": self.user.username, "password": new_password}).status_code, 200)

    def test_password_change_validates_current_password_and_new_password(self):
        self.client.force_authenticate(self.user)
        new_password = secrets.token_urlsafe(24)
        valid = {"old_password": self.password, "new_password1": new_password, "new_password2": new_password}
        for changes in (
            {"old_password": "incorrect"}, {"new_password2": "different"},
            {"new_password1": "123", "new_password2": "123"}, {"role": "DRIVER"},
        ):
            with self.subTest(changes=list(changes)):
                response = self.client.post(reverse("accounts:password_change"), {**valid, **changes})
                self.assertEqual(response.status_code, 400, response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_password_change_requires_login(self):
        self.assertEqual(self.client.post(reverse("accounts:password_change"), {}).status_code, 401)

    def test_password_change_preserves_current_browser_session(self):
        self.client.force_login(self.user)
        new_password = secrets.token_urlsafe(24)
        response = self.client.post(reverse("accounts:password_change"), {
            "old_password": self.password, "new_password1": new_password, "new_password2": new_password,
        })
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(self.client.get(reverse("accounts:me")).status_code, 200)

    def test_password_change_is_throttled(self):
        self.client.force_authenticate(self.user)
        for _ in range(30):
            self.client.post(reverse("accounts:password_change"), {})
        self.assertEqual(self.client.post(reverse("accounts:password_change"), {}).status_code, 429)


class UserModelAndAdminTests(TestCase):
    def test_create_superuser(self):
        user = User.objects.create_superuser(
            username="admin", email="ADMIN@EXAMPLE.COM", password=secrets.token_urlsafe(24)
        )
        self.assertTrue(user.is_staff)
        self.assertTrue(user.is_superuser)
        self.assertEqual(user.email, "admin@example.com")

    def test_email_is_required_by_manager(self):
        with self.assertRaises(ValueError):
            User.objects.create_user(username="missing_email", email="")

    def test_database_enforces_email_uniqueness_and_roles(self):
        user = User.objects.create_user(username="first", email="first@example.com")
        for changes in ({"role": "ADMIN"}, {"email": ""}):
            with self.subTest(changes=changes), self.assertRaises(IntegrityError), transaction.atomic():
                User.objects.filter(pk=user.pk).update(**changes)
        other = User.objects.create_user(username="second", email="second@example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.filter(pk=other.pk).update(email="FIRST@EXAMPLE.COM")

    def test_admin_pages_and_custom_user_creation(self):
        admin_user = User.objects.create_superuser(
            username="admin", email="admin@example.com", password=secrets.token_urlsafe(24)
        )
        self.client.force_login(admin_user)
        for url in (
            reverse("admin:accounts_user_add"),
            reverse("admin:accounts_user_change", args=[admin_user.pk]),
            reverse("admin:drivers_driverprofile_add"),
            reverse("admin:drivers_vehicle_add"),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        password = secrets.token_urlsafe(24)
        response = self.client.post(
            reverse("admin:accounts_user_add"),
            {
                "username": "admin_created_driver", "email": "driver@example.com",
                "role": "DRIVER", "usable_password": "true",
                "password1": password, "password2": password,
            },
        )
        self.assertEqual(response.status_code, 302)
        user = User.objects.get(username="admin_created_driver")
        self.assertTrue(user.check_password(password))
        self.assertEqual(user.role, "DRIVER")
