"""Transactional invitation -> acceptance -> independent final review workflow."""
import hashlib
import json
from datetime import timedelta

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from notifications.services import notify
from rides.services import Conflict
from .access import DEPENDENCIES, PERMISSIONS
from .models import PositionAssignment, PositionCategory, PositionEvent, PositionInvitation, User


def require_manager(actor):
    if not actor.is_active or not actor.has_perm("accounts.manage_positions"):
        raise PermissionDenied("Only administrators with position-management permission can do this.")


def validate_permissions(permissions, dashboard):
    if not isinstance(permissions, list) or any(code not in PERMISSIONS for code in permissions):
        raise ValidationError("Choose powers from the available permission list.")
    codes = set(permissions)
    for code in list(codes):
        codes.update(DEPENDENCIES.get(code, set()))
    if "accounts.manage_positions" in codes and dashboard != "ADMIN":
        raise ValidationError("Position management is an administrator power. Choose the Administrator dashboard.")
    if dashboard == "DRIVER" and codes:
        raise ValidationError("A driving position cannot include administrative powers.")
    return sorted(codes)


@transaction.atomic
def create_category(actor, *, name, description, dashboard, permissions):
    require_manager(actor)
    name = name.strip()
    if not name or len(name) > 80 or len(description) > 500 or dashboard not in PositionCategory.Dashboard.values:
        raise ValidationError("Provide a category name, description and valid dashboard.")
    if PositionCategory.objects.filter(name__iexact=name).exists():
        raise Conflict("A category with this name already exists.")
    return PositionCategory.objects.create(name=name, description=description.strip(), dashboard=dashboard,
        permissions=validate_permissions(permissions, dashboard), created_by=actor)


def record(invitation, actor, action, note=""):
    PositionEvent.objects.create(invitation=invitation, actor=actor, action=action, note=note)


def alert_reviewers(invitation, message):
    from django.db.models import Q
    recipients = set(User.objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(user_permissions__codename="manage_positions", user_permissions__content_type__app_label="accounts") |
        Q(groups__permissions__codename="manage_positions", groups__permissions__content_type__app_label="accounts")
    ).values_list("pk", flat=True))
    for assignment in PositionAssignment.objects.select_related("invitation").filter(user__is_active=True, revoked_at__isnull=True, invitation__status="APPROVED"):
        if "accounts.manage_positions" in assignment.invitation.permissions:
            recipients.add(assignment.user_id)
    for user_id in recipients - {invitation.user_id}:
        notify(user_id, message, title="Position review", category="POSITION", action_url=f"/positions/{invitation.pk}/")


@transaction.atomic
def invite(actor, *, user_id, category_id, note=""):
    require_manager(actor)
    target = User.objects.select_for_update().filter(pk=user_id, is_active=True).first()
    if not target:
        raise NotFound("Active user not found.")
    if target.pk == actor.pk or target.is_staff or target.is_superuser:
        raise ValidationError("Invite another ordinary account. Existing platform administrators are managed separately.")
    category = PositionCategory.objects.get(pk=category_id)
    if len(note) > 500:
        raise ValidationError("Use at most 500 characters.")
    if category.dashboard == "DRIVER" and target.role != "DRIVER":
        raise ValidationError("The recipient must have a driver account and submit their driving credentials first.")
    expired = PositionInvitation.objects.select_for_update().filter(user=target, status__in=["INVITED", "ACCEPTED"], expires_at__lte=timezone.now())
    for old in expired:
        old.status = "EXPIRED"
        old.save(update_fields=["status"])
        record(old, actor, "EXPIRED", "Invitation expired before a final decision.")
    if PositionInvitation.objects.filter(user=target, status__in=["INVITED", "ACCEPTED"]).exists():
        raise Conflict("This user already has an open invitation. Withdraw it before sending another.")
    invitation = PositionInvitation.objects.create(user=target, category=category, category_name=category.name,
        dashboard=category.dashboard, permissions=validate_permissions(category.permissions, category.dashboard),
        invited_by=actor, note=note.strip(), expires_at=timezone.now() + timedelta(days=7))
    record(invitation, actor, "INVITED", note.strip())
    notify(target.pk, f"You have been invited to join as {category.name}. Review the powers and accept or decline within 7 days.",
        title="Position invitation", category="POSITION", action_url=f"/positions/{invitation.pk}/", sent_by=actor)
    return invitation


@transaction.atomic
def respond(actor, invitation_id, *, decision, note=""):
    User.objects.select_for_update().get(pk=actor.pk)
    invitation = PositionInvitation.objects.select_for_update().filter(pk=invitation_id, user=actor).first()
    if not invitation:
        raise NotFound("Invitation not found.")
    if invitation.status != "INVITED" or invitation.expired:
        raise Conflict("This invitation is no longer awaiting a response.")
    if decision not in {"accept", "decline"} or len(note) > 500:
        raise ValidationError("Choose accept or decline and use at most 500 characters.")
    invitation.status = "ACCEPTED" if decision == "accept" else "DECLINED"
    invitation.accepted_at = timezone.now() if decision == "accept" else None
    invitation.acceptance_note = note.strip()
    invitation.save(update_fields=["status", "accepted_at", "acceptance_note"])
    record(invitation, actor, invitation.status, note.strip())
    alert_reviewers(invitation, f"{actor.username} declined the invitation for {invitation.category_name}." if decision == "decline" else f"{actor.username} accepted {invitation.category_name}. Final review is required before access is granted.")
    notify(actor.pk, "Your response is saved. No new powers are granted until an administrator approves." if decision == "accept" else "You declined this invitation.", title="Invitation response", category="POSITION", action_url=f"/positions/{invitation.pk}/")
    return invitation


