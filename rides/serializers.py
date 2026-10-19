from decimal import Decimal

from django.db.models import Avg
from django.utils import timezone
from rest_framework import serializers


from accounts.serializers import WritableFieldsOnlyMixin
from drivers.models import DriverProfile
from payments.models import Payment

from .models import (
    ASSIGNED_STATUSES, Badge, CarpoolPassenger, CarpoolRide,
    FavouriteDriver, LostItem, LoyaltyPoints, PriceLock, Rating, Ride,
    RideEvent, RideMessage, RideOffer, RideQuote, RideStop, RideTip,
    SavedPlace, ScheduledRide, SOSAlert, TripShareToken, UserBadge,
)

_UNSET = object()


class CoordinatesSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)


class StopInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    label = serializers.CharField(max_length=200)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)


class RideStopSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideStop
        fields = ("id", "stop_order", "label", "latitude", "longitude")


class PlaceItemSerializer(serializers.Serializer):
    label = serializers.CharField()
    address = serializers.CharField()
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6)
    category = serializers.CharField(required=False, default="")


class QuoteInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    pickup_label = serializers.CharField(max_length=200)
    destination_label = serializers.CharField(max_length=200)
    pickup_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False)
    pickup_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, required=False)
    destination_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False)
    destination_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, required=False)
    vehicle_class = serializers.ChoiceField(choices=["STANDARD", "COMFORT", "XL", "MOTO"], default="STANDARD", required=False)
    stops = StopInputSerializer(many=True, required=False, default=list)

    def validate(self, attrs):
        from .places import resolve_location_name
        request = self.context.get("request")
        user = getattr(request, "user", None)

        if attrs.get("pickup_latitude") is None or attrs.get("pickup_longitude") is None:
            resolved = resolve_location_name(attrs.get("pickup_label"), user=user)
            if not resolved:
                raise serializers.ValidationError({
                    "pickup_label": f"Could not find coordinates for '{attrs.get('pickup_label')}'. Please select a valid location from the search list."
                })
            attrs["pickup_latitude"], attrs["pickup_longitude"], _ = resolved

        if attrs.get("destination_latitude") is None or attrs.get("destination_longitude") is None:
            resolved = resolve_location_name(attrs.get("destination_label"), user=user)
            if not resolved:
                raise serializers.ValidationError({
                    "destination_label": f"Could not find coordinates for '{attrs.get('destination_label')}'. Please select a valid location from the search list."
                })
            attrs["destination_latitude"], attrs["destination_longitude"], _ = resolved

        return attrs



class QuoteSerializer(serializers.ModelSerializer):
    stops = RideStopSerializer(many=True, read_only=True)

    class Meta:
        model = RideQuote
        exclude = ("rider",)



class RequestRideSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    quote_id = serializers.UUIDField()
    request_id = serializers.UUIDField()
    payment_method = serializers.ChoiceField(choices=Ride.PaymentMethod.choices, default="CASH")
    promo_code = serializers.CharField(max_length=20, required=False, allow_blank=True, default="")


class ActionSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    action = serializers.ChoiceField(choices=["arriving", "arrived", "start", "complete", "cancel"])
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class RatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rating
        fields = ("id", "author", "recipient", "score", "comment", "feedback_tags", "created_at")
        read_only_fields = ("id", "author", "recipient", "created_at")


class RatingInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    score = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")
    feedback_tags = serializers.ListField(
        child=serializers.CharField(max_length=50), required=False, default=list
    )


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideEvent
        fields = ("status", "created_at")


