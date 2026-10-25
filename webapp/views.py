from django.conf import settings
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.views import LoginView
from django.core.cache import cache
from django.db.models import Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_http_methods
from rest_framework.exceptions import ValidationError

from accounts.models import User
from drivers.models import DriverProfile
from payments.models import Payment
from rides.models import ACTIVE_STATUSES, Ride

from .forms import RegistrationForm


def auth_limited(request):
    key = f"web_auth:{request.META.get('REMOTE_ADDR', '')}"
    if cache.add(key, 1, timeout=60):
        return False
    try:
        return cache.incr(key) > 30
    except ValueError:
        cache.set(key, 1, timeout=60)
        return False


class WebLoginView(LoginView):
    template_name = "webapp/login.html"
    redirect_authenticated_user = True

    def post(self, request, *args, **kwargs):
        if auth_limited(request):
            return render(request, self.template_name, {"form": self.get_form(), "rate_error": "Too many attempts. Please wait a minute."}, status=429)
        return super().post(request, *args, **kwargs)


def home(request):
    return render(request, "webapp/home.html", {"public_map": {
        "tileUrl": settings.MAP_TILE_URL, "attribution": settings.MAP_ATTRIBUTION,
        "latitude": settings.MAP_CENTER_LATITUDE, "longitude": settings.MAP_CENTER_LONGITUDE,
    }})


@require_http_methods(["GET", "POST"])
def register(request):
    if request.user.is_authenticated:
        return redirect("webapp:dashboard")
    form = RegistrationForm(request.POST or None, initial={"role": "DRIVER" if request.GET.get("role") == "driver" else "RIDER"})
    if request.method == "POST":
        if auth_limited(request):
            return render(request, "webapp/register.html", {"form": form, "rate_error": "Too many attempts. Please wait a minute."}, status=429)
        if form.is_valid():
            try:
                user = form.save()
            except ValidationError:
                form.add_error(None, "That username or email was just registered. Please choose another.")
            else:
                login(request, user)
                return redirect("webapp:dashboard")
    return render(request, "webapp/register.html", {"form": form})


@login_required
@ensure_csrf_cookie
def dashboard(request):
    config = {
        "user": {"id": request.user.pk, "role": request.user.role, "name": request.user.get_full_name() or request.user.username},
        "currency": settings.RIDE_CURRENCY, "cardPayments": settings.CARD_PAYMENTS_ENABLED,
        "map": {"tileUrl": settings.MAP_TILE_URL, "attribution": settings.MAP_ATTRIBUTION,
                "latitude": settings.MAP_CENTER_LATITUDE, "longitude": settings.MAP_CENTER_LONGITUDE},
    }
    return render(request, "webapp/dashboard.html", {"app_config": config})


@login_required
@never_cache
@require_http_methods(["GET", "HEAD"])
def receipt(request, ride_id):
    payment = get_object_or_404(
        Payment.objects.select_related("ride__quote", "ride__rider", "ride__driver").filter(
            Q(ride__rider=request.user) | Q(ride__driver=request.user),
            ride__status=Ride.Status.COMPLETED,
        ),
        ride_id=ride_id,
    )
    return render(request, "webapp/receipt.html", {
        "payment": payment, "ride": payment.ride,
        "driver_copy": request.user.pk == payment.ride.driver_id,
        "driver_net": payment.amount - payment.platform_fee,
    })


@never_cache
@require_http_methods(["GET", "HEAD"])
def shared_trip(request, token):
    return render(request, "webapp/shared_trip.html", {"share_token": token})


