from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient

User = get_user_model()


class SecurityQuestionAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="question_user",
            email="question@example.com",
            password="StrongOldPassword123!",
        )
        self.user.security_question = "What was the name of your first pet?"
        self.user.set_security_answer("Rex")
        self.user.save()

    def test_get_security_question(self):
        response = self.client.get(f"/api/auth/password/security-question/?username={self.user.username}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["username"], self.user.username)
        self.assertEqual(response.data["security_question"], "What was the name of your first pet?")

    def test_get_security_question_not_found(self):
        response = self.client.get("/api/auth/password/security-question/?username=unknown")
        self.assertEqual(response.status_code, 404)

    def test_reset_password_with_security_answer(self):
        response = self.client.post("/api/auth/password/reset-with-answer/", {
            "username": self.user.username,
            "security_answer": "rex",  # case-insensitive
            "new_password1": "NewStrongPassword456!",
            "new_password2": "NewStrongPassword456!",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("NewStrongPassword456!"))

    def test_reset_password_with_wrong_security_answer(self):
        response = self.client.post("/api/auth/password/reset-with-answer/", {
            "username": self.user.username,
            "security_answer": "WrongAnswer",
            "new_password1": "NewStrongPassword456!",
            "new_password2": "NewStrongPassword456!",
        })
        self.assertEqual(response.status_code, 400)
        self.assertIn("security_answer", response.data)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("StrongOldPassword123!"))

    def test_registration_with_security_question(self):
        response = self.client.post("/api/auth/register/", {
            "username": "new_rider_seq",
            "email": "new_rider_seq@example.com",
            "password": "PasswordWithLettersAnd123!",
            "role": "RIDER",
            "security_question": "In what city were you born?",
            "security_answer": "Dhaka",
        })
        self.assertEqual(response.status_code, 201)
        new_user = User.objects.get(username="new_rider_seq")
        self.assertEqual(new_user.security_question, "In what city were you born?")
        self.assertTrue(new_user.check_security_answer("dhaka"))
