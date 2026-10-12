from django.urls import path

from .views import AvailabilityView, DriverProfileView, LocationView, VehicleView

app_name = "drivers"
urlpatterns = [
    path("profile/", DriverProfileView.as_view(), name="profile"),
    path("vehicle/", VehicleView.as_view(), name="vehicle"),
    path("availability/", AvailabilityView.as_view(), name="availability"),
    path("location/", LocationView.as_view(), name="location"),
]
