from django.urls import path

from .views import (
    AvailabilityView, DocumentsView, DriverProfileView, EarningsView,
    LocationView, PayoutListCreateView, PayoutProcessView, VehicleView,
)

app_name = "drivers"
urlpatterns = [
    path("profile/", DriverProfileView.as_view(), name="profile"),
    path("vehicle/", VehicleView.as_view(), name="vehicle"),
    path("availability/", AvailabilityView.as_view(), name="availability"),
    path("location/", LocationView.as_view(), name="location"),
    path("documents/", DocumentsView.as_view(), name="documents"),
    path("earnings/", EarningsView.as_view(), name="earnings"),
    path("payouts/", PayoutListCreateView.as_view(), name="payouts"),
    path("payouts/<int:pk>/process/", PayoutProcessView.as_view(), name="payout-process"),
]
