import math
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from drivers.models import DriverProfile
from notifications.services import notify, publish

from .geo import distance_km, money
from .models import ACTIVE_STATUSES, ASSIGNED_STATUSES, Rating, Ride, RideEvent, RideOffer, RideQuote


class Conflict(APIException):
    status_code = 409
    default_detail = "This action is no longer available. Refresh and try again."


def require_role(user, role):
    if user.role != role:
        raise PermissionDenied(f"This action requires a {role.lower()} account.")


def create_quote(user, data):
    require_role(user, "RIDER")
    direct = distance_km(data["pickup_latitude"], data["pickup_longitude"], data["destination_latitude"], data["destination_longitude"])
    if direct < 0.1 or direct > 200:
        raise ValidationError({"detail": "Choose locations between 100 metres and 200 km apart."})
    estimated = money(Decimal(str(direct)) * Decimal(settings.FARE_ROAD_FACTOR))
    fare = money(max(Decimal(settings.FARE_MINIMUM), Decimal(settings.FARE_BASE) + estimated * Decimal(settings.FARE_PER_KM)))
    return RideQuote.objects.create(
        rider=user, **data, distance_km=estimated, estimated_minutes=max(1, math.ceil(float(estimated) / 25 * 60)),
        fare=fare, platform_fee=money(fare * Decimal(settings.PLATFORM_FEE_PERCENT) / 100),
        currency=settings.RIDE_CURRENCY, expires_at=timezone.now() + timedelta(minutes=10),
    )


def record_status(ride, status, message=None):
    ride.status = status
    ride.save()
    RideEvent.objects.create(ride=ride, status=status)
    for user_id in {ride.rider_id, ride.driver_id} - {None}:
        notify(user_id, message or ride.get_status_display(), ride.id)


@transaction.atomic
def expire_searches(user=None):
    pending = Ride.objects.select_for_update().filter(
        status__in=[Ride.Status.REQUESTED, Ride.Status.SEARCHING],
        created_at__lt=timezone.now() - timedelta(minutes=settings.SEARCH_TIMEOUT_MINUTES),
    )
    if user is not None:
        pending = pending.filter(rider=user)
    count = 0
    for ride in pending[:100]:
        ride.cancelled_at = timezone.now()
        ride.cancellation_reason = "No driver accepted within the search window. Please request again."
        record_status(ride, Ride.Status.CANCELLED, ride.cancellation_reason)
        count += 1
    return count


@transaction.atomic
def request_ride(user, *, quote_id, request_id, payment_method):
    require_role(user, "RIDER")
    get_user_model().objects.select_for_update().get(pk=user.pk)
    existing = Ride.objects.filter(rider=user, request_id=request_id).first()
    if existing:
        if existing.quote_id != quote_id or existing.payment_method != payment_method:
            raise Conflict("This request ID has already been used for different ride details.")
        return existing, False
    expire_searches(user)
    if Ride.objects.filter(rider=user, status__in=ACTIVE_STATUSES).exists():
        raise Conflict("You already have an active ride.")
    if payment_method == "CARD" and not settings.CARD_PAYMENTS_ENABLED:
        raise ValidationError({"payment_method": "Card payments are not configured. Choose cash."})
    quote = RideQuote.objects.select_for_update().filter(pk=quote_id, rider=user).first()
    if not quote:
        raise NotFound("Fare quote not found.")
    if quote.expires_at <= timezone.now():
        raise ValidationError({"quote_id": "This quote has expired. Get a new estimate."})
    if Ride.objects.filter(quote=quote).exists():
        raise Conflict("This quote has already been used. Get a new estimate.")
    ride = Ride.objects.create(rider=user, quote=quote, request_id=request_id, payment_method=payment_method)
    RideEvent.objects.create(ride=ride, status=Ride.Status.REQUESTED)
    record_status(ride, Ride.Status.SEARCHING)
    dispatch_ride(ride.pk)
    return ride, True


def driver_is_eligible(profile, *, require_online=True):
    now = timezone.now()
    return (
        profile.user.is_active and profile.user.role == "DRIVER"
        and profile.verification_status == DriverProfile.Verification.APPROVED
        and profile.license_expiry_date >= timezone.localdate()
        and (profile.is_online or not require_online)
        and profile.latitude is not None and profile.longitude is not None
        and profile.location_updated_at is not None
        and profile.location_updated_at >= now - timedelta(seconds=settings.LOCATION_MAX_AGE_SECONDS)
        and hasattr(profile, "vehicle")
    )


@transaction.atomic
def dispatch_ride(ride_id):
    ride = Ride.objects.select_for_update().select_related("quote").get(pk=ride_id)
    if ride.status != Ride.Status.SEARCHING:
        return
    if ride.created_at < timezone.now() - timedelta(minutes=settings.SEARCH_TIMEOUT_MINUTES):
        return
    busy = Ride.objects.filter(status__in=ASSIGNED_STATUSES).values_list("driver_id", flat=True)
    profiles = DriverProfile.objects.filter(
        is_online=True, verification_status="APPROVED", user__is_active=True, user__role="DRIVER",
        license_expiry_date__gte=timezone.localdate(), vehicle__isnull=False,
        location_updated_at__gte=timezone.now() - timedelta(seconds=settings.LOCATION_MAX_AGE_SECONDS),
        latitude__isnull=False, longitude__isnull=False,
    ).exclude(user_id__in=busy).select_related("user", "vehicle")
    nearby = []
    existing_offers = ride.offers.all()
    available_slots = max(0, 5 - existing_offers.filter(rejected_at__isnull=True, expires_at__gt=timezone.now()).count())
    offered_ids = set(existing_offers.values_list("driver_id", flat=True))
    for profile in profiles[:500]:
        if profile.user_id in offered_ids:
            continue
        distance = distance_km(ride.quote.pickup_latitude, ride.quote.pickup_longitude, profile.latitude, profile.longitude)
        if distance <= settings.MATCH_RADIUS_KM:
            nearby.append((distance, profile.user_id))
    for distance, driver_id in sorted(nearby)[:available_slots]:
        offer, created = RideOffer.objects.get_or_create(
            ride=ride, driver_id=driver_id,
            defaults={"distance_km": money(distance), "expires_at": timezone.now() + timedelta(seconds=settings.OFFER_TIMEOUT_SECONDS)},
        )
        if created:
            notify(driver_id, "A nearby rider is looking for a ride.", ride.id)