@user_passes_test(lambda user: user.is_active and user.is_staff)
def operations(request):
    from rides.models import LostItem, SOSAlert, SurgePricingRule, Ride, ACTIVE_STATUSES
    from drivers.models import DriverProfile, DriverDocument
    from payments.models import Payment
    from accounts.models import User
    from django.db.models import Avg, Count, Q, Sum, F
    from django.db.models.functions import TruncDate
    from django.utils import timezone
    import datetime

    # Existing metrics
    user_counts = User.objects.aggregate(total=Count("id"), drivers=Count("id", filter=Q(role="DRIVER")))
    ride_counts = Ride.objects.aggregate(
        total=Count("id"), active=Count("id", filter=Q(status__in=ACTIVE_STATUSES)),
        completed=Count("id", filter=Q(status="COMPLETED")),
        cancelled=Count("id", filter=Q(status="CANCELLED")),
        rider_cancelled=Count("id", filter=Q(status="CANCELLED", cancelled_by__role="RIDER")),
        driver_cancelled=Count("id", filter=Q(status="CANCELLED", cancelled_by__role="DRIVER")),
    )
    driver_counts = DriverProfile.objects.aggregate(
        pending=Count("id", filter=Q(verification_status="PENDING")),
        online=Count("id", filter=Q(is_online=True)),
        approved=Count("id", filter=Q(verification_status="APPROVED")),
    )
    users_count = user_counts["total"]
    active_count = ride_counts["active"]
    completed_count = ride_counts["completed"]
    pending_count = driver_counts["pending"]
    recent_rides = Ride.objects.select_related("rider", "driver", "quote")[:20]
    revenue = Payment.objects.filter(status="PAID").exclude(method__in=["WALLET", "SSL_DEMO"]).values("currency").annotate(gross=Sum("amount"), fees=Sum("platform_fee"))

    # NEW: SOS Alerts
    sos_alerts = SOSAlert.objects.select_related("ride__rider", "ride__driver", "raised_by").order_by("-created_at")[:20]
    unresolved_sos = SOSAlert.objects.filter(resolved_at__isnull=True).count()

    # NEW: Driver verification queue
    pending_drivers = DriverProfile.objects.filter(
        verification_status="PENDING"
    ).select_related("user", "vehicle").prefetch_related("documents").order_by("created_at")[:20]

    # NEW: Revenue analytics (last 30 days daily breakdown)
    thirty_days_ago = timezone.now() - datetime.timedelta(days=30)
    daily_revenue = list(
        Payment.objects.filter(status="PAID", created_at__gte=thirty_days_ago).exclude(method__in=["WALLET", "SSL_DEMO"])
        .annotate(date=TruncDate("created_at"))
        .values("date")
        .annotate(
            total=Sum("amount"),
            fees=Sum("platform_fee"),
            cash=Sum("amount", filter=Q(method="CASH")),
            card=Sum("amount", filter=Q(method="CARD")),
            count=Count("id"),
        )
        .order_by("date")
    )
    # Find max for chart scaling
    max_daily = max((d["total"] or 0 for d in daily_revenue), default=0) if daily_revenue else 0
    max_daily_val = float(max_daily) if max_daily else 0
    for day in daily_revenue:
        tot = float(day["total"] or 0)
        day["percent"] = round((tot / max_daily_val * 100), 1) if max_daily_val > 0 else 0

    # 30-day totals
    revenue_30d_total = sum((d["total"] or 0 for d in daily_revenue), 0)
    revenue_30d_fees = sum((d["fees"] or 0 for d in daily_revenue), 0)
    revenue_30d_cash = sum((d["cash"] or 0 for d in daily_revenue), 0)
    revenue_30d_card = sum((d["card"] or 0 for d in daily_revenue), 0)

    # NEW: Cancellation analytics
    total_rides = ride_counts["total"]
    cancelled_rides = Ride.objects.filter(status="CANCELLED")
    cancelled_count = ride_counts["cancelled"]
    cancellation_rate = round((cancelled_count / total_rides * 100), 1) if total_rides else 0
    rider_cancelled = ride_counts["rider_cancelled"]
    driver_cancelled = ride_counts["driver_cancelled"]
    recent_cancellations = cancelled_rides.select_related("rider", "driver", "quote").order_by("-cancelled_at")[:10]
    # Top cancellation reasons
    cancel_reasons = list(
        cancelled_rides.exclude(cancellation_reason="")
        .values("cancellation_reason")
        .annotate(count=Count("id"))
        .order_by("-count")[:5]
    )

    # NEW: Surge rules
    surge_rules = SurgePricingRule.objects.all()[:20]

    # NEW: Platform totals
    total_drivers = user_counts["drivers"]
    online_drivers = driver_counts["online"]
    approved_drivers = driver_counts["approved"]

    context = {
        "open_lost_items": LostItem.objects.exclude(status__in=["RETURNED", "NOT_FOUND"]).count(),
        "users_count": users_count,
        "active_count": active_count,
        "completed_count": completed_count,
        "pending_count": pending_count,
        "recent_rides": recent_rides,
        "revenue": revenue,
        "sos_alerts": sos_alerts,
        "unresolved_sos": unresolved_sos,
        "pending_drivers": pending_drivers,
        "daily_revenue": daily_revenue,
        "max_daily": float(max_daily) if max_daily else 1,
        "revenue_30d_total": revenue_30d_total,
        "revenue_30d_fees": revenue_30d_fees,
        "revenue_30d_cash": revenue_30d_cash,
        "revenue_30d_card": revenue_30d_card,
        "cancelled_count": cancelled_count,
        "cancellation_rate": cancellation_rate,
        "rider_cancelled": rider_cancelled,
        "driver_cancelled": driver_cancelled,
        "recent_cancellations": recent_cancellations,
        "cancel_reasons": cancel_reasons,
        "surge_rules": surge_rules,
        "total_drivers": total_drivers,
        "online_drivers": online_drivers,
        "approved_drivers": approved_drivers,
    }
    return render(request, "webapp/operations.html", context)

