from django.db.models import Avg
from django.utils import timezone
from rest_framework import serializers

from accounts.serializers import WritableFieldsOnlyMixin
from drivers.models import DriverProfile
from payments.models import Payment

from .models import ASSIGNED_STATUSES, Rating, Ride, RideEvent, RideOffer, RideQuote


class CoordinatesSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)


class QuoteInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    pickup_label = serializers.CharField(max_length=200)
    destination_label = serializers.CharField(max_length=200)
    pickup_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    pickup_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)
    destination_latitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-90, max_value=90)
    destination_longitude = serializers.DecimalField(max_digits=9, decimal_places=6, min_value=-180, max_value=180)


class QuoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = RideQuote
        exclude = ("rider",)


class RequestRideSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    quote_id = serializers.UUIDField()
    request_id = serializers.UUIDField()
    payment_method = serializers.ChoiceField(choices=Ride.PaymentMethod.choices, default="CASH")


class ActionSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    action = serializers.ChoiceField(choices=["arriving", "arrived", "start", "complete", "cancel"])
    reason = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


class RatingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Rating
        fields = ("id", "author", "recipient", "score", "comment", "created_at")
        read_only_fields = ("id", "author", "recipient", "created_at")


class RatingInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    score = serializers.IntegerField(min_value=1, max_value=5)
    comment = serializers.CharField(max_length=500, required=False, allow_blank=True, default="")


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

    class Meta:
        model = Ride
        fields = (
            "id", "request_id", "status", "quote", "rider", "driver", "payment_method", "payment",
            "cancellation_reason", "created_at", "updated_at", "accepted_at", "arrived_at",
            "started_at", "completed_at", "cancelled_at", "events", "ratings",
        )

    def person(self, user, *, contact=False):
        if not user:
            return None
        data = {"id": user.pk, "name": user.get_full_name() or user.username,
                "rating": user.received_ratings.aggregate(value=Avg("score"))["value"]}
        if contact:
            data["phone_number"] = user.phone_number
        return data

    def get_rider(self, ride):
        return self.person(ride.rider, contact=ride.status in ASSIGNED_STATUSES)

    def get_driver(self, ride):
        data = self.person(ride.driver, contact=ride.status in ASSIGNED_STATUSES)
        if data is None:
            return None
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
