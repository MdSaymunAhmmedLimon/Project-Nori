"""Server-rendered workspaces and the two-stage position approval screens."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods
from rest_framework.exceptions import APIException

from accounts import positions
from accounts.access import PERMISSIONS, can_view, dashboard_url, permission_labels, position_for
from accounts.models import PositionAssignment, PositionCategory, PositionInvitation, User
from notifications.models import Notification
from notifications.services import notify, publish


class CategoryForm(forms.Form):
    name = forms.CharField(max_length=80)
    description = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}))
    dashboard = forms.ChoiceField(choices=PositionCategory.Dashboard.choices)
    permissions = forms.MultipleChoiceField(choices=list(PERMISSIONS.items()), required=False, widget=forms.CheckboxSelectMultiple)


class InviteForm(forms.Form):
    username = forms.CharField(max_length=150, label="Recipient username", help_text="The recipient must already have a Nori account.")
    category = forms.ModelChoiceField(queryset=PositionCategory.objects.all(), label="Position category")
    note = forms.CharField(max_length=500, required=False, widget=forms.Textarea(attrs={"rows": 3}), label="Message to the recipient")

    def clean_username(self):
        username = self.cleaned_data["username"]
        self.target = User.objects.filter(username__iexact=username, is_active=True).first()
        if not self.target:
            raise forms.ValidationError("No active account matches this username.")
        return username


def manager_required(request):
    if not request.user.has_perm("accounts.manage_positions"):
        raise PermissionDenied


def action(request, callback, destination, **kwargs):
    try:
        callback()
    except APIException as error:
        if error.status_code == 403:
            raise PermissionDenied from error
        messages.error(request, str(error.detail))
    else:
        messages.success(request, "Your decision has been saved.")
    return redirect(destination, **kwargs)


@login_required
@never_cache
@require_GET
def index(request):
    manager = request.user.has_perm("accounts.manage_positions")
    invitations = PositionInvitation.objects.select_related("user", "invited_by", "reviewed_by")
    if not manager:
        invitations = invitations.filter(user=request.user)
    status = request.GET.get("status", "")
    if status in PositionInvitation.Status.values:
        invitations = invitations.filter(status=status)
    page = Paginator(invitations, 20).get_page(request.GET.get("page"))
    assignments = PositionAssignment.objects.select_related("user", "invitation").filter(revoked_at__isnull=True)
    if not manager:
        assignments = assignments.filter(user=request.user)
    return render(request, "webapp/positions.html", {"manager": manager, "page": page, "statuses": PositionInvitation.Status.choices,
        "selected_status": status, "assignments": assignments[:50], "categories": PositionCategory.objects.all() if manager else [],
        "pending_review": invitations.filter(status="ACCEPTED", expires_at__gt=timezone.now()).count()})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def create_category(request):
    manager_required(request)
    form = CategoryForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                positions.create_category(request.user, **form.cleaned_data)
        except (APIException, IntegrityError) as error:
            form.add_error(None, str(error.detail) if isinstance(error, APIException) else "That category name is already in use.")
        else:
            messages.success(request, "Category created. Its powers are granted only through an accepted and approved invitation.")
            return redirect("webapp:positions")
    return render(request, "webapp/position_form.html", {"form": form, "title": "Create a user category", "description": "Choose the dashboard and exact powers this position can receive.", "submit_label": "Create category"})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def invite(request):
    manager_required(request)
    form = InviteForm(request.POST or None, initial={"username": request.GET.get("username", "")})
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                invitation = positions.invite(request.user, user_id=form.target.pk, category_id=form.cleaned_data["category"].pk, note=form.cleaned_data["note"])
        except (APIException, IntegrityError) as error:
            form.add_error(None, str(error.detail) if isinstance(error, APIException) else "This user already has an invitation. Reload and try again.")
        else:
            messages.success(request, "Invitation sent. The user must accept before your final review.")
            return redirect("webapp:position-detail", pk=invitation.pk)
    categories = [{"id": category.pk, "name": category.name, "permissions": permission_labels(category.permissions), "description": category.description} for category in PositionCategory.objects.all()]
    return render(request, "webapp/position_form.html", {"form": form, "title": "Invite someone to a position", "description": "Send an invitation, wait for acceptance, then verify the account and give your final decision.", "submit_label": "Send invitation", "category_previews": categories})


@login_required
@never_cache
@require_GET
def detail(request, pk):
    invitation = get_object_or_404(PositionInvitation.objects.select_related("user", "invited_by", "reviewed_by"), pk=pk)
    manager = request.user.has_perm("accounts.manage_positions")
    if invitation.user_id != request.user.pk and not manager:
        raise PermissionDenied
    from drivers.models import DriverProfile
    profile = DriverProfile.objects.filter(user=invitation.user).first() if invitation.dashboard == "DRIVER" else None
    from drivers.review import approval_errors
    return render(request, "webapp/position_detail.html", {"invitation": invitation, "manager": manager,
        "permissions": permission_labels(invitation.permissions), "version": positions.review_version(invitation),
        "events": invitation.events.select_related("actor"), "current_position": position_for(invitation.user),
        "driver_profile": profile, "driver_errors": approval_errors(profile) if profile else []})


@login_required
@require_POST
def respond(request, pk):
    return action(request, lambda: positions.respond(request.user, pk, decision=request.POST.get("decision"), note=request.POST.get("note", "")), "webapp:position-detail", pk=pk)


@login_required
@require_POST
def decide(request, pk):
    manager_required(request)
    if request.POST.get("confirmed") != "yes":
        messages.error(request, "Confirm that you have checked the account, offered powers and acceptance before deciding.")
        return redirect("webapp:position-detail", pk=pk)
    return action(request, lambda: positions.decide(request.user, pk, decision=request.POST.get("decision"), note=request.POST.get("note", ""), expected_version=request.POST.get("version", "")), "webapp:position-detail", pk=pk)


@login_required
@require_POST
def revoke(request, pk):
    manager_required(request)
    get_object_or_404(User, pk=pk)
    return action(request, lambda: positions.revoke(request.user, pk, reason=request.POST.get("reason", "")), "webapp:positions")


@login_required
@never_cache
@require_GET
def team_dashboard(request):
    position = position_for(request.user)
    if not position or position.invitation.dashboard == "DRIVER":
        return redirect(dashboard_url(request.user))
    from rides.models import ACTIVE_STATUSES, Ride, SOSAlert, LostItem
    cards = []
    if can_view(request.user, "rides.view_sosalert"):
        cards.append({"label": "Open safety alerts", "count": SOSAlert.objects.filter(resolved_at__isnull=True).count(), "url": "/operations/support/", "description": "Review alerts and follow up on rider safety."})
    if can_view(request.user, "rides.view_lostitem"):
        cards.append({"label": "Lost-item reports", "count": LostItem.objects.count(), "url": "/operations/support/?kind=lost", "description": "Help riders reconnect with their belongings."})
    if can_view(request.user, "rides.view_ride"):
        cards.append({"label": "Active trips", "count": Ride.objects.filter(status__in=ACTIVE_STATUSES).count(), "url": "/operations/rides/?status=active", "description": "Review trip activity and handle cancellations."})
    if can_view(request.user, "accounts.view_user"):
        cards.append({"label": "Active accounts", "count": User.objects.filter(is_active=True).count(), "url": "/operations/users/", "description": "Find accounts and review their details."})
    if can_view(request.user, "drivers.view_driverprofile"):
        from drivers.models import DriverProfile
        cards.append({"label": "Driver applications", "count": DriverProfile.objects.filter(verification_status="PENDING").count(), "url": "/operations/drivers/", "description": "Check driver credentials and documents."})
    if can_view(request.user, "drivers.view_payoutrequest"):
        from drivers.models import PayoutRequest
        cards.append({"label": "Pending payouts", "count": PayoutRequest.objects.filter(status="PENDING").count(), "url": "/operations/payouts/", "description": "Review driver payout requests."})
    return render(request, "webapp/team_dashboard.html", {"position": position, "cards": cards, "permission_labels": permission_labels(position.invitation.permissions)})


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def notification_center(request):
    if request.method == "POST":
        rows = Notification.objects.filter(user=request.user, read_at__isnull=True)
        if request.POST.get("action") == "read-one":
            try:
                notification_id = int(request.POST.get("notification", "0"))
            except (TypeError, ValueError):
                from django.http import Http404
                raise Http404 from None
            notification = get_object_or_404(Notification, pk=notification_id, user=request.user)
            rows = rows.filter(pk=notification.pk)
        elif request.POST.get("action") != "read-all":
            raise PermissionDenied
        if rows.update(read_at=timezone.now()):
            publish(request.user.pk, "notifications_read")
        return redirect("webapp:notification-center")
    rows = Notification.objects.filter(user=request.user).order_by("-created_at", "-pk")
    if request.GET.get("filter") == "unread":
        rows = rows.filter(read_at__isnull=True)
    return render(request, "webapp/notification_center.html", {"page": Paginator(rows, 20).get_page(request.GET.get("page")), "unread_only": request.GET.get("filter") == "unread"})


class AnnouncementForm(forms.Form):
    username = forms.CharField(max_length=150, label="Recipient username")
    title = forms.CharField(max_length=100)
    message = forms.CharField(max_length=250, widget=forms.Textarea(attrs={"rows": 4}))

    def clean_username(self):
        user = User.objects.filter(username__iexact=self.cleaned_data["username"], is_active=True).first()
        if not user:
            raise forms.ValidationError("No active account matches this username.")
        self.target = user
        return user.username


@login_required
@never_cache
@require_http_methods(["GET", "POST"])
def announce(request):
    if not request.user.has_perm("accounts.send_notifications"):
        raise PermissionDenied
    form = AnnouncementForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            notify(form.target.pk, form.cleaned_data["message"], title=form.cleaned_data["title"], category="ANNOUNCEMENT", sent_by=request.user)
        messages.success(request, "Notification sent to " + form.target.username + ".")
        return redirect("webapp:notification-center")
    return render(request, "webapp/position_form.html", {"form": form, "title": "Send a notification", "description": "Send an in-app update to a specific account. The sender and delivery time are recorded.", "submit_label": "Send notification"})