def review_version(invitation):
    user = User.objects.get(pk=invitation.user_id)
    assignment = PositionAssignment.objects.filter(user=user).values("invitation_id", "revoked_at").first()
    data = [invitation.pk, invitation.status, invitation.category_name, invitation.permissions,
        str(invitation.accepted_at), str(invitation.expires_at), user.username, user.email,
        user.first_name, user.last_name, user.phone_number, user.is_active, user.is_staff, user.is_superuser, user.role, assignment]
    if invitation.dashboard == "DRIVER":
        from drivers.models import DriverProfile
        from drivers.review import review_version as driver_version
        profile = DriverProfile.objects.filter(user=user).first()
        data.append(driver_version(profile) if profile else None)
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


@transaction.atomic
def decide(actor, invitation_id, *, decision, note, expected_version):
    require_manager(actor)
    target_id = PositionInvitation.objects.filter(pk=invitation_id).values_list("user_id", flat=True).first()
    if target_id is None:
        raise NotFound("Invitation not found.")
    target = User.objects.select_for_update().get(pk=target_id)
    invitation = PositionInvitation.objects.select_for_update().get(pk=invitation_id)
    if invitation.dashboard == "DRIVER":
        from drivers.models import DriverProfile
        DriverProfile.objects.select_for_update().filter(user=target).first()
    if target.pk == actor.pk or target.is_staff or target.is_superuser:
        raise PermissionDenied("You cannot review your own position or a platform administrator's position.")
    if invitation.status not in {"INVITED", "ACCEPTED"} or invitation.expired:
        raise Conflict("This invitation is final or expired. Refresh to see its current state.")
    if not expected_version or expected_version != review_version(invitation):
        raise Conflict("The account or invitation changed. Reload and review all details again.")
    if decision not in {"approve", "reject", "withdraw"} or not note.strip() or len(note) > 500:
        raise ValidationError("Choose a decision and provide a review note of at most 500 characters.")
    if decision in {"approve", "reject"} and invitation.status != "ACCEPTED":
        raise Conflict("The user must accept before final approval or rejection. You may withdraw an unanswered invitation.")
    if decision == "approve":
        if not target.is_active:
            raise Conflict("This account is inactive. It cannot receive a position.")
        validate_permissions(invitation.permissions, invitation.dashboard)
        if invitation.dashboard == "DRIVER":
            from drivers.review import review_driver
            from drivers.models import DriverProfile
            profile = DriverProfile.objects.filter(user=target).first()
            if not profile:
                raise Conflict("The driver must submit a profile, license and vehicle before final approval.")
            review_driver(actor, profile.pk, decision="APPROVED", note=note[:300], position_review=True)
        previous = PositionAssignment.objects.select_for_update().filter(user=target, revoked_at__isnull=True).first()
        if previous:
            if previous.invitation.dashboard == "DRIVER" and invitation.dashboard != "DRIVER":
                from drivers.models import DriverProfile
                from rides.models import ACTIVE_STATUSES, Ride
                if Ride.objects.filter(driver=target, status__in=ACTIVE_STATUSES).exists():
                    raise Conflict("Finish the active trip before replacing this driving position.")
                DriverProfile.objects.filter(user=target).update(is_online=False, verification_status="PENDING")
            record(previous.invitation, actor, "REPLACED", f"Replaced by approved invitation #{invitation.pk}.")
        PositionAssignment.objects.update_or_create(user=target, defaults={"invitation": invitation, "revoked_at": None, "revoked_by": None, "revoke_reason": ""})
    invitation.status = {"approve": "APPROVED", "reject": "REJECTED", "withdraw": "WITHDRAWN"}[decision]
    invitation.reviewed_by = actor
    invitation.reviewed_at = timezone.now()
    invitation.review_note = note.strip()
    invitation.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_note"])
    record(invitation, actor, invitation.status, note.strip())
    notify(target.pk, f"Your {invitation.category_name} invitation was {invitation.get_status_display().lower()}. {note.strip()}",
        title="Final position decision", category="POSITION", action_url=f"/positions/{invitation.pk}/", sent_by=actor)
    return invitation


@transaction.atomic
def revoke(actor, user_id, *, reason):
    require_manager(actor)
    if actor.pk == user_id or not reason.strip() or len(reason) > 500:
        raise ValidationError("Choose another user and explain why access is being revoked (up to 500 characters).")
    target = User.objects.select_for_update().get(pk=user_id)
    assignment = PositionAssignment.objects.select_for_update().select_related("invitation").filter(user=target, revoked_at__isnull=True).first()
    if not assignment:
        raise Conflict("This user has no active position.")
    if assignment.invitation.dashboard == "DRIVER":
        from drivers.models import DriverProfile
        from rides.models import ACTIVE_STATUSES, Ride
        if Ride.objects.filter(driver=target, status__in=ACTIVE_STATUSES).exists():
            raise Conflict("Finish or cancel the driver's active trip before revoking this position.")
        DriverProfile.objects.filter(user=target).update(is_online=False, verification_status="PENDING")
    assignment.revoked_at, assignment.revoked_by, assignment.revoke_reason = timezone.now(), actor, reason.strip()
    assignment.save(update_fields=["revoked_at", "revoked_by", "revoke_reason"])
    record(assignment.invitation, actor, "REVOKED", reason.strip())
    notify(target.pk, f"Your {assignment.invitation.category_name} access has been revoked. {reason.strip()}", title="Position access revoked", category="POSITION", action_url="/positions/", sent_by=actor)
