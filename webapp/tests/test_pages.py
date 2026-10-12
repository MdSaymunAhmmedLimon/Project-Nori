import secrets

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase


class WebTests(TestCase):
    def setUp(self):
        cache.clear()
        self.password = secrets.token_urlsafe(24)
        self.user = get_user_model().objects.create_user(username="browser_user", email="browser@example.com", password=self.password)

    def test_public_pages_render_and_dashboard_requires_login(self):
        for url in ("/", "/login/", "/register/"):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)
        self.assertRedirects(self.client.get("/app/"), "/login/?next=/app/")

    def test_browser_registration_logs_in_and_preserves_role(self):
        response = self.client.post("/register/", {
            "username":"new_driver", "email":"newdriver@example.com", "role":"DRIVER",
            "password1":self.password, "password2":self.password,
        })
        self.assertRedirects(response, "/app/")
        user = get_user_model().objects.get(username="new_driver")
        self.assertEqual(user.role, "DRIVER")
        self.assertTrue(user.check_password(self.password))
        self.assertContains(self.client.get("/app/"), "Your availability")

    def test_duplicate_registration_and_password_mismatch_show_errors(self):
        response = self.client.post("/register/", {
            "username":"browser_user", "email":"browser@example.com", "role":"RIDER",
            "password1":self.password, "password2":self.password,
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].errors)
        response = self.client.post("/register/", {
            "username":"new", "email":"new@example.com", "role":"RIDER",
            "password1":self.password, "password2":secrets.token_urlsafe(24),
        })
        self.assertIn("password2", response.context["form"].errors)

    def test_session_login_csrf_protected_api_and_logout(self):
        client = Client(enforce_csrf_checks=True)
        client.get("/login/")
        token = client.cookies["csrftoken"].value
        credentials = {"username":self.user.username, "password":self.password}
        self.assertEqual(client.post("/login/", credentials).status_code, 403)
        self.assertEqual(client.post("/login/", credentials, HTTP_X_CSRFTOKEN=token).status_code, 302)
        token = client.cookies["csrftoken"].value
        self.assertEqual(client.get("/api/auth/me/").status_code, 200)
        self.assertEqual(client.patch("/api/auth/me/", {"first_name":"Browser"}, content_type="application/json").status_code, 403)
        self.assertEqual(client.patch("/api/auth/me/", {"first_name":"Browser"}, content_type="application/json", HTTP_X_CSRFTOKEN=token).status_code, 200)
        self.assertEqual(client.get("/logout/").status_code, 405)
        self.assertEqual(client.post("/logout/").status_code, 403)
        self.assertEqual(client.post("/logout/", HTTP_X_CSRFTOKEN=token).status_code, 302)
        self.assertEqual(client.get("/api/auth/me/").status_code, 401)

    def test_operations_requires_staff(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get("/operations/").status_code, 302)
        self.user.is_staff = True
        self.user.save()
        self.assertEqual(self.client.get("/operations/").status_code, 200)

    def test_browser_login_has_rate_limit(self):
        for _ in range(30):
            self.client.post("/login/", {})
        self.assertEqual(self.client.post("/login/", {}).status_code, 429)

    def test_login_does_not_redirect_to_external_site(self):
        response = self.client.post("/login/", {"username":self.user.username,"password":self.password,"next":"https://example.org/"})
        self.assertRedirects(response, "/app/")
