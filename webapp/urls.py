from django.contrib.auth.views import LogoutView
from django.urls import path

from . import account_views, operations, position_views, views
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
    path("password/reset/complete/", account_views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
    path("app/", views.dashboard, name="dashboard"),
    path("team/", position_views.team_dashboard, name="team-dashboard"),
    path("notifications/", position_views.notification_center, name="notification-center"),
    path("notifications/send/", position_views.announce, name="send-notification"),
    path("positions/", position_views.index, name="positions"),
    path("positions/categories/new/", position_views.create_category, name="position-category-create"),
    path("positions/invite/", position_views.invite, name="position-invite"),
    path("positions/<int:pk>/", position_views.detail, name="position-detail"),
    path("positions/<int:pk>/respond/", position_views.respond, name="position-respond"),
    path("positions/<int:pk>/decide/", position_views.decide, name="position-decide"),
    path("positions/users/<int:pk>/revoke/", position_views.revoke, name="position-revoke"),
    path("share/<str:token>/", views.shared_trip, name="shared-trip"),
    path("app/rides/<uuid:ride_id>/receipt/", views.receipt, name="receipt"),
    path("operations/", views.operations, name="operations"),
    path("operations/rides/<uuid:pk>/cancel/", operations.cancel_ride, name="operations-ride-cancel"),
    path("operations/drivers/<int:pk>/", operations.driver_detail, name="operations-driver"),
    path("operations/drivers/<int:pk>/review/", operations.driver_decision, name="operations-driver-review"),
    path("operations/documents/<int:pk>/review/", operations.document_decision, name="operations-document-review"),
    path("operations/documents/<int:pk>/file/", operations.document_file, name="operations-document-file"),
    path("operations/users/<int:pk>/access/", operations.user_state, name="operations-user-access"),
    path("operations/payouts/<int:pk>/process/", operations.payout_action, name="operations-payout"),
    path("operations/sos/<int:pk>/resolve/", operations.resolve_alert, name="operations-sos"),
    path("operations/lost-items/<int:pk>/update/", operations.update_report, name="operations-lost-item"),
    path("operations/<str:section>/", operations.listing, name="operations-list"),
    path("health/", health, name="health"),
]
