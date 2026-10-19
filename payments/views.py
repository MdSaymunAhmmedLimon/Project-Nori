from datetime import date

import stripe
from django.conf import settings
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from drivers.permissions import IsDriver
from rides.reporting import DateRangeSerializer, apply_date_range

from .models import Payment
from .services import confirm_cash, create_checkout, process_stripe_event


class CashPayment(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDriver]

    def post(self, request, pk):
        payment = confirm_cash(request.user, pk)
        return Response({"status": payment.status, "paid_at": payment.paid_at})


class Checkout(APIView):
    def post(self, request, pk):
        return Response({"url": create_checkout(request.user, pk)})


class StripeWebhook(APIView):
    permission_classes = [permissions.AllowAny]
    authentication_classes = []
    throttle_classes = []

    def post(self, request):
        if not settings.STRIPE_WEBHOOK_SECRET:
            return Response({"detail": "Card payments are not configured."}, status=503)
        try:
            event = stripe.Webhook.construct_event(request.body, request.headers.get("Stripe-Signature", ""), settings.STRIPE_WEBHOOK_SECRET)
        except (ValueError, stripe.SignatureVerificationError):
            return Response({"detail": "Invalid webhook signature."}, status=400)
        process_stripe_event(event)
        return Response({"received": True})


class Earnings(APIView):
    permission_classes = [permissions.IsAuthenticated, IsDriver]

    def get(self, request):
        filters = DateRangeSerializer(data=request.query_params)
        filters.is_valid(raise_exception=True)
        payments = Payment.objects.filter(ride__driver=request.user, ride__status="COMPLETED")
        payments = apply_date_range(payments, filters.validated_data, "ride__completed_at")
        net = ExpressionWrapper(F("amount") - F("platform_fee"), output_field=DecimalField(max_digits=10, decimal_places=2))
        totals = list(payments.values("currency").annotate(
            trips=Count("id"), gross=Sum("amount"), platform_fees=Sum("platform_fee"), net=Sum(net),
            paid_net=Sum(net, filter=Q(status="PAID")), pending_net=Sum(net, filter=Q(status="PENDING")),
            cash_collected=Sum("amount", filter=Q(status="PAID", method="CASH")),
            card_net=Sum(net, filter=Q(status="PAID", method="CARD")),
        ))
        today = timezone.localdate()
        daily = list(apply_date_range(payments, {"date_from": today, "date_to": today}, "ride__completed_at")
                     .values("currency").annotate(trips=Count("id"), net=Sum(net)))
        # Keep the chart bounded, even for an all-time summary. Never mix currencies.
        trend_end = filters.validated_data.get("date_to", timezone.localdate())
        trend_start = date.fromordinal(max(1, trend_end.toordinal() - 30))
        breakdown = list(apply_date_range(
            payments, {"date_from": trend_start, "date_to": trend_end}, "ride__completed_at",
        ).annotate(date=TruncDate("ride__completed_at")).values("date", "currency").annotate(
            trips=Count("id"), gross=Sum("amount"), platform_fees=Sum("platform_fee"), net=Sum(net),
            paid_net=Sum(net, filter=Q(status="PAID")),
            pending_net=Sum(net, filter=Q(status="PENDING")),
        ).order_by("-date", "currency"))
        for row in totals:
            row["average_net"] = f"{row['net'] / row['trips']:.2f}"
        return Response({"totals": totals, "today": daily, "daily": breakdown,
                         "trend_from": trend_start, "trend_to": trend_end})
