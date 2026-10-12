from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models.functions import Lower

from .validators import validate_license_expiry, validate_vehicle_year


class DriverProfile(models.Model):
    class Verification(models.TextChoices):
        PENDING = "PENDING", "Awaiting review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Needs changes"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="driver_profile",
        limit_choices_to={"role": "DRIVER"},
    )
    license_number = models.CharField(max_length=64, unique=True)
    license_expiry_date = models.DateField(validators=[validate_license_expiry])
    verification_status = models.CharField(max_length=8, choices=Verification.choices, default=Verification.PENDING)
    review_note = models.CharField(max_length=300, blank=True)
    is_online = models.BooleanField(default=False, db_index=True)
    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    location_updated_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("license_number"), name="drivers_license_ci_unique"),
        ]

    def clean(self):
        super().clean()
        if self.user_id and self.user.role != "DRIVER":
            raise ValidationError({"user": "Only drivers can have a driver profile."})

    def save(self, *args, **kwargs):
        self.license_number = self.license_number.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Driver: {self.user.username}"


class Vehicle(models.Model):
    driver = models.OneToOneField(
        DriverProfile, on_delete=models.CASCADE, related_name="vehicle"
    )
    license_plate = models.CharField(max_length=32, unique=True)
    make = models.CharField(max_length=50)
    model = models.CharField(max_length=50)
    color = models.CharField(max_length=30)
    year = models.PositiveSmallIntegerField(validators=[validate_vehicle_year])
    seats = models.PositiveSmallIntegerField(
        default=4, validators=[MinValueValidator(1), MaxValueValidator(8)]
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower("license_plate"), name="drivers_plate_ci_unique"),
            models.CheckConstraint(
                condition=models.Q(seats__gte=1, seats__lte=8),
                name="drivers_vehicle_valid_seats",
            ),
        ]

    def save(self, *args, **kwargs):
        self.license_plate = self.license_plate.strip().upper()
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.license_plate} ({self.make} {self.model})"
