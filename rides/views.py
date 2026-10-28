import csv
from datetime import timedelta

from django.conf import settings
from django.db.models import Avg, Count, F, Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from rest_framework import generics
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services, support
from .models import (
    ACTIVE_STATUSES, CarpoolRide, FavouriteDriver, LostItem, Ride, RideOffer,
    SavedPlace, ScheduledRide, SOSAlert, TripShareToken,
)
from .reporting import filter_history
from .queries import with_ride_details
from .serializers import (
    ActionSerializer, CarpoolCreateSerializer, CarpoolJoinSerializer,
    CarpoolPassengerSerializer, CarpoolRideSerializer, OfferSerializer,
    FavouriteDriverSerializer, LostItemInputSerializer, LostItemSerializer,
    PlaceItemSerializer, PromoValidateSerializer, QuoteInputSerializer,
    QuoteSerializer, RatingInputSerializer, RatingSerializer, RouteInputSerializer,
    RequestRideSerializer, RescheduleRideInputSerializer, RideMessageInputSerializer, RideMessageSerializer,
    RideSerializer, RideTipInputSerializer, RideTipSerializer,
    SavedPlaceInputSerializer, SavedPlaceSerializer,
    ScheduleRideInputSerializer, ScheduledRideSerializer, SOSAlertSerializer,
    SOSInputSerializer,
)


def own_rides(user):
    return Ride.objects.filter(Q(rider=user) | Q(driver=user))


class PlaceSearchView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def get(self, request):
        from .places import search_places
        q = request.query_params.get("q", "")
        results = search_places(q, user=request.user)
        return Response(PlaceItemSerializer(results, many=True).data)


class RouteView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def post(self, request):
        from .routing import get_road_route
        serializer = RouteInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        points = serializer.validated_data["coordinates"]
        return Response(get_road_route([(p["latitude"], p["longitude"]) for p in points]))


