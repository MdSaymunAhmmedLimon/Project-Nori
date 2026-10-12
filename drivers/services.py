from django.db import transaction
from rest_framework.exceptions import ValidationError

from .models import DriverProfile, Vehicle


@transaction.atomic
def set_availability(user, is_online):
    from rides.models import ASSIGNED_STATUSES, Ride
    from rides.services import Conflict, driver_is_eligible
    profile = DriverProfile.objects.select_for_update().select_related("user").filter(user=user).first()
    if not profile:
        raise ValidationError({"detail": "Create your driver profile first."})
    if is_online and not driver_is_eligible(profile, require_online=False):
        raise Conflict("An approved profile, valid license, vehicle, and recent location are required to go online.")
    if not is_online and Ride.objects.filter(driver=user, status__in=ASSIGNED_STATUSES).exists():
        raise Conflict("Complete or cancel your active trip before going offline.")
    profile.is_online = is_online
    profile.save(update_fields=["is_online", "updated_at"])
    return profile


@transaction.atomic
def update_location(user, latitude, longitude):
    from datetime import timedelta
    from django.utils import timezone
    from notifications.services import publish
    from rides.models import ASSIGNED_STATUSES, Ride, TrackPoint
    profile = DriverProfile.objects.select_for_update().filter(user=user).first()
    if not profile:
        raise ValidationError({"detail": "Create your driver profile first."})
    profile.latitude, profile.longitude, profile.location_updated_at = latitude, longitude, timezone.now()
    profile.save(update_fields=["latitude", "longitude", "location_updated_at", "updated_at"])
    ride = Ride.objects.filter(driver=user, status__in=ASSIGNED_STATUSES).first()
    if ride:
        recent = ride.track_points.filter(recorded_at__gte=timezone.now() - timedelta(seconds=5)).exists()
        if not recent:
            TrackPoint.objects.create(ride=ride, latitude=latitude, longitude=longitude)
            publish(ride.rider_id, "location", ride_id=str(ride.pk))
    return profile


def require_editable_driver(user):
    from rides.models import ASSIGNED_STATUSES, Ride
    from rides.services import Conflict
    if Ride.objects.filter(driver=user, status__in=ASSIGNED_STATUSES).exists():
        raise Conflict("Finish your trip before changing driver or vehicle information.")


@transaction.atomic
def create_driver_profile(*, user, **profile_data):
    if user.role != "DRIVER":
        raise ValidationError({"detail": "Only drivers can create a driver profile."})
    return DriverProfile.objects.create(user=user, **profile_data)


@transaction.atomic
def create_vehicle(*, user, **vehicle_data):
    try:
        profile = DriverProfile.objects.get(user=user)
    except DriverProfile.DoesNotExist as exc:
        raise ValidationError({"detail": "Create your driver profile first."}) from exc
    return Vehicle.objects.create(driver=profile, **vehicle_data)
