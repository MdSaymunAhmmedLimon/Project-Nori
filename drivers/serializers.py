from decimal import Decimal

from django.db import IntegrityError, transaction
from rest_framework import serializers


from accounts.serializers import WritableFieldsOnlyMixin

from .models import DriverDocument, DriverProfile, PayoutRequest, Vehicle
from .services import create_driver_profile, create_vehicle, require_editable_driver


class DriverProfileSerializer(WritableFieldsOnlyMixin, serializers.ModelSerializer):
    class Meta:
        model = DriverProfile
        fields = (
            "id", "user", "license_number", "license_expiry_date", "created_at", "updated_at",
            "verification_status", "review_note", "is_online", "latitude", "longitude", "location_updated_at",
        )
        read_only_fields = ("id", "user", "created_at", "updated_at", "verification_status", "review_note", "is_online", "latitude", "longitude", "location_updated_at")

    def validate_license_number(self, value):
        value = value.strip().upper()
        self.fields["license_number"].run_validators(value)
        queryset = DriverProfile.objects.filter(license_number__iexact=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("This license is already registered.")
        return value

    def create(self, validated_data):
        try:
            return create_driver_profile(user=self.context["request"].user, **validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"detail": "A profile already exists for this driver or license."}
            ) from exc

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                instance = DriverProfile.objects.select_for_update().get(pk=instance.pk)
                require_editable_driver(instance.user)
                if any(getattr(instance, key) != value for key, value in validated_data.items()):
                    instance.verification_status = "PENDING"
                    instance.is_online = False
                    instance.review_note = "Driver information changed; review required."
                return super().update(instance, validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"license_number": "This license is already registered."}
            ) from exc


class VehicleSerializer(WritableFieldsOnlyMixin, serializers.ModelSerializer):
    class Meta:
        model = Vehicle
        fields = (
            "id", "driver", "license_plate", "vehicle_class", "make", "model", "color",
            "year", "seats", "created_at", "updated_at",
        )
        read_only_fields = ("id", "driver", "created_at", "updated_at")


    def validate_license_plate(self, value):
        value = value.strip().upper()
        self.fields["license_plate"].run_validators(value)
        queryset = Vehicle.objects.filter(license_plate__iexact=value)
        if self.instance:
            queryset = queryset.exclude(pk=self.instance.pk)
        if queryset.exists():
            raise serializers.ValidationError("This license plate is already registered.")
        return value

    def create(self, validated_data):
        try:
            return create_vehicle(user=self.context["request"].user, **validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"detail": "A vehicle already exists for this driver or license plate."}
            ) from exc

    def update(self, instance, validated_data):
        try:
            with transaction.atomic():
                profile = DriverProfile.objects.select_for_update().get(pk=instance.driver_id)
                require_editable_driver(profile.user)
                if any(getattr(instance, key) != value for key, value in validated_data.items()):
                    profile.verification_status = "PENDING"
                    profile.is_online = False
                    profile.review_note = "Vehicle information changed; review required."
                    profile.save(update_fields=["verification_status", "is_online", "review_note", "updated_at"])
                return super().update(instance, validated_data)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {"license_plate": "This license plate is already registered."}
            ) from exc


class DriverDocumentSerializer(serializers.ModelSerializer):
    """Serializer for driver document uploads (multipart/form-data)."""

    class Meta:
        model = DriverDocument
        fields = (
            "id", "doc_type", "file", "review_status", "review_note",
            "uploaded_at", "reviewed_at",
        )
        read_only_fields = ("id", "review_status", "review_note", "uploaded_at", "reviewed_at")

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        try:
            profile = DriverProfile.objects.select_for_update().get(user=user)
        except DriverProfile.DoesNotExist as exc:
            raise serializers.ValidationError(
                {"detail": "Create your driver profile first."}
            ) from exc
        require_editable_driver(user)
        profile.verification_status = "PENDING"
        profile.is_online = False
        profile.review_note = "Verification documents changed; review required."
        profile.save(update_fields=["verification_status", "is_online", "review_note", "updated_at"])
        # Replace existing document of same type instead of raising integrity error
        existing = DriverDocument.objects.filter(driver=profile, doc_type=validated_data["doc_type"]).first()
        if existing:
            from django.utils import timezone
            existing.file = validated_data["file"]
            existing.uploaded_at = timezone.now()
            existing.review_status = DriverDocument.ReviewStatus.PENDING
            existing.review_note = ""
            existing.reviewed_at = None
            existing.save(update_fields=["file", "uploaded_at", "review_status", "review_note", "reviewed_at"])
            return existing
        return DriverDocument.objects.create(driver=profile, **validated_data)



class PayoutRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = PayoutRequest
        fields = (
            "id", "amount", "payout_method", "account_details",
            "status", "admin_note", "requested_at", "processed_at",
        )
        read_only_fields = ("id", "status", "admin_note", "requested_at", "processed_at")


class PayoutRequestInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    amount = serializers.DecimalField(max_digits=10, decimal_places=2, min_value=Decimal("10.00"))
    payout_method = serializers.ChoiceField(choices=PayoutRequest.Method.choices)

    account_details = serializers.CharField(max_length=200)


class PayoutProcessInputSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    action = serializers.ChoiceField(choices=["approve", "reject", "pay"])
    admin_note = serializers.CharField(max_length=300, required=False, allow_blank=True, default="")


