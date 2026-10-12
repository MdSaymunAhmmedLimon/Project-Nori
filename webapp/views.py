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
    return render(request, "webapp/home.html")


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


@user_passes_test(lambda user: user.is_active and user.is_staff)
def operations(request):
    context = {
        "users_count": User.objects.count(),
        "active_count": Ride.objects.filter(status__in=ACTIVE_STATUSES).count(),
        "completed_count": Ride.objects.filter(status="COMPLETED").count(),
        "pending_count": DriverProfile.objects.filter(verification_status="PENDING").count(),
        "recent_rides": Ride.objects.select_related("rider", "driver", "quote")[:20],
        "revenue": Payment.objects.filter(status="PAID").values("currency").annotate(gross=Sum("amount"), fees=Sum("platform_fee")),
    }
    return render(request, "webapp/operations.html", context)
