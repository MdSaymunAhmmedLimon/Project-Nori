"""Session-authenticated administration pages and CSRF-protected staff actions."""

from functools import wraps

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.admin.models import LogEntry
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST
from rest_framework.exceptions import APIException

from accounts.models import User
from accounts.access import can_view, is_operator
from drivers.models import DriverDocument, DriverProfile, PayoutRequest
from drivers.review import approval_errors, audit, review_document, review_driver, review_version
from drivers.services import process_payout
from notifications.services import notify
from rides.models import ACTIVE_STATUSES, LostItem, Ride, SOSAlert
from rides.services import Conflict, resolve_sos
from rides.support import update_lost_item


def staff_page(view):
    @wraps(view)
    @login_required
    @never_cache
    def wrapped(request, *args, **kwargs):
        if not is_operator(request.user):
            raise PermissionDenied
        return view(request, *args, **kwargs)
    return wrapped


def require_permission(request, permission):
    if not request.user.has_perm(permission):
        raise PermissionDenied("Your staff account needs permission for this action.")


class ReviewForm(forms.Form):
    decision = forms.ChoiceField(choices=DriverProfile.Verification.choices)
    note = forms.CharField(max_length=300, required=False)
    expected_version = forms.CharField(min_length=64, max_length=64)


def action_result(request, callback, destination, **kwargs):
    try:
        with transaction.atomic():
            callback()
    except APIException as error:
        if error.status_code == 403:
            raise PermissionDenied from error
        if error.status_code == 404:
            raise Http404 from error
        detail = error.detail
        if isinstance(detail, list):
            detail = " ".join(str(item) for item in detail)
        messages.error(request, str(detail))
    else:
        messages.success(request, "Changes saved.")
    return redirect(destination, **kwargs)


@staff_page
@require_GET
def listing(request, section):
    required = {"drivers": "drivers.view_driverprofile", "users": "accounts.view_user", "rides": "rides.view_ride", "payouts": "drivers.view_payoutrequest", "support": "rides.view_lostitem" if request.GET.get("kind") == "lost" else "rides.view_sosalert"}
    if section in required and not can_view(request.user, required[section]):
        raise PermissionDenied
    query = request.GET.get("q", "").strip()[:150]
    selected = request.GET.get("status", "")
    context = {"section": section, "q": query, "selected_status": selected, "currency": settings.RIDE_CURRENCY}
    if section == "drivers":
        rows = DriverProfile.objects.select_related("user", "vehicle").order_by("created_at", "pk")
        context.update(title="Driver applications", subtitle="Review drivers before they can accept rides.", statuses=DriverProfile.Verification.choices)
        if "status" not in request.GET:
            selected = "PENDING"
        if selected in DriverProfile.Verification.values:
            rows = rows.filter(verification_status=selected)
        if query:
            rows = rows.filter(Q(user__username__icontains=query) | Q(user__email__icontains=query) | Q(license_number__icontains=query) | Q(vehicle__license_plate__icontains=query))
    elif section == "users":
        rows = User.objects.order_by("-date_joined", "-pk")
        context.update(title="Users & access", subtitle="Find riders and drivers and manage account access.", statuses=[("active", "Active"), ("inactive", "Inactive"), ("RIDER", "Riders"), ("DRIVER", "Drivers")])
        if selected in {"active", "inactive"}:
            rows = rows.filter(is_active=selected == "active")
        elif selected in User.Role.values:
            rows = rows.filter(role=selected)
        if query:
            rows = rows.filter(Q(username__icontains=query) | Q(email__icontains=query) | Q(phone_number__icontains=query))
    elif section == "rides":
        rows = Ride.objects.select_related("rider", "driver", "quote").order_by("-created_at", "-pk")
        context.update(title="Ride activity", subtitle="Monitor trips, participants, fares, and ride status.", statuses=[("active", "Active rides"), *Ride.Status.choices])
        if selected == "active":
            rows = rows.filter(status__in=ACTIVE_STATUSES)
        elif selected in Ride.Status.values:
            rows = rows.filter(status=selected)
        if query:
            rows = rows.filter(Q(rider__username__icontains=query) | Q(driver__username__icontains=query) | Q(quote__pickup_label__icontains=query) | Q(quote__destination_label__icontains=query))
    elif section == "payouts":
        rows = PayoutRequest.objects.select_related("driver__user").order_by("-requested_at", "-pk")
        context.update(title="Driver payouts", subtitle="Review payout requests and record completed transfers.", statuses=PayoutRequest.Status.choices)
        if selected in PayoutRequest.Status.values:
            rows = rows.filter(status=selected)
        if query:
            rows = rows.filter(driver__user__username__icontains=query)
    elif section == "support":
        kind = "lost" if request.GET.get("kind") == "lost" else "sos"
        context.update(title="Safety & support", subtitle="Follow up on SOS alerts and lost-item reports.", kind=kind)
        if kind == "sos":
            rows = SOSAlert.objects.select_related("ride__rider", "ride__driver", "raised_by", "resolved_by").order_by("resolved_at", "-created_at", "-pk")
            context["statuses"] = [("open", "Open"), ("resolved", "Resolved")]
            if selected in {"open", "resolved"}:
                rows = rows.filter(resolved_at__isnull=selected == "open")
            if query:
                rows = rows.filter(Q(raised_by__username__icontains=query) | Q(message__icontains=query))
        else:
            rows = LostItem.objects.select_related("ride__quote", "reporter").order_by("-created_at", "-pk")
            context["statuses"] = LostItem.Status.choices
            if selected in LostItem.Status.values:
                rows = rows.filter(status=selected)
            if query:
                rows = rows.filter(Q(reporter__username__icontains=query) | Q(description__icontains=query))
    else:
        raise Http404
    context["selected_status"] = selected
    context["page"] = Paginator(rows, 20).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    context["page_query"] = params.urlencode()
    return render(request, "webapp/operations_list.html", context)