class RideSerializer(serializers.ModelSerializer):
    quote = QuoteSerializer(read_only=True)
    driver = serializers.SerializerMethodField()
    rider = serializers.SerializerMethodField()
    events = EventSerializer(many=True, read_only=True)
    ratings = RatingSerializer(many=True, read_only=True)
    payment = serializers.SerializerMethodField()
    tip = serializers.SerializerMethodField()
    verification_pin = serializers.SerializerMethodField()

    class Meta:
        model = Ride
        fields = (
            "id", "request_id", "status", "quote", "rider", "driver", "payment_method", "payment",
            "cancellation_fee", "cancellation_reason", "verification_pin", "ride_preferences",
            "carbon_emissions_kg", "tip", "created_at", "updated_at",
            "accepted_at", "arrived_at", "started_at", "completed_at", "cancelled_at", "events", "ratings",
        )

    def get_verification_pin(self, ride):
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else self.context.get("user")
        if user and user.is_authenticated and user.pk in {ride.rider_id, ride.driver_id}:
            return ride.verification_pin
        return None

    def get_tip(self, ride):
        if hasattr(ride, "tip"):
            return str(ride.tip.amount)
        return None


    def person(self, user, *, contact=False, average_rating=_UNSET):
        if not user:
            return None
        if average_rating is _UNSET:
            average_rating = user.received_ratings.aggregate(value=Avg("score"))["value"]
        data = {"id": user.pk, "name": user.get_full_name() or user.username,
                "rating": average_rating}
        if contact:
            data["phone_number"] = user.phone_number
        return data

    def get_rider(self, ride):
        return self.person(ride.rider, contact=ride.status in ASSIGNED_STATUSES,
                           average_rating=getattr(ride, "rider_average_rating", _UNSET))

    def get_driver(self, ride):
        data = self.person(ride.driver, contact=ride.status in ASSIGNED_STATUSES,
                           average_rating=getattr(ride, "driver_average_rating", _UNSET))
        if data is None:
            return None
        if hasattr(ride, "driver_average_rating"):
            profile = getattr(ride.driver, "driver_profile", None)
        else:
            profile = DriverProfile.objects.filter(user=ride.driver).select_related("vehicle").first()
        if profile:
            if hasattr(profile, "vehicle"):
                vehicle = profile.vehicle
                data["vehicle"] = {name: getattr(vehicle, name) for name in ("make", "model", "color", "license_plate")}
            if ride.status in ASSIGNED_STATUSES and profile.location_updated_at:
                data["location"] = {
                    "latitude": str(profile.latitude), "longitude": str(profile.longitude),
                    "updated_at": profile.location_updated_at.isoformat(),
                    "age_seconds": max(0, int((timezone.now() - profile.location_updated_at).total_seconds())),
                }
        return data

    def get_payment(self, ride):
        try:
            payment = ride.payment
        except Payment.DoesNotExist:
            return None
        return {"id": str(payment.pk), "amount": str(payment.amount), "currency": payment.currency,
                "status": payment.status, "method": payment.method, "paid_at": payment.paid_at}


class OfferSerializer(serializers.ModelSerializer):
    quote = QuoteSerializer(source="ride.quote", read_only=True)
    ride_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = RideOffer
        fields = ("id", "ride_id", "distance_km", "expires_at", "quote")


class ScheduleRideInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    quote_id = serializers.UUIDField()
    payment_method = serializers.ChoiceField(choices=Ride.PaymentMethod.choices, default="CASH")
    scheduled_for = serializers.DateTimeField()


class RescheduleRideInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    scheduled_for = serializers.DateTimeField()


class ScheduledRideSerializer(serializers.ModelSerializer):
    quote = QuoteSerializer(read_only=True)

    class Meta:
        model = ScheduledRide
        fields = ("id", "quote", "payment_method", "scheduled_for", "ride", "cancelled_at", "created_at")
        read_only_fields = ("id", "ride", "cancelled_at", "created_at")


class PromoValidateSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    code = serializers.CharField(max_length=20)
    fare = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("0.01"))



class SOSInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    message = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


class SOSAlertSerializer(serializers.ModelSerializer):
    raised_by = serializers.StringRelatedField()
    resolved_by = serializers.StringRelatedField()

    class Meta:
        model = SOSAlert
        fields = ("id", "ride", "raised_by", "message", "created_at", "resolved_at", "resolved_by")
        read_only_fields = ("id", "created_at", "resolved_at", "resolved_by")


class CarpoolPassengerSerializer(serializers.ModelSerializer):
    rider = serializers.StringRelatedField()

    class Meta:
        model = CarpoolPassenger
        fields = ("id", "rider", "pickup_label", "destination_label", "seats", "fare_paid", "status", "joined_at", "cancelled_at")
        read_only_fields = ("id", "rider", "fare_paid", "joined_at", "cancelled_at")


class CarpoolRideSerializer(serializers.ModelSerializer):
    passengers = CarpoolPassengerSerializer(many=True, read_only=True)
    ride_id = serializers.UUIDField(source="ride.id", read_only=True)

    class Meta:
        model = CarpoolRide
        fields = ("id", "ride_id", "total_seats", "available_seats", "fare_per_seat", "is_open", "created_at", "passengers")
        read_only_fields = ("id", "ride_id", "available_seats", "created_at")


class CarpoolCreateSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    total_seats = serializers.IntegerField(min_value=1, max_value=6, default=3)
    fare_per_seat = serializers.DecimalField(max_digits=10, decimal_places=2, required=False, allow_null=True)


class CarpoolJoinSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    seats = serializers.IntegerField(min_value=1, max_value=6, default=1)
    pickup_label = serializers.CharField(max_length=200)
    destination_label = serializers.CharField(max_length=200)


class RideTipInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("1.00"))


class RideTipSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideTip
        fields = ("id", "ride", "amount", "created_at")
        read_only_fields = ("id", "ride", "created_at")


class RideMessageInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    content = serializers.CharField(max_length=500)


class RideMessageSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source="sender.username", read_only=True)

    class Meta:
        model = RideMessage
        fields = ("id", "sender", "sender_name", "content", "created_at")
        read_only_fields = ("id", "sender", "created_at")


class SavedPlaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = SavedPlace
        fields = ("id", "label", "address", "latitude", "longitude", "created_at")
        read_only_fields = ("id", "created_at")


class SavedPlaceInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    label = serializers.CharField(max_length=50)
    address = serializers.CharField(max_length=200)
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)


class FavouriteDriverSerializer(serializers.ModelSerializer):
    driver_id = serializers.IntegerField(source="driver.id", read_only=True)
    driver_name = serializers.SerializerMethodField()
    driver_rating = serializers.SerializerMethodField()

    class Meta:
        model = FavouriteDriver
        fields = ("id", "driver_id", "driver_name", "driver_rating", "created_at")
        read_only_fields = fields

    def get_driver_name(self, obj):
        return obj.driver.get_full_name() or obj.driver.username

    def get_driver_rating(self, obj):
        if hasattr(obj, "average_rating"):
            return obj.average_rating
        return obj.driver.received_ratings.aggregate(value=Avg("score"))["value"]


class LostItemSerializer(serializers.ModelSerializer):
    pickup_label = serializers.CharField(source="ride.quote.pickup_label", read_only=True)
    destination_label = serializers.CharField(source="ride.quote.destination_label", read_only=True)

    class Meta:
        model = LostItem
        fields = (
            "id", "ride", "pickup_label", "destination_label", "description",
            "status", "created_at", "resolved_at",
        )
        read_only_fields = fields


class LostItemInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    description = serializers.CharField(max_length=500)


class TripShareSerializer(serializers.ModelSerializer):
    share_url = serializers.SerializerMethodField()

    class Meta:
        model = TripShareToken
        fields = ("id", "ride", "token", "share_url", "created_at", "expires_at")
        read_only_fields = ("id", "ride", "token", "share_url", "created_at", "expires_at")

    def get_share_url(self, obj):
        request = self.context.get("request")
        path = f"/share/{obj.token}/"
        if request:
            return request.build_absolute_uri(path)
        return path


class BadgeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Badge
        fields = ("id", "code", "name", "description", "icon", "criteria_type", "criteria_value")


class UserBadgeSerializer(serializers.ModelSerializer):
    badge = BadgeSerializer(read_only=True)

    class Meta:
        model = UserBadge
        fields = ("id", "badge", "earned_at")


class PriceLockSerializer(serializers.ModelSerializer):
    class Meta:
        model = PriceLock
        fields = (
            "id", "pickup_label", "destination_label",
            "pickup_latitude", "pickup_longitude",
            "destination_latitude", "destination_longitude",
            "locked_fare", "currency", "valid_until", "is_active", "created_at",
        )
        read_only_fields = ("id", "locked_fare", "currency", "valid_until", "is_active", "created_at")


class PriceLockInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    pickup_label = serializers.CharField(max_length=200)
    destination_label = serializers.CharField(max_length=200)
    pickup_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False)
    pickup_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, required=False)
    destination_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90, required=False)
    destination_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180, required=False)

    def validate(self, attrs):
        from .places import resolve_location_name
        request = self.context.get("request")
        user = getattr(request, "user", None)
        if attrs.get("pickup_latitude") is None or attrs.get("pickup_longitude") is None:
            resolved = resolve_location_name(attrs.get("pickup_label"), user=user)
            if not resolved:
                raise serializers.ValidationError({
                    "pickup_label": f"Could not find coordinates for '{attrs.get('pickup_label')}'."
                })
            attrs["pickup_latitude"], attrs["pickup_longitude"], _ = resolved

        if attrs.get("destination_latitude") is None or attrs.get("destination_longitude") is None:
            resolved = resolve_location_name(attrs.get("destination_label"), user=user)
            if not resolved:
                raise serializers.ValidationError({
                    "destination_label": f"Could not find coordinates for '{attrs.get('destination_label')}'."
                })
            attrs["destination_latitude"], attrs["destination_longitude"], _ = resolved
        return attrs


class LoyaltyPointsSerializer(serializers.ModelSerializer):
    class Meta:
        model = LoyaltyPoints
        fields = ("points", "tier", "lifetime_points", "updated_at")


