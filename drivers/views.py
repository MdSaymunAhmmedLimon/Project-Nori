from django.shortcuts import get_object_or_404
from rest_framework import generics, mixins, permissions, serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import DriverProfile, Vehicle
from .permissions import IsDriver
from .serializers import DriverProfileSerializer, VehicleSerializer
from .services import set_availability, update_location


class OwnResourceView(mixins.CreateModelMixin, generics.RetrieveUpdateAPIView):
    permission_classes = [permissions.IsAuthenticated, IsDriver]
    http_method_names = ["get", "post", "patch", "head", "options"]

    def post(self, request, *args, **kwargs):
        return self.create(request, *args, **kwargs)


class DriverProfileView(OwnResourceView):
    serializer_class = DriverProfileSerializer

    def get_object(self):
        return get_object_or_404(DriverProfile, user=self.request.user)


class VehicleView(OwnResourceView):
    serializer_class = VehicleSerializer

    def get_object(self):
        return get_object_or_404(Vehicle, driver__user=self.request.user)


class AvailabilityView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDriver]

    def post(self, request):
        from accounts.serializers import WritableFieldsOnlyMixin
        class Input(WritableFieldsOnlyMixin, serializers.Serializer):
            is_online = serializers.BooleanField()
        data = Input(data=request.data)
        data.is_valid(raise_exception=True)
        profile = set_availability(request.user, **data.validated_data)
        return Response(DriverProfileSerializer(profile).data)


class LocationView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDriver]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "location"

    def post(self, request):
        from rides.serializers import CoordinatesSerializer
        data = CoordinatesSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        profile = update_location(request.user, **data.validated_data)
        return Response(DriverProfileSerializer(profile).data)
