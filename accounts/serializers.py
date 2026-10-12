from django.contrib.auth import get_user_model
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from rest_framework import serializers
from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.authentication import JWTAuthentication

from .services import register_user

User = get_user_model()


class WritableFieldsOnlyMixin:
    """Report forbidden/unknown input instead of silently dropping it."""

    def to_internal_value(self, data):
        if isinstance(data, dict):
            writable = {name for name, field in self.fields.items() if not field.read_only}
            invalid = set(data) - writable
            if invalid:
                raise serializers.ValidationError(
                    {name: "This field cannot be written." for name in sorted(invalid)}
                )
        return super().to_internal_value(data)


class UserSerializer(WritableFieldsOnlyMixin, serializers.ModelSerializer):
    class Meta:
        model = User
        fields = (
            "id", "username", "email", "first_name", "last_name",
            "phone_number", "role", "date_joined",
        )
        read_only_fields = ("id", "username", "email", "role", "date_joined")


class RegistrationSerializer(WritableFieldsOnlyMixin, serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    role = serializers.ChoiceField(choices=User.Role.choices, default=User.Role.RIDER)
    referral_code = serializers.CharField(required=False, allow_blank=True, default="", write_only=True)

    class Meta:
        model = User
        fields = (
            "id", "username", "email", "password", "first_name",
            "last_name", "phone_number", "role", "referral_code",
        )
        read_only_fields = ("id",)


    def validate_username(self, value):
        value = User.normalize_username(value)
        self.fields["username"].run_validators(value)
        return value

    def validate_email(self, value):
        value = User.objects.normalize_email(value)
        self.fields["email"].run_validators(value)
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("A user with this email already exists.")
        return value

    def validate(self, attrs):
        user = User(**{key: value for key, value in attrs.items() if key not in ("password", "referral_code")})
        try:

            validate_password(attrs["password"], user=user)
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": exc.messages}) from exc
        return attrs

    def create(self, validated_data):
        try:
            return register_user(**validated_data)
        except IntegrityError as exc:
            # Uniqueness may change between validation and insertion.
            raise serializers.ValidationError(
                {"detail": "That username or email is already registered."}
            ) from exc


class AccountTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs):
        try:
            # The stock refresh serializer checks activity, but not password changes.
            JWTAuthentication().get_user(self.token_class(attrs["refresh"]))
            return super().validate(attrs)
        except User.DoesNotExist as exc:
            # A token can outlive its account after an administrator deletes it.
            raise AuthenticationFailed(
                "No active account found for the given token.", code="no_active_account"
            ) from exc


class PasswordChangeSerializer(WritableFieldsOnlyMixin, serializers.Serializer):
    old_password = serializers.CharField(write_only=True, trim_whitespace=False)
    new_password1 = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    new_password2 = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)

    def validate(self, attrs):
        self.form = PasswordChangeForm(self.context["request"].user, data=attrs)
        if not self.form.is_valid():
            raise serializers.ValidationError(dict(self.form.errors))
        return attrs

    def save(self, **kwargs):
        return self.form.save()


class ReferralItemSerializer(serializers.Serializer):
    referee_username = serializers.CharField(source="referee.username")
    status = serializers.CharField()
    reward_amount = serializers.DecimalField(max_digits=10, decimal_places=2)
    created_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)

