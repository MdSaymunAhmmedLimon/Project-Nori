import math
import random
import secrets
import uuid
from datetime import timedelta
from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from drivers.models import DriverProfile
from notifications.services import notify, publish

from .geo import distance_km, money
from .models import (
    ACTIVE_STATUSES, ASSIGNED_STATUSES, Badge, CarpoolPassenger, CarpoolRide,
    FavouriteDriver, LostItem, LoyaltyPoints, PriceLock, PromoCode, PromoUsage,
    Rating, Ride, RideEvent, RideMessage, RideOffer, RideQuote, RideStop, RideTip,
    SavedPlace, ScheduledRide, SOSAlert, SurgePricingRule, TripShareToken,
    UserBadge,
)


class Conflict(APIException):
    status_code = 409
    default_detail = "This action is no longer available. Refresh and try again."


def generate_verification_pin():
    """Return a random 4-digit PIN for ride verification."""
    return f"{random.randint(1000, 9999)}"


@transaction.atomic
def create_trip_share(user, ride_id):
    """Create a short-lived, revocable read-only link for an active trip."""
    ride = Ride.objects.select_for_update().filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound()
    if ride.status not in ACTIVE_STATUSES:
        raise Conflict("Trip sharing is available while a ride is active.")
    return TripShareToken.objects.create(
        ride=ride, created_by=user, token=secrets.token_urlsafe(32),
        expires_at=timezone.now() + timedelta(hours=6),
    )


def revoke_trip_share(user, share_id):
    share = TripShareToken.objects.filter(pk=share_id, created_by=user).first()
    if not share:
        raise NotFound()
    share.delete()


def calculate_carbon_emissions(distance_km):
    """Return estimated kg CO2 emissions for a car trip (~0.12 kg/km)."""
    return (Decimal(str(distance_km)) * Decimal("0.120")).quantize(Decimal("0.001"))


def require_role(user, role):
    if user.role != role:
        raise PermissionDenied(f"This action requires a {role.lower()} account.")


