import uuid
from decimal import Decimal

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
    surge_multiplier = models.DecimalField(max_digits=4, decimal_places=2, default=1)
    vehicle_class = models.CharField(max_length=10, default="STANDARD")
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
        WALLET = "WALLET", "Nori Wallet (demo)"
        SSL_DEMO = "SSL_DEMO", "SSLCOMMERZ (demo)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rider_rides")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="driver_rides")
    quote = models.OneToOneField(RideQuote, on_delete=models.PROTECT)
    request_id = models.UUIDField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED, db_index=True)
    payment_method = models.CharField(max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH)
    cancellation_fee = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal("0.00"))
    cancellation_reason = models.CharField(max_length=300, blank=True)
    verification_pin = models.CharField(max_length=4, blank=True)
    ride_preferences = models.JSONField(default=dict, blank=True)
    carbon_emissions_kg = models.DecimalField(max_digits=6, decimal_places=3, default=Decimal("0"))

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
        indexes = [
            models.Index(fields=["rider", "-created_at", "-id"], name="ride_rider_history_idx"),
            models.Index(fields=["driver", "-created_at", "-id"], name="ride_driver_history_idx"),
            models.Index(fields=["driver", "completed_at"], condition=models.Q(status="COMPLETED"), name="ride_driver_completed_idx"),
        ]
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
        indexes = [models.Index(fields=["ride", "-recorded_at"], name="track_ride_recorded_idx")]


class Rating(models.Model):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="ratings")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="given_ratings")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="received_ratings")
    score = models.PositiveSmallIntegerField(validators=[MinValueValidator(1), MaxValueValidator(5)])
    comment = models.CharField(max_length=500, blank=True)
    feedback_tags = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["ride", "author"], name="one_rating_per_participant"),
            models.CheckConstraint(condition=models.Q(score__gte=1, score__lte=5), name="rating_valid_score"),
        ]


class PromoCode(models.Model):
    code = models.CharField(max_length=20, unique=True, db_index=True)
    description = models.CharField(max_length=200, blank=True)
    discount_percent = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.01')), MaxValueValidator(Decimal('100'))],
        help_text="Percentage discount (e.g. 10 for 10%)",
    )
    discount_flat = models.DecimalField(
        max_digits=10, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.01'))],
        help_text="Flat amount discount in ride currency",
    )
    min_fare = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    max_uses = models.PositiveIntegerField(null=True, blank=True, help_text="null = unlimited")
    uses = models.PositiveIntegerField(default=0)
    expires_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=(
                    models.Q(discount_percent__isnull=False, discount_flat__isnull=True)
                    | models.Q(discount_percent__isnull=True, discount_flat__isnull=False)
                ),
                name="promo_exactly_one_discount_type",
            )
        ]

    def save(self, *args, **kwargs):
        self.code = self.code.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class PromoUsage(models.Model):
    promo = models.ForeignKey(PromoCode, on_delete=models.CASCADE, related_name="usage_records")
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="promo_usages")
    ride = models.OneToOneField("Ride", on_delete=models.CASCADE, related_name="promo_usage")
    discount_applied = models.DecimalField(max_digits=10, decimal_places=2)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["promo", "rider"], name="promo_one_per_rider"),
        ]


class SOSAlert(models.Model):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="sos_alerts")
    raised_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sos_alerts_raised"
    )
    message = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    resolved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="sos_alerts_resolved",
    )

    class Meta:
        ordering = ["-created_at"]

class ScheduledRide(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="scheduled_rides")
    quote = models.ForeignKey(RideQuote, on_delete=models.CASCADE)
    payment_method = models.CharField(max_length=10, choices=Ride.PaymentMethod.choices, default=Ride.PaymentMethod.CASH)
    scheduled_for = models.DateTimeField(db_index=True)
    ride = models.OneToOneField(Ride, null=True, blank=True, on_delete=models.SET_NULL, related_name="from_scheduled")
    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["scheduled_for"]
        indexes = [
            models.Index(fields=["scheduled_for", "id"], condition=models.Q(cancelled_at__isnull=True, ride__isnull=True), name="schedule_due_pending_idx"),
            models.Index(fields=["rider", "scheduled_for", "id"], condition=models.Q(cancelled_at__isnull=True, ride__isnull=True), name="schedule_rider_pending_idx"),
        ]


class SurgePricingRule(models.Model):
    """Defines a surge multiplier for a specific time window."""
    name = models.CharField(max_length=100)
    multiplier = models.DecimalField(
        max_digits=4, decimal_places=2,
        help_text="e.g. 1.5 for 50% surge",
        validators=[MinValueValidator(Decimal('1.00'))],
    )
    hour_start = models.PositiveSmallIntegerField(help_text="Hour in UTC (0-23)")
    hour_end = models.PositiveSmallIntegerField(help_text="Hour in UTC (0-23), exclusive")
    day_of_week = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="0=Monday…6=Sunday; null means every day"
    )
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-multiplier"]

    def __str__(self):
        return f"{self.name} ×{self.multiplier}"


