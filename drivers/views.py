from django.db.models import Avg, Count, Sum
from django.db.models.functions import TruncDate
from django.shortcuts import get_object_or_404
from rest_framework import generics, mixins, permissions, serializers
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from .models import DriverProfile, PayoutRequest, Vehicle
from .permissions import IsDriver
from .serializers import (
    DriverDocumentSerializer, DriverProfileSerializer,
    PayoutProcessInputSerializer, PayoutRequestInputSerializer,
    PayoutRequestSerializer, VehicleSerializer,
)
from .services import (
    get_driver_balance, process_payout, request_payout,
    set_availability, update_location,
)



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


class DocumentsView(generics.ListCreateAPIView):
    """List and upload driver verification documents (multipart/form-data)."""

    permission_classes = [permissions.IsAuthenticated, IsDriver]
    serializer_class = DriverDocumentSerializer
    parser_classes_override = None  # allows multipart uploads via DRF default parsers

    def get_serializer_class(self):
        from .serializers import DriverDocumentSerializer
        return DriverDocumentSerializer

    def get_queryset(self):
        from .models import DriverDocument, DriverProfile
        profile = DriverProfile.objects.filter(user=self.request.user).first()
        if not profile:
            return DriverDocument.objects.none()
        return profile.documents.all()

    def perform_create(self, serializer):
        serializer.save()



class EarningsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDriver]

    def get(self, request):
        from payments.models import Payment
        from rides.services import require_role
        require_role(request.user, "DRIVER")

        qs = Payment.objects.filter(
            ride__driver=request.user,
            ride__status="COMPLETED",
        ).exclude(method__in=["WALLET", "SSL_DEMO"])

        # Optional date range filters
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            qs = qs.filter(created_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(created_at__date__lte=date_to)

        totals = qs.aggregate(
            total_revenue=Sum("amount"),
            total_fees=Sum("platform_fee"),
            ride_count=Count("id"),
        )
        total_revenue = totals["total_revenue"] or 0
        total_fees = totals["total_fees"] or 0
        net_earnings = float(total_revenue) - float(total_fees)

        # Per-day breakdown
        daily = list(
            qs.annotate(date=TruncDate("created_at"))
            .values("date")
            .annotate(revenue=Sum("amount"), fees=Sum("platform_fee"), rides=Count("id"))
            .order_by("date")
        )

        # Rating stats
        rating_agg = request.user.received_ratings.aggregate(
            avg_rating=Avg("score"), rating_count=Count("id")
        )

        from rides.models import RideTip
        tips_total = float(RideTip.objects.filter(ride__driver=request.user).exclude(ride__payment_method__in=["WALLET", "SSL_DEMO"]).aggregate(total=Sum("amount"))["total"] or 0)

        return Response({
            "total_revenue": f"{float(total_revenue):.2f}",
            "total_platform_fees": f"{float(total_fees):.2f}",
            "total_tips": f"{tips_total:.2f}",
            "net_earnings": f"{(net_earnings + tips_total):.2f}",
            "ride_count": totals["ride_count"] or 0,
            "avg_rating": rating_agg["avg_rating"],
            "rating_count": rating_agg["rating_count"],
            "daily_breakdown": [
                {
                    "date": str(d["date"]),
                    "revenue": str(d["revenue"]),
                    "platform_fees": str(d["fees"]),
                    "net": f"{float(d['revenue']) - float(d['fees']):.2f}",
                    "rides": d["rides"],
                }
                for d in daily
            ],
        })


class PayoutListCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDriver]

    def get(self, request):
        profile = get_object_or_404(DriverProfile, user=request.user)
        balance = get_driver_balance(request.user)
        payouts = profile.payout_requests.all()
        return Response({
            "available_balance": f"{balance:.2f}",
            "payouts": PayoutRequestSerializer(payouts, many=True).data,
        })

    def post(self, request):
        serializer = PayoutRequestInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payout = request_payout(request.user, **serializer.validated_data)
        return Response(PayoutRequestSerializer(payout).data, status=201)


class PayoutProcessView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        serializer = PayoutProcessInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        payout = process_payout(request.user, pk, **serializer.validated_data)
        return Response(PayoutRequestSerializer(payout).data)

