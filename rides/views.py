from django.db.models import Q
from django.utils import timezone
from rest_framework import generics
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import ACTIVE_STATUSES, Ride, RideOffer
from .serializers import (
    ActionSerializer, OfferSerializer, QuoteInputSerializer, QuoteSerializer,
    RatingInputSerializer, RatingSerializer, RequestRideSerializer, RideSerializer,
)


def own_rides(user):
    return Ride.objects.filter(Q(rider=user) | Q(driver=user)).select_related("quote", "rider", "driver", "payment").prefetch_related("events", "ratings")


class QuoteView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def post(self, request):
        serializer = QuoteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response(QuoteSerializer(services.create_quote(request.user, serializer.validated_data)).data, status=201)


class RideList(generics.ListAPIView):
    serializer_class = RideSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def get_queryset(self):
        queryset = own_rides(self.request.user)
        if self.request.query_params.get("status"):
            queryset = queryset.filter(status=self.request.query_params["status"])
        return queryset

    def post(self, request):
        serializer = RequestRideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ride, created = services.request_ride(request.user, **serializer.validated_data)
        return Response(RideSerializer(ride).data, status=201 if created else 200)


class CurrentRide(APIView):
    def get(self, request):
        services.expire_searches(request.user)
        ride = own_rides(request.user).filter(status__in=ACTIVE_STATUSES).first()
        if ride and ride.status == "SEARCHING":
            services.dispatch_ride(ride.pk)
        return Response(RideSerializer(ride).data if ride else None)


class RideDetail(generics.RetrieveAPIView):
    serializer_class = RideSerializer

    def get_queryset(self):
        return own_rides(self.request.user)


class RideAction(APIView):
    def post(self, request, pk):
        serializer = ActionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ride = services.transition_ride(request.user, pk, **serializer.validated_data)
        return Response(RideSerializer(ride).data)


class RateRide(APIView):
    def post(self, request, pk):
        serializer = RatingInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        rating = services.rate_ride(request.user, pk, **serializer.validated_data)
        return Response(RatingSerializer(rating).data, status=201)


class Tracking(RideDetail):
    def get(self, request, *args, **kwargs):
        ride = self.get_object()
        points = list(ride.track_points.order_by("-recorded_at").values("latitude", "longitude", "recorded_at")[:1000])
        return Response({"ride_id": str(ride.pk), "points": list(reversed(points))})


class Offers(generics.ListAPIView):
    serializer_class = OfferSerializer

    def get_queryset(self):
        services.require_role(self.request.user, "DRIVER")
        from drivers.models import DriverProfile
        profile = DriverProfile.objects.select_related("user", "vehicle").filter(user=self.request.user).first()
        if not profile or not services.driver_is_eligible(profile):
            return RideOffer.objects.none()
        services.dispatch_waiting()
        return RideOffer.objects.filter(
            driver=self.request.user, rejected_at__isnull=True, expires_at__gt=timezone.now(),
            ride__status="SEARCHING",
        ).select_related("ride__quote").order_by("distance_km")


class OfferAction(APIView):
    def post(self, request, pk, action):
        if action == "accept":
            return Response(RideSerializer(services.accept_offer(request.user, pk)).data)
        services.reject_offer(request.user, pk)
        return Response({"detail": "Offer rejected."})
