from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.checks import Error, Warning, register


@register()
def validate_fare_settings(app_configs, **kwargs):
    errors = []
    for key in ("FARE_BASE", "FARE_PER_KM", "FARE_MINIMUM", "FARE_ROAD_FACTOR", "PLATFORM_FEE_PERCENT"):
        try:
            value = Decimal(getattr(settings, key))
            if not value.is_finite() or value < 0 or (key == "FARE_ROAD_FACTOR" and value < 1) or (key == "PLATFORM_FEE_PERCENT" and value > 100):
                raise InvalidOperation()
        except (InvalidOperation, TypeError, ValueError):
            errors.append(Error(f"{key} has an invalid value.", id="rides.E001"))
    if settings.RIDE_CURRENCY not in {"BDT", "USD", "EUR", "GBP", "INR"}:
        errors.append(Error("Choose a supported two-decimal currency: BDT, USD, EUR, GBP, INR.", id="rides.E002"))
    return errors


@register(deploy=True)
def deployment_checks(app_configs, **kwargs):
    if not settings.REDIS_URL:
        return [Warning("Set REDIS_URL for shared live updates and rate limiting across processes.", id="rides.W001")]
    return []
