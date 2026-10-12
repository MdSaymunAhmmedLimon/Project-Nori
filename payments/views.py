import stripe
from django.conf import settings
from django.db.models import Count, DecimalField, ExpressionWrapper, F, Q, Sum
from django.utils import timezone
from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from drivers.permissions import IsDriver

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
        payments = Payment.objects.filter(ride__driver=request.user, ride__status="COMPLETED")
        net = ExpressionWrapper(F("amount") - F("platform_fee"), output_field=DecimalField(max_digits=10, decimal_places=2))
        totals = list(payments.values("currency").annotate(
            trips=Count("id"), gross=Sum("amount"), platform_fees=Sum("platform_fee"), net=Sum(net),
            paid_net=Sum(net, filter=Q(status="PAID")), pending_net=Sum(net, filter=Q(status="PENDING")),
            cash_collected=Sum("amount", filter=Q(status="PAID", method="CASH")),
            card_net=Sum(net, filter=Q(status="PAID", method="CARD")),
        ))
        daily = list(payments.filter(ride__completed_at__date=timezone.localdate()).values("currency").annotate(trips=Count("id"), net=Sum(net)))
        return Response({"totals": totals, "today": daily})
