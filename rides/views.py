from django.db.models import Avg, Count, F, Q, Sum
from django.utils import timezone
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import (
    ACTIVE_STATUSES, CarpoolPassenger, CarpoolRide, Ride, RideOffer,
    SavedPlace, ScheduledRide, SOSAlert,
)
from .serializers import (
    ActionSerializer, CarpoolCreateSerializer, CarpoolJoinSerializer,
    CarpoolPassengerSerializer, CarpoolRideSerializer, OfferSerializer,
    PromoValidateSerializer, QuoteInputSerializer, QuoteSerializer,
    RatingInputSerializer, RatingSerializer, RequestRideSerializer,
    RideMessageInputSerializer, RideMessageSerializer, RideSerializer,
    RideTipInputSerializer, RideTipSerializer, SavedPlaceInputSerializer,
    SavedPlaceSerializer, ScheduleRideInputSerializer,
    ScheduledRideSerializer, SOSAlertSerializer, SOSInputSerializer,
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


class RiderStats(APIView):
    def get(self, request):
        from payments.models import Payment
        services.require_role(request.user, "RIDER")
        completed = Ride.objects.filter(rider=request.user, status=Ride.Status.COMPLETED)
        totals = completed.aggregate(ride_count=Count("id"))
        payment_agg = Payment.objects.filter(
            ride__rider=request.user, ride__status="COMPLETED"
        ).aggregate(total_spent=Sum("amount"))
        rating_agg = request.user.given_ratings.aggregate(avg_given=Avg("score"))

        # Top 5 favourite routes (pickup_label -> destination_label)
        routes = (
            completed
            .values(pickup=F("quote__pickup_label"), destination=F("quote__destination_label"))
            .annotate(count=Count("id"))
            .order_by("-count")[:5]
        )

        return Response({
            "total_rides": totals["ride_count"],
            "total_spent": f"{float(payment_agg['total_spent'] or 0):.2f}",
            "avg_rating_given": rating_agg["avg_given"],

            "favourite_routes": list(routes),
        })


class ScheduledRideList(generics.ListAPIView):
    serializer_class = ScheduledRideSerializer

    def get_queryset(self):
        return ScheduledRide.objects.filter(rider=self.request.user).select_related("quote", "ride")

    def post(self, request):
        s = ScheduleRideInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        sr = services.schedule_ride(request.user, **s.validated_data)
        return Response(ScheduledRideSerializer(sr).data, status=201)


class ScheduledRideDetail(APIView):
    def delete(self, request, pk):
        sr = services.cancel_scheduled_ride(request.user, pk)
        return Response(ScheduledRideSerializer(sr).data)


class ValidatePromo(APIView):
    def post(self, request):
        s = PromoValidateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        promo, discounted_fare, discount = services.validate_promo(
            request.user, s.validated_data["code"], s.validated_data["fare"]
        )
        return Response({
            "code": promo.code,
            "description": promo.description,
            "discount_applied": str(discount),
            "discounted_fare": str(discounted_fare),
        })


class SOSView(APIView):
    def get(self, request, pk):
        ride = own_rides(request.user).filter(pk=pk).first()
        if not ride:
            raise NotFound()
        alerts = SOSAlert.objects.filter(ride=ride)
        return Response(SOSAlertSerializer(alerts, many=True).data)

    def post(self, request, pk):
        s = SOSInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        alert = services.raise_sos(request.user, pk, **s.validated_data)
        return Response(SOSAlertSerializer(alert).data, status=201)


class SOSResolve(APIView):
    def post(self, request, pk):
        alert = services.resolve_sos(request.user, pk)
        return Response(SOSAlertSerializer(alert).data)


class CarpoolList(generics.ListAPIView):
    serializer_class = CarpoolRideSerializer

    def get_queryset(self):
        return CarpoolRide.objects.filter(
            is_open=True,
            available_seats__gt=0,
            ride__status__in=ACTIVE_STATUSES,
        ).select_related("ride__quote").prefetch_related("passengers")


class EnableCarpool(APIView):
    def post(self, request, pk):
        s = CarpoolCreateSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        carpool = services.create_carpool(request.user, pk, **s.validated_data)
        return Response(CarpoolRideSerializer(carpool).data, status=201)


class JoinCarpool(APIView):
    def post(self, request, pk):
        s = CarpoolJoinSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        passenger = services.join_carpool(request.user, pk, **s.validated_data)
        return Response(CarpoolPassengerSerializer(passenger).data, status=201)


class LeaveCarpool(APIView):
    def post(self, request, pk):
        passenger = services.leave_carpool(request.user, pk)
        return Response(CarpoolPassengerSerializer(passenger).data)


class RideTipView(APIView):
    def post(self, request, pk):
        serializer = RideTipInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        tip = services.tip_ride(request.user, pk, serializer.validated_data["amount"])
        return Response(RideTipSerializer(tip).data, status=201)


class RideMessagesView(APIView):
    def get(self, request, pk):
        ride = own_rides(request.user).filter(pk=pk).first()
        if not ride:
            raise NotFound()
        return Response(RideMessageSerializer(ride.messages.all(), many=True).data)

    def post(self, request, pk):
        serializer = RideMessageInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        msg = services.send_ride_message(request.user, pk, serializer.validated_data["content"])
        return Response(RideMessageSerializer(msg).data, status=201)


class SavedPlacesView(generics.ListCreateAPIView):
    serializer_class = SavedPlaceSerializer

    def get_queryset(self):
        return SavedPlace.objects.filter(user=self.request.user)

    def post(self, request):
        serializer = SavedPlaceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        place = services.create_saved_place(request.user, **serializer.validated_data)
        return Response(SavedPlaceSerializer(place).data, status=201)


class SavedPlaceDetailView(APIView):
    def delete(self, request, pk):
        services.delete_saved_place(request.user, pk)
        return Response({"detail": "Saved place deleted."})


class LiveETAView(APIView):
    def get(self, request, pk):
        eta_data = services.get_ride_eta(request.user, pk)
        return Response(eta_data)


