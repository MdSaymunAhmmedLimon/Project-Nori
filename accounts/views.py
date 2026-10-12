from django.contrib.auth import update_session_auth_hash
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters
from rest_framework import generics, permissions
from rest_framework.authentication import SessionAuthentication
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.views import (
    TokenBlacklistView,
    TokenObtainPairView,
    TokenRefreshView,
)

from .serializers import AccountTokenRefreshSerializer, PasswordChangeSerializer, RegistrationSerializer, UserSerializer


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
