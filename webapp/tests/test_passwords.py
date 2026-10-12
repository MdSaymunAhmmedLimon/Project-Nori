import secrets

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework_simplejwt.tokens import RefreshToken


@override_settings(DEBUG=False)
class PasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = secrets.token_urlsafe(24)
        self.user = get_user_model().objects.create_user(
            username="password_rider", email="password@example.com", password=self.password,
        )
        self.user.security_question = "What was the name of your first pet?"
        self.user.set_security_answer("Fluffy")
        self.user.save()
        self.new_password = secrets.token_urlsafe(24)

    def password_data(self, **changes):
        data = {"old_password": self.password, "new_password1": self.new_password, "new_password2": self.new_password}
        data.update(changes)
        return data

    def assert_tokens_revoked(self, refresh):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {refresh.access_token}"}
        self.assertEqual(Client().get("/api/auth/me/", **headers).status_code, 401)
        response = Client().post("/api/auth/refresh/", {"refresh": str(refresh)}, content_type="application/json")
        self.assertEqual(response.status_code, 401)

    def test_change_keeps_current_session_and_revokes_other_sessions_and_tokens(self):
        other = Client()
        other.force_login(self.user)
        self.client.force_login(self.user)
        refresh = RefreshToken.for_user(self.user)
        response = self.client.post(reverse("webapp:password_change"), self.password_data())
        self.assertRedirects(response, reverse("webapp:password_change_done"))
        self.assertEqual(self.client.get("/api/auth/me/").status_code, 200)
        self.assertEqual(other.get("/api/auth/me/").status_code, 401)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))
        self.assert_tokens_revoked(refresh)

    def test_change_requires_current_password_and_valid_matching_passwords(self):
        self.client.force_login(self.user)
        for changes, field in (
            ({"old_password": "wrong"}, "old_password"),
            ({"new_password2": "different"}, "new_password2"),
            ({"new_password1": "123", "new_password2": "123"}, "new_password2"),
        ):
            with self.subTest(field=field):
                response = self.client.post(reverse("webapp:password_change"), self.password_data(**changes))
                self.assertIn(field, response.context["form"].errors)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_password_posts_require_csrf_and_change_requires_login(self):
        client = Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(reverse("webapp:password_change")).status_code, 302)
        self.assertEqual(client.post(reverse("webapp:password_reset"), {"username": self.user.username}).status_code, 403)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("webapp:password_change"), self.password_data()).status_code, 403)

    def test_security_question_lookup_and_verification_resets_password(self):
        # Step 1: Lookup by username
        response = self.client.post(reverse("webapp:password_reset"), {"username": self.user.username})
        self.assertRedirects(response, f"{reverse('webapp:password_reset_verify')}?username={self.user.username}")

        # Step 2: Verification with wrong answer fails
        verify_url = reverse("webapp:password_reset_verify")
        wrong_data = {
            "username": self.user.username,
            "security_answer": "WrongAnswer",
            "new_password1": self.new_password,
            "new_password2": self.new_password,
        }
        res_wrong = self.client.post(verify_url, wrong_data)
        self.assertContains(res_wrong, "Incorrect answer")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

        # Step 3: Verification with correct answer (case-insensitive) succeeds
        correct_data = {
            "username": self.user.username,
            "security_answer": "fluffy",  # lower case test
            "new_password1": self.new_password,
            "new_password2": self.new_password,
        }
        res_correct = self.client.post(verify_url, correct_data)
        self.assertRedirects(res_correct, reverse("webapp:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertFalse(self.user.check_password(self.password))

    def test_security_question_lookup_unknown_user_shows_error(self):
        response = self.client.post(reverse("webapp:password_reset"), {"username": "nonexistent_user"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No account found")

    def test_reset_requests_are_rate_limited(self):
        for _ in range(30):
            self.client.post(reverse("webapp:password_reset"), {"username": "nonexistent_user"})
        self.assertEqual(self.client.post(reverse("webapp:password_reset"), {"username": self.user.username}).status_code, 429)

