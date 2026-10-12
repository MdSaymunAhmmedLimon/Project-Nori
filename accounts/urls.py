from django.urls import path

from .views import (
    CurrentUserView, LoginView, LogoutView, PasswordChangeView,
    RefreshView, ReferralView, RegistrationView, ResetWithSecurityAnswerView,
    SecurityQuestionView,
)

app_name = "accounts"
urlpatterns = [
    path("register/", RegistrationView.as_view(), name="register"),
    path("login/", LoginView.as_view(), name="login"),
    path("refresh/", RefreshView.as_view(), name="refresh"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("me/", CurrentUserView.as_view(), name="me"),
    path("password/change/", PasswordChangeView.as_view(), name="password_change"),
    path("password/security-question/", SecurityQuestionView.as_view(), name="security_question"),
    path("password/reset-with-answer/", ResetWithSecurityAnswerView.as_view(), name="reset_with_answer"),
    path("referral/", ReferralView.as_view(), name="referral"),
]

