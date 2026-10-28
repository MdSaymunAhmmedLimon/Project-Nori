from decimal import Decimal

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .models import DriverProfile, PayoutRequest, Vehicle



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


def get_driver_balance(user):
    """Calculate withdrawable balance for driver (earnings + tips - requested/paid payouts)."""
    from payments.models import Payment
    from rides.models import RideTip

    payments = Payment.objects.filter(ride__driver=user, ride__status="COMPLETED").exclude(method__in=["WALLET", "SSL_DEMO"])
    rev = payments.aggregate(revenue=Sum("amount"), fees=Sum("platform_fee"))
    gross = Decimal(str(rev["revenue"] or 0))
    fees = Decimal(str(rev["fees"] or 0))
    net_earnings = gross - fees

    tips_total = Decimal(str(RideTip.objects.filter(ride__driver=user).exclude(ride__payment_method__in=["WALLET", "SSL_DEMO"]).aggregate(total=Sum("amount"))["total"] or 0))

    total_income = net_earnings + tips_total

    # Deduct pending, approved, or paid payouts
    profile = DriverProfile.objects.filter(user=user).first()
    if not profile:
        return Decimal("0.00")

    payouts = PayoutRequest.objects.filter(
        driver=profile, status__in=["PENDING", "APPROVED", "PAID"]
    ).aggregate(total=Sum("amount"))["total"]
    total_withdrawn = Decimal(str(payouts or 0))

    return max(Decimal("0.00"), total_income - total_withdrawn)


@transaction.atomic
def request_payout(user, *, amount, payout_method, account_details):
    """Driver creates a payout request."""
    from rides.services import Conflict
    profile = DriverProfile.objects.select_for_update().filter(user=user).first()
    if not profile:
        raise ValidationError({"detail": "Create your driver profile first."})

    amount = Decimal(str(amount))
    balance = get_driver_balance(user)
    if amount > balance:
        raise Conflict(f"Requested amount ({amount}) exceeds available balance ({balance:.2f}).")

    return PayoutRequest.objects.create(
        driver=profile,
        amount=amount,
        payout_method=payout_method,
        account_details=account_details.strip(),
        status=PayoutRequest.Status.PENDING,
    )


@transaction.atomic
def process_payout(staff_user, payout_id, *, action, admin_note=""):
    """Staff processes a payout request."""
    if not staff_user.is_active or not staff_user.has_perm("drivers.change_payoutrequest"):
        raise PermissionDenied("Only staff can process payout requests.")

    payout = PayoutRequest.objects.select_for_update().filter(pk=payout_id).first()
    if not payout:
        raise NotFound("Payout request not found.")

    action_map = {
        "approve": PayoutRequest.Status.APPROVED,
        "reject": PayoutRequest.Status.REJECTED,
        "pay": PayoutRequest.Status.PAID,
    }
    if action not in action_map:
        raise ValidationError({"action": "Invalid action. Choose approve, reject, or pay."})

    payout.status = action_map[action]
    payout.admin_note = admin_note.strip()
    payout.processed_at = timezone.now()
    payout.save(update_fields=["status", "admin_note", "processed_at"])
    return payout

