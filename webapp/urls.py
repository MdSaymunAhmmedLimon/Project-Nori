from django.contrib.auth.views import LogoutView
from django.urls import path

from . import account_views, views
from .health import health

app_name = "webapp"
urlpatterns = [
    path("", views.home, name="home"),
    path("login/", views.WebLoginView.as_view(), name="login"),
    path("register/", views.register, name="register"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("password/change/", account_views.PasswordChangeView.as_view(), name="password_change"),
    path("password/changed/", account_views.PasswordChangeDoneView.as_view(), name="password_change_done"),
    path("password/reset/", account_views.PasswordResetView.as_view(), name="password_reset"),
    path("password/reset/verify/", account_views.PasswordResetVerifyView.as_view(), name="password_reset_verify"),
    path("password/reset/sent/", account_views.PasswordResetCompleteView.as_view(), name="password_reset_done"),
    path("password/reset/<uidb64>/<token>/", account_views.PasswordResetVerifyView.as_view(), name="password_reset_confirm"),
    path("password/reset/complete/", account_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("app/", views.dashboard, name="dashboard"),
    path("app/rides/<uuid:ride_id>/receipt/", views.receipt, name="receipt"),
    path("operations/", views.operations, name="operations"),
    path("health/", health, name="health"),
]