class CarpoolRide(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ride = models.OneToOneField(Ride, on_delete=models.CASCADE, related_name="carpool")
    total_seats = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(1), MaxValueValidator(6)]
    )
    available_seats = models.PositiveSmallIntegerField(
        default=3, validators=[MinValueValidator(0), MaxValueValidator(6)]
    )
    fare_per_seat = models.DecimalField(max_digits=10, decimal_places=2)
    is_open = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"Carpool for Ride {str(self.ride_id)[:8]} ({self.available_seats}/{self.total_seats} seats)"


class CarpoolPassenger(models.Model):
    carpool = models.ForeignKey(CarpoolRide, on_delete=models.CASCADE, related_name="passengers")
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="carpool_trips")
    pickup_label = models.CharField(max_length=200)
    destination_label = models.CharField(max_length=200)
    seats = models.PositiveSmallIntegerField(default=1, validators=[MinValueValidator(1)])
    fare_paid = models.DecimalField(max_digits=10, decimal_places=2)
    status = models.CharField(
        max_length=15,
        choices=[("CONFIRMED", "Confirmed"), ("CANCELLED", "Cancelled")],
        default="CONFIRMED",
    )
    joined_at = models.DateTimeField(auto_now_add=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["joined_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["carpool", "rider"],
                condition=models.Q(status="CONFIRMED"),
                name="unique_confirmed_carpool_rider",
            ),
        ]

    def __str__(self):
        return f"{self.rider.username} ({self.seats} seats) on Carpool {str(self.carpool_id)[:8]}"


class RideTip(models.Model):
    ride = models.OneToOneField(Ride, on_delete=models.CASCADE, related_name="tip")
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("1.00"))]
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Tip {self.amount} on Ride {str(self.ride_id)[:8]}"


class RideMessage(models.Model):
    ride = models.ForeignKey(Ride, on_delete=models.CASCADE, related_name="messages")
    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="sent_ride_messages"
    )
    content = models.CharField(max_length=500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.sender.username} on Ride {str(self.ride_id)[:8]}: {self.content[:30]}"


class SavedPlace(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="saved_places"
    )
    label = models.CharField(max_length=50)
    address = models.CharField(max_length=200)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "label"], name="unique_user_saved_place_label"),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.label}"


class RideStop(models.Model):
    quote = models.ForeignKey(RideQuote, on_delete=models.CASCADE, related_name="stops")
    stop_order = models.PositiveSmallIntegerField(default=1)
    label = models.CharField(max_length=200)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)

    class Meta:
        ordering = ["stop_order"]

    def __str__(self):
        return f"Stop {self.stop_order}: {self.label}"


class FavouriteDriver(models.Model):
    rider = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favourite_drivers")
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="favourited_by")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["rider", "driver"], name="unique_favourite_driver")]

    def __str__(self):
        return f"{self.rider.username} favorited {self.driver.username}"


class TripShareToken(models.Model):
    ride = models.ForeignKey("Ride", on_delete=models.CASCADE, related_name="share_tokens")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    def __str__(self):
        return f"ShareToken {self.token[:8]} for Ride {self.ride_id}"


class LostItem(models.Model):
    class Status(models.TextChoices):
        REPORTED = "REPORTED", "Reported"
        CONTACTING = "CONTACTING", "Contacting driver"
        FOUND = "FOUND", "Found"
        RETURNED = "RETURNED", "Returned"
        NOT_FOUND = "NOT_FOUND", "Not found"

    ride = models.ForeignKey("Ride", on_delete=models.CASCADE, related_name="lost_items")
    reporter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    description = models.CharField(max_length=500)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.REPORTED)
    admin_note = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"LostItem on Ride {self.ride_id}: {self.description[:30]}"


class Badge(models.Model):
    code = models.CharField(max_length=30, unique=True)
    name = models.CharField(max_length=100)
    description = models.CharField(max_length=200)
    icon = models.CharField(max_length=10)  # emoji
    criteria_type = models.CharField(max_length=30)  # ride_count, early_bird, night_owl, big_tipper, five_star_streak, loyal_rider
    criteria_value = models.PositiveIntegerField(default=1)

    def __str__(self):
        return f"{self.icon} {self.name}"


class UserBadge(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="earned_badges")
    badge = models.ForeignKey(Badge, on_delete=models.CASCADE)
    earned_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["user", "badge"], name="unique_user_badge")]
        ordering = ["-earned_at"]

    def __str__(self):
        return f"{self.user.username} earned {self.badge.name}"


class PriceLock(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="price_locks")
    pickup_label = models.CharField(max_length=200)
    destination_label = models.CharField(max_length=200)
    pickup_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    pickup_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_latitude = models.DecimalField(max_digits=9, decimal_places=6)
    destination_longitude = models.DecimalField(max_digits=9, decimal_places=6)
    locked_fare = models.DecimalField(max_digits=10, decimal_places=2)
    currency = models.CharField(max_length=3)
    valid_until = models.DateTimeField()
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user.username}: {self.pickup_label} -> {self.destination_label} ({self.locked_fare} {self.currency})"


class LoyaltyPoints(models.Model):
    TIER_CHOICES = [("SILVER", "Silver"), ("GOLD", "Gold"), ("PLATINUM", "Platinum")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="loyalty")
    points = models.PositiveIntegerField(default=0)
    tier = models.CharField(max_length=10, choices=TIER_CHOICES, default="SILVER")
    lifetime_points = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}: {self.points} pts ({self.tier})"