def create_quote(user, data):
    require_role(user, "RIDER")
    stops = data.pop("stops", [])
    vehicle_class = data.pop("vehicle_class", "STANDARD")

    coords = [(data["pickup_latitude"], data["pickup_longitude"])]
    for s in stops:
        coords.append((s["latitude"], s["longitude"]))
    coords.append((data["destination_latitude"], data["destination_longitude"]))

    direct = sum(
        distance_km(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
        for i in range(len(coords) - 1)
    )
    if direct < 0.1 or direct > 300:
        raise ValidationError({"detail": "Choose locations between 100 metres and 300 km apart."})

    surge = get_active_surge_multiplier()
    class_multiplier = getattr(settings, "VEHICLE_CLASS_MULTIPLIERS", {}).get(vehicle_class, Decimal("1.00"))

    estimated = money(Decimal(str(direct)) * Decimal(settings.FARE_ROAD_FACTOR))
    base_fare = max(Decimal(settings.FARE_MINIMUM), Decimal(settings.FARE_BASE) + estimated * Decimal(settings.FARE_PER_KM))
    fare = money(base_fare * surge * class_multiplier)

    quote = RideQuote.objects.create(
        rider=user, **data, distance_km=estimated, estimated_minutes=max(1, math.ceil(float(estimated) / 25 * 60)),
        fare=fare, platform_fee=money(fare * Decimal(settings.PLATFORM_FEE_PERCENT) / 100),
        currency=settings.RIDE_CURRENCY, expires_at=timezone.now() + timedelta(minutes=10),
        surge_multiplier=surge, vehicle_class=vehicle_class,
    )
    for idx, s in enumerate(stops, start=1):
        RideStop.objects.create(quote=quote, stop_order=idx, **s)
    return quote



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
def request_ride(user, *, quote_id, request_id, payment_method, promo_code="", ride_preferences=None):
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

    pin = generate_verification_pin()
    carbon = calculate_carbon_emissions(quote.distance_km)
    prefs = ride_preferences if isinstance(ride_preferences, dict) else {}

    ride = Ride.objects.create(
        rider=user,
        quote=quote,
        request_id=request_id,
        payment_method=payment_method,
        verification_pin=pin,
        carbon_emissions_kg=carbon,
        ride_preferences=prefs,
    )
    # Apply promo code if provided
    if promo_code:
        promo, discounted_fare, discount_amount = validate_promo(user, promo_code, quote.fare)
        apply_promo_to_ride(ride, promo, discount_amount)
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
    vclass = getattr(ride.quote, "vehicle_class", "STANDARD")
    profiles = DriverProfile.objects.filter(
        is_online=True, verification_status="APPROVED", user__is_active=True, user__role="DRIVER",
        license_expiry_date__gte=timezone.localdate(), vehicle__isnull=False,
        location_updated_at__gte=timezone.now() - timedelta(seconds=settings.LOCATION_MAX_AGE_SECONDS),
        latitude__isnull=False, longitude__isnull=False,
        vehicle__vehicle_class=vclass,
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

        fee = Decimal("0.00")
        if user.pk == ride.rider_id and ride.driver_id is not None:
            grace_seconds = getattr(settings, "CANCELLATION_GRACE_SECONDS", 120)
            passed_grace = ride.accepted_at and (timezone.now() - ride.accepted_at).total_seconds() > grace_seconds
            if ride.status == Ride.Status.DRIVER_ARRIVED or passed_grace:
                fee = Decimal(str(getattr(settings, "RIDE_CANCELLATION_FEE", "50.00")))
                ride.cancellation_fee = fee
                from payments.models import Payment
                Payment.objects.get_or_create(ride=ride, defaults={
                    "amount": fee, "platform_fee": Decimal("0.00"),
                    "currency": ride.quote.currency, "method": ride.payment_method,
                })

        ride.cancelled_at, ride.cancelled_by = timezone.now(), user
        cancel_msg = reason.strip()
        if fee > 0:
            cancel_msg = (cancel_msg + f" (Cancellation fee of {fee} {ride.quote.currency} applied)").strip()
        ride.cancellation_reason = cancel_msg
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
        # Check referral reward on rider's first completed ride
        from accounts.models import Referral
        if Ride.objects.filter(rider=ride.rider, status=Ride.Status.COMPLETED).count() == 1:
            ref = Referral.objects.filter(referee=ride.rider, status=Referral.Status.PENDING).first()
            if ref:
                ref.status = Referral.Status.COMPLETED
                ref.completed_at = timezone.now()
                ref.save(update_fields=["status", "completed_at"])
                ref_promo_code = f"REF-{str(uuid.uuid4())[:6].upper()}"
                PromoCode.objects.create(
                    code=ref_promo_code,
                    description=f"Referral reward for inviting {ride.rider.username}",
                    discount_flat=ref.reward_amount,
                    max_uses=1,
                    is_active=True,
                )
                notify(ref.referrer_id, f"Your referral reward of {ref.reward_amount} is ready! Promo code: {ref_promo_code}")
    return ride



@transaction.atomic
def rate_ride(user, ride_id, *, score, comment="", feedback_tags=None):
    ride = Ride.objects.select_for_update().filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound()
    if ride.status != Ride.Status.COMPLETED:
        raise Conflict("Ratings are available after a completed ride.")
    if Rating.objects.filter(ride=ride, author=user).exists():
        raise Conflict("You have already rated this ride.")
    recipient_id = ride.driver_id if user.pk == ride.rider_id else ride.rider_id
    return Rating.objects.create(
        ride=ride, author=user, recipient_id=recipient_id,
        score=score, comment=comment, feedback_tags=feedback_tags or [],
    )


def validate_promo(user, code, fare):
    """Validate a promo code and return (promo, discounted_fare, discount_amount)."""
    require_role(user, "RIDER")
    promo = PromoCode.objects.filter(code=code.strip().upper(), is_active=True).first()
    if not promo:
        raise ValidationError({"promo_code": "Invalid or inactive promo code."})
    if promo.expires_at and promo.expires_at <= timezone.now():
        raise ValidationError({"promo_code": "This promo code has expired."})
    if promo.max_uses is not None and promo.uses >= promo.max_uses:
        raise ValidationError({"promo_code": "This promo code has reached its usage limit."})
    if PromoUsage.objects.filter(promo=promo, rider=user).exists():
        raise ValidationError({"promo_code": "You have already used this promo code."})
    fare = Decimal(str(fare))
    if fare < promo.min_fare:
        raise ValidationError({"promo_code": f"Minimum fare of {promo.min_fare} required for this promo."})
    if promo.discount_percent:
        discount = money(fare * promo.discount_percent / 100)
    else:
        discount = money(min(promo.discount_flat, fare))
    discounted_fare = money(fare - discount)
    return promo, discounted_fare, discount


def apply_promo_to_ride(ride, promo, discount_amount):
    """Atomically record promo usage. Called inside request_ride transaction."""
    PromoCode.objects.filter(pk=promo.pk).update(uses=F("uses") + 1)
    PromoUsage.objects.create(promo=promo, rider=ride.rider, ride=ride, discount_applied=discount_amount)


def raise_sos(user, ride_id, message=""):
    """Raise an SOS/emergency alert for an active ride."""
    ride = Ride.objects.filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound()
    if ride.status not in ACTIVE_STATUSES:
        raise Conflict("SOS can only be raised during an active ride.")
    alert = SOSAlert.objects.create(ride=ride, raised_by=user, message=message.strip())
    # Notify the other participant
    other_id = ride.driver_id if user.pk == ride.rider_id else ride.rider_id
    if other_id:
        notify(other_id, f"SOS alert raised! Message: {message or 'Help needed.'}", ride.id)
    # Email admin
    from django.core.mail import mail_admins
    try:
        mail_admins(
            subject=f"SOS Alert \u2013 Ride {str(ride.pk)[:8]}",
            message=(
                f"User {user.username} (id={user.pk}) raised an SOS alert.\n"
                f"Ride: {ride.pk}\n"
                f"Status: {ride.get_status_display()}\n"
                f"Message: {message or '(none)'}\n"
            ),
            fail_silently=True,
        )
    except Exception:
        pass
    return alert


def resolve_sos(admin_user, alert_id):
    """Mark an SOS alert as resolved. Staff only."""
    if not admin_user.is_staff:
        raise PermissionDenied("Only staff can resolve SOS alerts.")
    alert = SOSAlert.objects.filter(pk=alert_id, resolved_at__isnull=True).first()
    if not alert:
        raise NotFound("Alert not found or already resolved.")
    alert.resolved_at = timezone.now()
    alert.resolved_by = admin_user
    alert.save(update_fields=["resolved_at", "resolved_by"])
    return alert


def get_active_surge_multiplier():
    """Return the highest applicable surge multiplier right now, or Decimal('1')."""
    if not getattr(settings, 'SURGE_PRICING_ENABLED', True):
        return Decimal('1')
    now = timezone.now()
    hour = now.hour
    dow = now.weekday()
    rule = SurgePricingRule.objects.filter(
        is_active=True,
        hour_start__lte=hour,
        hour_end__gt=hour,
    ).filter(
        Q(day_of_week__isnull=True) | Q(day_of_week=dow)
    ).order_by("-multiplier").first()
    return rule.multiplier if rule else Decimal('1')


def schedule_ride(user, *, quote_id, payment_method, scheduled_for):
    """Create a ScheduledRide for future dispatch."""
    require_role(user, "RIDER")
    now = timezone.now()
    if scheduled_for <= now + timedelta(minutes=15):
        raise ValidationError({"scheduled_for": "Must be at least 15 minutes in the future."})
    if scheduled_for > now + timedelta(days=7):
        raise ValidationError({"scheduled_for": "Cannot schedule more than 7 days ahead."})
    if payment_method == "CARD" and not settings.CARD_PAYMENTS_ENABLED:
        raise ValidationError({"payment_method": "Card payments are not configured. Choose cash."})
    quote = RideQuote.objects.filter(pk=quote_id, rider=user).first()
    if not quote:
        raise NotFound("Fare quote not found.")
    if Ride.objects.filter(quote=quote).exists():
        raise Conflict("This quote has already been used. Get a new estimate.")
    return ScheduledRide.objects.create(
        rider=user, quote=quote, payment_method=payment_method, scheduled_for=scheduled_for
    )


def cancel_scheduled_ride(user, scheduled_id):
    """Cancel a scheduled ride that has not yet been dispatched."""
    sr = ScheduledRide.objects.filter(pk=scheduled_id, rider=user, cancelled_at__isnull=True, ride__isnull=True).first()
    if not sr:
        raise NotFound("Scheduled ride not found or already started/cancelled.")
    sr.cancelled_at = timezone.now()
    sr.save(update_fields=["cancelled_at"])
    return sr


@transaction.atomic
def create_carpool(user, ride_id, *, total_seats=3, fare_per_seat=None):
    """Enable carpooling on an active ride."""
    if not getattr(settings, "ALLOW_MULTIPLE_RIDERS_PER_TRIP", False):
        raise Conflict("Only one person can book any trip. Multiple riders per trip are not permitted.")
    ride = Ride.objects.select_for_update().select_related("quote").filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound("Ride not found.")
    if ride.status not in ACTIVE_STATUSES:
        raise Conflict("Carpooling can only be enabled on active rides.")
    if hasattr(ride, "carpool"):
        raise Conflict("Carpool is already enabled for this ride.")

    total_seats = int(total_seats)
    if total_seats < 1 or total_seats > 6:
        raise ValidationError({"total_seats": "Seats must be between 1 and 6."})

    if fare_per_seat is None:
        # Default: split base fare among total possible passengers + primary rider
        seat_fare = money(ride.quote.fare / Decimal(str(total_seats + 1)))
    else:
        seat_fare = money(Decimal(str(fare_per_seat)))

    return CarpoolRide.objects.create(
        ride=ride,
        total_seats=total_seats,
        available_seats=total_seats,
        fare_per_seat=seat_fare,
        is_open=True,
    )


@transaction.atomic
def join_carpool(user, carpool_id, *, seats=1, pickup_label, destination_label):
    """Rider joins an existing carpool."""
    require_role(user, "RIDER")
    if not getattr(settings, "ALLOW_MULTIPLE_RIDERS_PER_TRIP", False):
        raise Conflict("Only one person can book any trip. Multiple riders cannot join or share this trip.")
    seats = int(seats)
    if seats < 1:
        raise ValidationError({"seats": "Must request at least 1 seat."})

    carpool = CarpoolRide.objects.select_for_update().select_related("ride").filter(pk=carpool_id).first()
    if not carpool or not carpool.is_open:
        raise Conflict("This carpool is no longer open for new passengers.")

    ride = carpool.ride
    if user.pk in {ride.rider_id, ride.driver_id}:
        raise Conflict("The host rider or driver cannot join as a secondary passenger.")

    if carpool.available_seats < seats:
        raise Conflict(f"Only {carpool.available_seats} seat(s) remaining in this carpool.")

    if CarpoolPassenger.objects.filter(carpool=carpool, rider=user, status="CONFIRMED").exists():
        raise Conflict("You are already confirmed in this carpool.")

    fare_total = money(carpool.fare_per_seat * Decimal(str(seats)))
    passenger = CarpoolPassenger.objects.create(
        carpool=carpool,
        rider=user,
        pickup_label=pickup_label.strip(),
        destination_label=destination_label.strip(),
        seats=seats,
        fare_paid=fare_total,
        status="CONFIRMED",
    )

    carpool.available_seats -= seats
    if carpool.available_seats == 0:
        carpool.is_open = False
    carpool.save(update_fields=["available_seats", "is_open"])

    notify(ride.rider_id, f"{user.username} joined your carpool ({seats} seat(s)).", ride.id)
    if ride.driver_id:
        notify(ride.driver_id, f"{user.username} joined carpool ({seats} seat(s)).", ride.id)

    return passenger


@transaction.atomic
def leave_carpool(user, carpool_id):
    """Cancel a seat in a carpool."""
    require_role(user, "RIDER")
    carpool = CarpoolRide.objects.select_for_update().select_related("ride").filter(pk=carpool_id).first()
    if not carpool:
        raise NotFound("Carpool not found.")

    passenger = CarpoolPassenger.objects.select_for_update().filter(
        carpool=carpool, rider=user, status="CONFIRMED"
    ).first()
    if not passenger:
        raise NotFound("Confirmed carpool seat not found.")

    passenger.status = "CANCELLED"
    passenger.cancelled_at = timezone.now()
    passenger.save(update_fields=["status", "cancelled_at"])

    carpool.available_seats += passenger.seats
    carpool.is_open = True
    carpool.save(update_fields=["available_seats", "is_open"])

    notify(carpool.ride.rider_id, f"{user.username} cancelled their carpool seat.", carpool.ride.id)
    if carpool.ride.driver_id:
        notify(carpool.ride.driver_id, f"{user.username} cancelled their carpool seat.", carpool.ride.id)

    return passenger


@transaction.atomic
def tip_ride(user, ride_id, amount):
    """Rider tips the driver for a completed ride."""
    require_role(user, "RIDER")
    ride = Ride.objects.select_for_update().filter(pk=ride_id).first()
    if not ride or ride.rider_id != user.pk:
        raise NotFound("Ride not found.")
    if ride.status != Ride.Status.COMPLETED:
        raise Conflict("Tips can only be added to completed rides.")
    if hasattr(ride, "tip"):
        raise Conflict("You have already tipped for this ride.")

    amount = money(Decimal(str(amount)))
    if amount < Decimal("1.00"):
        raise ValidationError({"amount": "Tip must be at least 1.00."})

    tip = RideTip.objects.create(ride=ride, amount=amount)
    if ride.driver_id:
        notify(ride.driver_id, f"You received a tip of {amount} {ride.quote.currency}!", ride.id)
    return tip


@transaction.atomic
def send_ride_message(user, ride_id, content):
    """Send an in-app message between rider and driver during an active ride."""
    ride = Ride.objects.select_for_update().filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound("Ride not found.")
    if ride.status not in ACTIVE_STATUSES:
        raise Conflict("Chat is only available during an active ride.")

    content = content.strip()
    if not content:
        raise ValidationError({"content": "Message content cannot be blank."})

    msg = RideMessage.objects.create(ride=ride, sender=user, content=content)
    recipient_id = ride.driver_id if user.pk == ride.rider_id else ride.rider_id
    if recipient_id:
        publish(recipient_id, "chat_message", ride_id=str(ride.pk), content=content, sender=user.username)
        notify(recipient_id, f"{user.username}: {content[:50]}", ride.id)
    return msg


@transaction.atomic
def create_saved_place(user, *, label, address, latitude, longitude):
    """Save a favourite place for quick destination selection."""
    require_role(user, "RIDER")
    label = label.strip()
    if SavedPlace.objects.filter(user=user, label__iexact=label).exists():
        raise Conflict(f"A saved place with label '{label}' already exists.")
    return SavedPlace.objects.create(
        user=user, label=label, address=address.strip(),
        latitude=latitude, longitude=longitude,
    )


@transaction.atomic
def delete_saved_place(user, place_id):
    """Delete a saved place."""
    place = SavedPlace.objects.filter(pk=place_id, user=user).first()
    if not place:
        raise NotFound("Saved place not found.")
    place.delete()
    return True


def get_ride_eta(user, ride_id):
    """Calculate live ETA and distance from driver's latest coordinates to destination."""
    ride = Ride.objects.select_related("quote", "driver__driver_profile").filter(pk=ride_id).first()
    if not ride or user.pk not in {ride.rider_id, ride.driver_id}:
        raise NotFound("Ride not found.")

    if not ride.driver or not hasattr(ride.driver, "driver_profile"):
        return {
            "estimated_minutes": ride.quote.estimated_minutes,
            "remaining_km": str(ride.quote.distance_km),
            "status": ride.status,
        }

    profile = ride.driver.driver_profile
    if profile.latitude is None or profile.longitude is None:
        return {
            "estimated_minutes": ride.quote.estimated_minutes,
            "remaining_km": str(ride.quote.distance_km),
            "status": ride.status,
        }

    # If arriving: distance to pickup; if in progress: distance to destination
    if ride.status in [Ride.Status.ACCEPTED, Ride.Status.DRIVER_ARRIVING]:
        target_lat = ride.quote.pickup_latitude
        target_lon = ride.quote.pickup_longitude
    else:
        target_lat = ride.quote.destination_latitude
        target_lon = ride.quote.destination_longitude

    rem_direct = distance_km(profile.latitude, profile.longitude, target_lat, target_lon)
    rem_km = money(Decimal(str(rem_direct)) * Decimal(settings.FARE_ROAD_FACTOR))
    eta_mins = max(1, math.ceil(float(rem_km) / 25 * 60))

    return {
        "estimated_minutes": eta_mins,
        "remaining_km": str(rem_km),
        "driver_latitude": str(profile.latitude),
        "driver_longitude": str(profile.longitude),
        "status": ride.status,
    }


