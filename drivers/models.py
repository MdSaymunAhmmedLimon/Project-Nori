from decimal import Decimal

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
    class VehicleClass(models.TextChoices):
        STANDARD = "STANDARD", "Standard"
        COMFORT = "COMFORT", "Comfort"
        XL = "XL", "XL (6-Seater)"
        MOTO = "MOTO", "Motorbike"

    driver = models.OneToOneField(
        DriverProfile, on_delete=models.CASCADE, related_name="vehicle"
    )
    license_plate = models.CharField(max_length=32, unique=True)
    vehicle_class = models.CharField(
        max_length=10, choices=VehicleClass.choices, default=VehicleClass.STANDARD
    )
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


def driver_document_upload_path(instance, filename):
    """Store driver documents under driver_docs/<driver_pk>/<filename>."""
    return f"driver_docs/{instance.driver.pk}/{filename}"


class DriverDocument(models.Model):
    class DocType(models.TextChoices):
        LICENSE_FRONT = "LICENSE_FRONT", "Driving licence (front)"
        LICENSE_BACK = "LICENSE_BACK", "Driving licence (back)"
        VEHICLE_FRONT = "VEHICLE_FRONT", "Vehicle photo (front)"
        VEHICLE_BACK = "VEHICLE_BACK", "Vehicle photo (back)"
        VEHICLE_SIDE = "VEHICLE_SIDE", "Vehicle photo (side)"
        INSURANCE = "INSURANCE", "Insurance certificate"
        OTHER = "OTHER", "Other document"

    class ReviewStatus(models.TextChoices):
        PENDING = "PENDING", "Awaiting review"
        ACCEPTED = "ACCEPTED", "Accepted"
        REJECTED = "REJECTED", "Rejected"

    driver = models.ForeignKey(
        DriverProfile, on_delete=models.CASCADE, related_name="documents"
    )
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    file = models.FileField(upload_to=driver_document_upload_path)
    review_status = models.CharField(
        max_length=8, choices=ReviewStatus.choices, default=ReviewStatus.PENDING
    )
    review_note = models.CharField(max_length=300, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-uploaded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["driver", "doc_type"],
                name="driver_doc_type_unique",
            )
        ]

    def __str__(self):
        return f"{self.driver} — {self.get_doc_type_display()}"


class PayoutRequest(models.Model):
    class Method(models.TextChoices):
        BANK = "BANK", "Bank Transfer"
        BKASH = "BKASH", "bKash"
        NAGAD = "NAGAD", "Nagad"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"
        PAID = "PAID", "Paid"

    driver = models.ForeignKey(
        DriverProfile, on_delete=models.CASCADE, related_name="payout_requests"
    )
    amount = models.DecimalField(
        max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("10.00"))]
    )
    payout_method = models.CharField(max_length=10, choices=Method.choices)
    account_details = models.CharField(max_length=200)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    admin_note = models.CharField(max_length=300, blank=True)
    requested_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-requested_at"]

    def __str__(self):
        return f"{self.driver} - {self.amount} ({self.get_status_display()})"

