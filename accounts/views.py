from django.contrib.auth import update_session_auth_hash
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import generics, permissions
from rest_framework.exceptions import ValidationError
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from django.db import transaction
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)

from .serializers import (
    AccountTokenRefreshSerializer, PasswordChangeSerializer,
    RegistrationSerializer, ResetPasswordWithAnswerSerializer,
    EmergencyContactInputSerializer, EmergencyContactSerializer,
    SecurityQuestionRequestSerializer, UserSerializer,
)


class AuthThrottleMixin:
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth"


class RegistrationView(AuthThrottleMixin, generics.CreateAPIView):
    serializer_class = RegistrationSerializer
    permission_classes = [permissions.AllowAny]
    authentication_classes = []


class LoginView(AuthThrottleMixin, TokenObtainPairView):
    pass


class RefreshView(AuthThrottleMixin, TokenRefreshView):
    serializer_class = AccountTokenRefreshSerializer


class LogoutView(AuthThrottleMixin, TokenBlacklistView):
    """The refresh token itself authorizes revocation, even after access expiry."""


class CurrentUserView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer
    http_method_names = ["get", "patch", "head", "options"]

    def get_object(self):
        return self.request.user


@method_decorator(sensitive_post_parameters(), name="dispatch")
class PasswordChangeView(AuthThrottleMixin, generics.GenericAPIView):
    serializer_class = PasswordChangeSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        if isinstance(request.successful_authenticator, SessionAuthentication):
            update_session_auth_hash(request, user)
        return Response({"detail": "Password updated. Sign in again on your other devices."})


class ReferralView(generics.GenericAPIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from django.db.models import Sum
        from .models import Referral, ReferralCode
        from .serializers import ReferralItemSerializer
        from .services import generate_unique_referral_code

        ref_code, _ = ReferralCode.objects.get_or_create(
            user=request.user,
            defaults={"code": generate_unique_referral_code(request.user)},
        )
        referrals = Referral.objects.filter(referrer=request.user).select_related("referee")
        completed = referrals.filter(status=Referral.Status.COMPLETED)
        total_rewards = completed.aggregate(total=Sum("reward_amount"))["total"] or 0
        return Response({
            "referral_code": ref_code.code,
            "total_referrals": referrals.count(),
            "completed_referrals": completed.count(),
            "total_rewards_earned": f"{float(total_rewards):.2f}",
            "referrals": ReferralItemSerializer(referrals, many=True).data,
        })


class EmergencyContactList(generics.ListCreateAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EmergencyContactSerializer
    pagination_class = None

    def get_queryset(self):
        from .models import EmergencyContact
        return EmergencyContact.objects.filter(user=self.request.user)

    @transaction.atomic
    def create(self, request, *args, **kwargs):
        from .models import EmergencyContact
        from django.contrib.auth import get_user_model

        get_user_model().objects.select_for_update().get(pk=request.user.pk)
        if EmergencyContact.objects.filter(user=request.user).count() >= 5:
            raise ValidationError({"detail": "You can save up to five emergency contacts."})
        serializer = EmergencyContactInputSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        contact = EmergencyContact.objects.create(user=request.user, **serializer.validated_data)
        return Response(EmergencyContactSerializer(contact).data, status=201)


class EmergencyContactDetail(generics.DestroyAPIView):
    permission_classes = [permissions.IsAuthenticated]
    serializer_class = EmergencyContactSerializer
    http_method_names = ["delete", "head", "options"]

    def get_queryset(self):
        from .models import EmergencyContact
        return EmergencyContact.objects.filter(user=self.request.user)


class SecurityQuestionView(AuthThrottleMixin, generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = SecurityQuestionRequestSerializer

    def get(self, request):
        username = request.query_params.get("username", "").strip()
        if not username:
            return Response({"username": "Username query parameter is required."}, status=400)
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.filter(username__iexact=username).first()
        if not user or not user.security_question:
            return Response({"detail": "User not found or no security question is set for this account."}, status=404)
        return Response({
            "username": user.username,
            "security_question": user.security_question,
        })

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        username = serializer.validated_data["username"]
        from django.contrib.auth import get_user_model
        User = get_user_model()
        user = User.objects.filter(username__iexact=username).first()
        if not user or not user.security_question:
            return Response({"detail": "User not found or no security question is set for this account."}, status=404)
        return Response({
            "username": user.username,
            "security_question": user.security_question,
        })


@method_decorator(sensitive_post_parameters(), name="dispatch")
class ResetWithSecurityAnswerView(AuthThrottleMixin, generics.GenericAPIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    serializer_class = ResetPasswordWithAnswerSerializer

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response({"detail": "Password has been reset successfully."})