@staff_page
@require_GET
def driver_detail(request, pk):
    if not can_view(request.user, "drivers.view_driverprofile"):
        raise PermissionDenied
    profile = get_object_or_404(DriverProfile.objects.select_related("user", "vehicle").prefetch_related("documents"), pk=pk)
    from django.contrib.contenttypes.models import ContentType
    history = LogEntry.objects.filter(content_type=ContentType.objects.get_for_model(profile), object_id=str(pk)).select_related("user")[:10]
    return render(request, "webapp/operations_driver.html", {"section": "drivers", "profile": profile,
        "approval_errors": approval_errors(profile), "review_history": history, "review_version": review_version(profile), "today": timezone.localdate()})


@staff_page
@require_POST
def driver_decision(request, pk):
    require_permission(request, "drivers.change_driverprofile")
    form = ReviewForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a review decision, use at most 300 characters, and reload the application before retrying.")
        return redirect("webapp:operations-driver", pk=pk)
    return action_result(request, lambda: review_driver(request.user, pk, **form.cleaned_data), "webapp:operations-driver", pk=pk)


@staff_page
@require_POST
def document_decision(request, pk):
    require_permission(request, "drivers.change_driverdocument")
    document = get_object_or_404(DriverDocument, pk=pk)
    form = ReviewForm(request.POST)
    form.fields["decision"].choices = [("ACCEPTED", "Accept"), ("REJECTED", "Reject")]
    if not form.is_valid():
        messages.error(request, "Invalid document review. Reload the application and try again.")
        return redirect("webapp:operations-driver", pk=document.driver_id)
    return action_result(request, lambda: review_document(request.user, pk, **form.cleaned_data), "webapp:operations-driver", pk=document.driver_id)


@staff_page
@require_GET
def document_file(request, pk):
    if not (request.user.has_perm("drivers.view_driverdocument") or request.user.has_perm("drivers.change_driverdocument")):
        raise PermissionDenied
    document = get_object_or_404(DriverDocument, pk=pk)
    try:
        return FileResponse(document.file.open("rb"), as_attachment=True)
    except (FileNotFoundError, OSError) as error:
        raise Http404("The document file is unavailable. Ask the driver to upload it again.") from error


@staff_page
@require_POST
def user_state(request, pk):
    require_permission(request, "accounts.change_user")
    def change():
        target = get_object_or_404(User.objects.select_for_update(), pk=pk)
        from accounts.access import position_for
        if target.is_staff or target.is_superuser or position_for(target) or target.pk == request.user.pk:
            raise Conflict("Staff account access must be managed through administration.")
        decision = request.POST.get("action")
        if decision not in {"activate", "deactivate"}:
            raise Conflict("Choose activate or deactivate.")
        active = decision == "activate"
        if not active and Ride.objects.filter(Q(rider=target) | Q(driver=target), status__in=ACTIVE_STATUSES).exists():
            raise Conflict("This account has an active ride. Finish or cancel that ride before deactivating the account.")
        target.is_active = active
        target.save(update_fields=["is_active"])
        if not active:
            DriverProfile.objects.filter(user=target).update(is_online=False)
        audit(request.user, target, "Account activated" if active else "Account deactivated")
    return action_result(request, change, "webapp:operations-list", section="users")


@staff_page
@require_POST
def payout_action(request, pk):
    require_permission(request, "drivers.change_payoutrequest")
    def change():
        payout = get_object_or_404(PayoutRequest.objects.select_for_update(), pk=pk)
        action = request.POST.get("action")
        allowed = {"PENDING": {"approve", "reject"}, "APPROVED": {"pay", "reject"}}
        if action not in allowed.get(payout.status, set()):
            raise Conflict("This payout changed or is already final. Refresh before processing it.")
        note = request.POST.get("admin_note", "").strip()
        if len(note) > 300 or (action in {"reject", "pay"} and not note):
            raise Conflict("Provide a rejection reason or transfer reference, using at most 300 characters.")
        payout = process_payout(request.user, pk, action=action, admin_note=note)
        audit(request.user, payout, f"Payout {payout.status}: {note}")
        notify(payout.driver.user_id, f"Payout request #{pk}: {payout.get_status_display()}.")
    return action_result(request, change, "webapp:operations-list", section="payouts")


@staff_page
@require_POST
def resolve_alert(request, pk):
    require_permission(request, "rides.change_sosalert")
    def change():
        alert = resolve_sos(request.user, pk)
        audit(request.user, alert, "SOS resolved")
    return action_result(request, change, "webapp:operations-list", section="support")


@staff_page
@require_POST
def update_report(request, pk):
    require_permission(request, "rides.change_lostitem")
    def change():
        report = update_lost_item(request.user, pk, status=request.POST.get("status"), admin_note=request.POST.get("admin_note", ""))
        audit(request.user, report, f"Lost-item report: {report.get_status_display()}")
    response = action_result(request, change, "webapp:operations-list", section="support")
    response["Location"] += "?kind=lost"
    return response


@staff_page
@require_POST
def cancel_ride(request, pk):
    require_permission(request, "accounts.cancel_operational_ride")
    from rides.services import cancel_operational_ride
    if request.POST.get("confirmed") != "yes":
        messages.error(request, "Confirm the cancellation after reviewing the trip and reason.")
        return redirect("webapp:operations-list", section="rides")
    return action_result(request, lambda: cancel_operational_ride(request.user, pk, reason=request.POST.get("reason", "")), "webapp:operations-list", section="rides")
