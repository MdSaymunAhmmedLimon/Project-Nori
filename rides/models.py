import uuid

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

ACTIVE_STATUSES = ["REQUESTED", "SEARCHING", "ACCEPTED", "DRIVER_ARRIVING", "DRIVER_ARRIVED", "IN_PROGRESS"]
ASSIGNED_STATUSES = ["ACCEPTED", "DRIVER_ARRIVING", "DRIVER_ARRIVED", "IN_PROGRESS"]


class RideQuote(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    pickup_label = models.CharField(max_length=200)
    destination_label = models.CharField(max_length=200)
    pickup_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    distance_km = models.DecimalField(max_digits=8, decimal_places=2)
    estimated_minutes = models.PositiveIntegerField()
    fare = models.DecimalField(max_digits=10, decimal_places=2)
    platform_fee = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()


class Ride(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "REQUESTED", "Requested"
        SEARCHING = "SEARCHING", "Searching for a driver"
        ACCEPTED = "ACCEPTED", "Driver assigned"
        DRIVER_ARRIVING = "DRIVER_ARRIVING", "Driver on the way"
        DRIVER_ARRIVED = "DRIVER_ARRIVED", "Driver arrived"
        IN_PROGRESS = "IN_PROGRESS", "Trip in progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    class PaymentMethod(models.TextChoices):
        CASH = "CASH", "Cash"
        CARD = "CARD", "Card"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rider_rides")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="driver_rides")
    quote = models.OneToOneField(RideQuote, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    payment_method = models.CharField(max_length=4, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    cancellation_reason = models.CharField(max_length=300, blank=True)
    cancelled_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="cancelled_rides")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    arrived_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["rider", "request_id"], name="ride_request_idempotent"),
            models.UniqueConstraint(fields=["rider"], condition=models.Q(status__in=ACTIVE_STATUSES), name="one_active_ride_per_rider"),
            models.UniqueConstraint(fields=["driver"], condition=models.Q(status__in=ACTIVE_STATUSES, driver__isnull=False), name="one_active_ride_per_driver"),
            models.CheckConstraint(condition=models.Q(status__in=ACTIVE_STATUSES + ["COMPLETED", "CANCELLED"]), name="ride_valid_status"),
            models.CheckConstraint(condition=~models.Q(rider=models.F("driver")), name="ride_distinct_participants"),
        ]

    def __str__(self):
        return f"{str(self.id)[:8]} — {self.get_status_display()}"


class RideOffer(models.Model):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="offers")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="ride_offers")
    distance_km = models.DecimalField(max_digits=7, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    rejected_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["ride", "driver"], name="unique_driver_offer")]


class RideEvent(models.Model):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="events")
    status = models.CharField(max_length=20, choices=Ride.Status.choices)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]


class TrackPoint(models.Model):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="track_points")
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    recorded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["recorded_at"]


class Rating(models.Model):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="ratings")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="given_ratings")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_ratings")
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ride", "author"], name="one_rating_per_participant"),
            models.CheckConstraint(condition=models.Q(score__gte=1, score__lte=5), name="rating_valid_score"),
        ]