class QuoteView(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def post(self, request):
        serializer = QuoteInputSerializer(data=request.data, context={"request": request})
        serializer.is_valid(raise_exception=True)
        return Response(QuoteSerializer(services.create_quote(request.user, serializer.validated_data)).data, status=201)



class RideList(generics.ListAPIView):
    serializer_class = RideSerializer
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def get_queryset(self):
        return with_ride_details(filter_history(own_rides(self.request.user), self.request.query_params))

    def post(self, request):
        serializer = RequestRideSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        ride, created = services.request_ride(request.user, **serializer.validated_data)
        return Response(RideSerializer(ride).data, status=201 if created else 200)


class RideExport(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def get(self, request):
        # Avoid event/rating prefetches: a report only needs the route and payment.
        rides = filter_history(
            Ride.objects.filter(Q(rider=request.user) | Q(driver=request.user))
            .select_related("quote", "payment"), request.query_params,
        )
        rows = list(rides[:5001])
        if len(rows) > 5000:
            return Response({"detail": "Reports support up to 5,000 trips. Narrow the date range and try again."}, status=400)
        response = HttpResponse(content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="nori-trips.csv"'
        add_never_cache_headers(response)
        response.write("\ufeff")  # Preserve place names in spreadsheet applications.
        writer = csv.writer(response)
        writer.writerow([
            "Trip ID", "Requested at (ISO 8601)", "Completed at (ISO 8601)",
            "Pickup", "Destination", "Status", "Payment method", "Currency",
            "Quoted fare", "Final fare", "Payment status", "Cancellation fee",
        ])
        for ride in rows:
            payment = getattr(ride, "payment", None)
            writer.writerow([
                str(ride.pk), ride.created_at.isoformat(),
                ride.completed_at.isoformat() if ride.completed_at else "",
                csv_text(ride.quote.pickup_label), csv_text(ride.quote.destination_label),
                ride.get_status_display(), ride.payment_method,
                payment.currency if payment else ride.quote.currency,
                ride.quote.fare, payment.amount if payment else "",
                payment.status if payment else "", ride.cancellation_fee,
            ])
        if len(response.content) > 4_000_000:
            return Response({"detail": "This report is too large. Narrow the date range and try again."}, status=400)
        return response


def csv_text(value):
    # Quoting alone does not prevent spreadsheet formula execution.
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        return "'" + value
    return value


class CurrentRide(APIView):
    def get(self, request):
        if request.user.role == "RIDER":
            services.dispatch_due_scheduled_rides(user=request.user, limit=1)
        services.expire_searches(request.user)
        ride = with_ride_details(own_rides(request.user)).filter(status__in=ACTIVE_STATUSES).first()
        if ride and ride.status == "SEARCHING":
            services.dispatch_ride(ride.pk)
        return Response(RideSerializer(ride).data if ride else None)


class RideDetail(generics.RetrieveAPIView):
    serializer_class = RideSerializer

    def get_queryset(self):
        return with_ride_details(own_rides(self.request.user))


class RideAction(APIView):
    def get(self, request, pk):
        return Response(services.cancellation_preview(request.user, pk))

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
    def get_queryset(self):
        return own_rides(self.request.user).only("id")

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
        ).select_related("ride__quote").prefetch_related("ride__quote__stops").order_by("distance_km", "pk")


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
        ).exclude(method__in=["WALLET", "SSL_DEMO"]).aggregate(total_spent=Sum("amount"))
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
        queryset = ScheduledRide.objects.filter(rider=self.request.user).select_related("quote").prefetch_related("quote__stops")
        if self.request.query_params.get("pending") == "1":
            queryset = queryset.filter(cancelled_at__isnull=True, ride__isnull=True)
        return queryset.order_by("scheduled_for", "pk")

    def post(self, request):
        s = ScheduleRideInputSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        sr = services.schedule_ride(request.user, **s.validated_data)
        return Response(ScheduledRideSerializer(sr).data, status=201)


class ScheduledRideDetail(APIView):
    def patch(self, request, pk):
        serializer = RescheduleRideInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        sr = services.reschedule_ride(request.user, pk, **serializer.validated_data)
        return Response(ScheduledRideSerializer(sr).data)

    def delete(self, request, pk):
        sr = services.cancel_scheduled_ride(request.user, pk)
        return Response(ScheduledRideSerializer(sr).data)


class ScheduledRideCalendar(APIView):
    def get(self, request, pk):
        from .calendar import reservation_calendar

        sr = ScheduledRide.objects.filter(pk=pk, rider=request.user).select_related("quote").first()
        if not sr:
            raise NotFound("Scheduled ride not found.")
        if sr.cancelled_at or sr.ride_id or sr.scheduled_for <= timezone.now():
            raise services.Conflict("Calendar reminders are available for upcoming reservations.")
        response = HttpResponse(reservation_calendar(sr), content_type="text/calendar; charset=utf-8")
        response["Content-Disposition"] = f'attachment; filename="nori-reservation-{sr.pk}.ics"'
        add_never_cache_headers(response)
        return response


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
        from django.conf import settings
        if not getattr(settings, "ALLOW_MULTIPLE_RIDERS_PER_TRIP", False):
            return CarpoolRide.objects.none()
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
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "messages"

    def get(self, request, pk):
        ride = own_rides(request.user).only("id").filter(pk=pk).first()
        if not ride:
            raise NotFound()
        return Response(RideMessageSerializer(ride.messages.select_related("sender"), many=True).data)

    def post(self, request, pk):
        serializer = RideMessageInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        msg = services.send_ride_message(request.user, pk, serializer.validated_data["content"])
        return Response(RideMessageSerializer(msg).data, status=201)


class SavedPlacesView(generics.ListCreateAPIView):
    serializer_class = SavedPlaceSerializer

    def get_queryset(self):
        return SavedPlace.objects.filter(user=self.request.user).order_by("-created_at", "-id")

    def post(self, request):
        serializer = SavedPlaceInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        place = services.create_saved_place(request.user, **serializer.validated_data)
        return Response(SavedPlaceSerializer(place).data, status=201)


class SavedPlaceDetailView(APIView):
    def patch(self, request, pk):
        serializer = SavedPlaceInputSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        place = services.update_saved_place(request.user, pk, serializer.validated_data)
        return Response(SavedPlaceSerializer(place).data)

    def delete(self, request, pk):
        services.delete_saved_place(request.user, pk)
        return Response({"detail": "Saved place deleted."})


class FavouriteDriverList(generics.ListAPIView):
    serializer_class = FavouriteDriverSerializer

    def get_queryset(self):
        services.require_role(self.request.user, "RIDER")
        return FavouriteDriver.objects.filter(rider=self.request.user).select_related("driver").annotate(
            average_rating=Avg("driver__received_ratings__score"),
        ).order_by("-created_at", "-id")


class SaveFavouriteDriver(APIView):
    def post(self, request, pk):
        favourite, created = support.save_favourite_driver(request.user, pk)
        return Response(FavouriteDriverSerializer(favourite).data, status=201 if created else 200)


class FavouriteDriverDetail(APIView):
    def delete(self, request, pk):
        support.remove_favourite_driver(request.user, pk)
        return Response(status=204)


class LostItemList(generics.ListAPIView):
    serializer_class = LostItemSerializer

    def get_queryset(self):
        services.require_role(self.request.user, "RIDER")
        return LostItem.objects.filter(reporter=self.request.user).select_related("ride__quote").order_by("-created_at", "-id")


class ReportLostItem(APIView):
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def post(self, request, pk):
        serializer = LostItemInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        report, created = support.report_lost_item(request.user, pk, serializer.validated_data["description"])
        return Response(LostItemSerializer(report).data, status=201 if created else 200)


class LiveETAView(APIView):
    def get(self, request, pk):
        eta_data = services.get_ride_eta(request.user, pk)
        return Response(eta_data)


class TripShare(APIView):
    def post(self, request, pk):
        share = services.create_trip_share(request.user, pk)
        from .serializers import TripShareSerializer
        return Response(TripShareSerializer(share, context={"request": request}).data, status=201)


class TripShareDetail(APIView):
    def delete(self, request, pk):
        services.revoke_trip_share(request.user, pk)
        return Response({"detail": "Trip sharing stopped."})


class PublicTripShare(APIView):
    authentication_classes = []
    permission_classes = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "rides"

    def get(self, request, token):
        share = TripShareToken.objects.select_related(
            "ride__quote", "ride__driver__driver_profile__vehicle",
        ).filter(token=token, expires_at__gt=timezone.now()).first()
        if not share:
            raise NotFound("This trip link has expired or was revoked.")
        ride = share.ride
        data = {
            "status": ride.status,
            "pickup": ride.quote.pickup_label,
            "destination": ride.quote.destination_label,
            "updated_at": ride.updated_at,
            "expires_at": share.expires_at,
            "driver": None,
            "location": None,
        }
        if ride.driver_id:
            driver = ride.driver
            profile = getattr(driver, "driver_profile", None)
            data["driver"] = {
                "name": driver.get_full_name() or driver.username,
                "vehicle": None,
            }
            if profile and hasattr(profile, "vehicle"):
                vehicle = profile.vehicle
                data["driver"]["vehicle"] = {
                    "make": vehicle.make, "model": vehicle.model,
                    "color": vehicle.color, "license_plate": vehicle.license_plate,
                }
            if (ride.status in ACTIVE_STATUSES and profile and profile.latitude is not None
                    and profile.location_updated_at
                    and profile.location_updated_at >= timezone.now() - timedelta(seconds=settings.LOCATION_MAX_AGE_SECONDS)):
                data["location"] = {
                    "latitude": str(profile.latitude), "longitude": str(profile.longitude),
                    "updated_at": profile.location_updated_at,
                }
        return Response(data)


