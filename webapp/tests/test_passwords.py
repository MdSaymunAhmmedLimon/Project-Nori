import re
import secrets
from unittest.mock import patch
from urllib.parse import urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core import mail
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from rest_framework_simplejwt.tokens import RefreshToken


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", PUBLIC_BASE_URL="https://nori.example", DEBUG=False)
class PasswordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = secrets.token_urlsafe(24)
        self.user = get_user_model().objects.create_user(
            username="password_rider", email="password@example.com", password=self.password,
        )
        self.new_password = secrets.token_urlsafe(24)

    def password_data(self, **changes):
        data = {"old_password": self.password, "new_password1": self.new_password, "new_password2": self.new_password}
        data.update(changes)
        return data

    def reset_link(self):
        return reverse("webapp:password_reset_confirm", kwargs={
            "uidb64": urlsafe_base64_encode(force_bytes(self.user.pk)),
            "token": default_token_generator.make_token(self.user),
        })

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
        self.assertEqual(client.post(reverse("webapp:password_reset"), {"email": self.user.email}).status_code, 403)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse("webapp:password_change"), self.password_data()).status_code, 403)

    def test_reset_email_uses_public_origin_and_unknown_email_has_same_response(self):
        responses = []
        for email in (self.user.email.upper(), "absent@example.com"):
            responses.append(self.client.post(reverse("webapp:password_reset"), {"email": email}, follow=True))
        self.assertEqual(responses[0].content, responses[1].content)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.user.email])
        self.assertIn("https://nori.example/password/reset/", mail.outbox[0].body)
        self.assertNotIn("testserver", mail.outbox[0].body)

    @override_settings(DEBUG=True)
    def test_local_reset_link_preserves_custom_port(self):
        self.client.post(reverse("webapp:password_reset"), {"email": self.user.email}, HTTP_HOST="localhost:8123")
        self.assertIn("http://localhost:8123/password/reset/", mail.outbox[0].body)

    def test_inactive_and_unusable_password_accounts_do_not_receive_email(self):
        self.user.is_active = False
        self.user.save()
        self.client.post(reverse("webapp:password_reset"), {"email": self.user.email})
        self.user.is_active = True
        self.user.set_unusable_password()
        self.user.save()
        self.client.post(reverse("webapp:password_reset"), {"email": self.user.email})
        self.assertEqual(len(mail.outbox), 0)

    def test_reset_link_is_one_use_and_revokes_existing_credentials(self):
        refresh = RefreshToken.for_user(self.user)
        other = Client()
        other.force_login(self.user)
        self.client.post(reverse("webapp:password_reset"), {"email": self.user.email})
        link = urlsplit(re.search(r"https://\S+", mail.outbox[0].body)[0]).path
        response = self.client.get(link)
        form_url = response.url
        self.assertIn("set-password", form_url)
        response = self.client.post(form_url, {key: value for key, value in self.password_data().items() if key != "old_password"})
        self.assertRedirects(response, reverse("webapp:password_reset_complete"))
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.new_password))
        self.assertFalse(self.user.check_password(self.password))
        self.assertEqual(other.get("/api/auth/me/").status_code, 401)
        self.assert_tokens_revoked(refresh)
        self.assertContains(self.client.get(link), "expired or has already been used")
        self.assertTrue(self.client.login(username=self.user.username, password=self.new_password))

    def test_invalid_and_expired_reset_links_cannot_change_password(self):
        self.assertContains(self.client.get("/password/reset/invalid/invalid/"), "Request a new link")
        link = self.reset_link()
        timestamp = default_token_generator._num_seconds(default_token_generator._now())
        with patch.object(default_token_generator, "_num_seconds", return_value=timestamp + 3601):
            self.assertContains(self.client.get(link), "Request a new link")
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(self.password))

    def test_reset_requests_are_rate_limited(self):
        for _ in range(30):
            self.client.post(reverse("webapp:password_reset"), {"email": "absent@example.com"})
        self.assertEqual(self.client.post(reverse("webapp:password_reset"), {"email": self.user.email}).status_code, 429)
        self.assertEqual(len(mail.outbox), 0)
