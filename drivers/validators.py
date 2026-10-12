from django.core.exceptions import ValidationError
from django.utils import timezone


def validate_license_expiry(value):
    if value < timezone.localdate():
        raise ValidationError("The driving license must not be expired.")


def validate_vehicle_year(value):
    if not 1900 <= value <= timezone.localdate().year + 1:
        raise ValidationError("Enter a vehicle year from 1900 through next year.")