def dispatch_waiting():
    expire_searches()
    for ride_id in Ride.objects.filter(status=Ride.Status.SEARCHING).order_by("created_at").values_list("pk", flat=True)[:50]:
        dispatch_ride(ride_id)


@transaction.atomic
def accept_offer(user, offer_id):
    require_role(user, "DRIVER")
    profile = DriverProfile.objects.select_for_update().select_related("user").filter(user=user).first()
    if not profile or not driver_is_eligible(profile):
        raise Conflict("Go online with an approved profile and a recent location first.")
    offer = RideOffer.objects.filter(pk=offer_id, driver=user).first()
    if not offer:
        raise NotFound()
    ride = Ride.objects.select_for_update().get(pk=offer.ride_id)
    if ride.driver_id == user.pk and ride.status in ASSIGNED_STATUSES:
        return ride
    if (offer.rejected_at or offer.expires_at <= timezone.now() or ride.status != Ride.Status.SEARCHING
            or ride.created_at < timezone.now() - timedelta(minutes=settings.SEARCH_TIMEOUT_MINUTES)):
        raise Conflict("This ride offer is no longer available.")
    if Ride.objects.filter(driver=user, status__in=ACTIVE_STATUSES).exists():
        raise Conflict("Finish your current trip before accepting another.")
    ride.driver = user
    ride.accepted_at = timezone.now()
    record_status(ride, Ride.Status.ACCEPTED)
    for driver_id in ride.offers.exclude(driver=user).values_list("driver_id", flat=True):
        publish(driver_id, "offers_changed")
    return ride


@transaction.atomic
def reject_offer(user, offer_id):
    offer = RideOffer.objects.select_for_update().filter(pk=offer_id, driver=user).first()
    if not offer:
        raise NotFound()
    if not offer.rejected_at:
        offer.rejected_at = timezone.now()
        offer.save(update_fields=["rejected_at"])


@transaction.atomic
def transition_ride(user, ride_id, action, reason=""):
    if user.role == "DRIVER":
        if not DriverProfile.objects.select_for_update().filter(user=user).first():
            raise NotFound()
    ride = Ride.objects.select_for_update().select_related("quote").filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound()
    if action == "cancel":
        if ride.status == Ride.Status.CANCELLED and ride.cancelled_by_id == user.pk:
            return ride
        if ride.status not in ACTIVE_STATUSES or ride.status == Ride.Status.IN_PROGRESS:
            raise Conflict("This ride can no longer be cancelled.")
        ride.cancelled_at, ride.cancelled_by, ride.cancellation_reason = timezone.now(), user, reason.strip()
        record_status(ride, Ride.Status.CANCELLED)
        for driver_id in ride.offers.values_list("driver_id", flat=True):
            publish(driver_id, "offers_changed")
        return ride
    if ride.driver_id != user.pk:
        raise PermissionDenied("Only the assigned driver can advance a trip.")
    transitions = {
        "arriving": (Ride.Status.ACCEPTED, Ride.Status.DRIVER_ARRIVING, None),
        "arrived": (Ride.Status.DRIVER_ARRIVING, Ride.Status.DRIVER_ARRIVED, "arrived_at"),
        "start": (Ride.Status.DRIVER_ARRIVED, Ride.Status.IN_PROGRESS, "started_at"),
        "complete": (Ride.Status.IN_PROGRESS, Ride.Status.COMPLETED, "completed_at"),
    }
    if action not in transitions:
        raise ValidationError({"action": "Unknown trip action."})
    previous, target, timestamp = transitions[action]
    if ride.status == target:
        return ride
    if ride.status != previous:
        raise Conflict(f"Cannot {action} a ride while it is {ride.get_status_display().lower()}.")
    if timestamp:
        setattr(ride, timestamp, timezone.now())
    record_status(ride, target)
    if target == Ride.Status.COMPLETED:
        from payments.models import Payment
        Payment.objects.get_or_create(ride=ride, defaults={
            "amount": ride.quote.fare, "platform_fee": ride.quote.platform_fee,
            "currency": ride.quote.currency, "method": ride.payment_method,
        })
    return ride


@transaction.atomic
def rate_ride(user, ride_id, *, score, comment=""):
    ride = Ride.objects.select_for_update().filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound()
    if ride.status != Ride.Status.COMPLETED:
        raise Conflict("Ratings are available after a completed ride.")
    if Rating.objects.filter(ride=ride, author=user).exists():
        raise Conflict("You have already rated this ride.")
    recipient_id = ride.driver_id if user.pk == ride.rider_id else ride.rider_id
    return Rating.objects.create(ride=ride, author=user, recipient_id=recipient_id, score=score, comment=comment)
