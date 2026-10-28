"""Staff driver-review workflow shared by the dashboard and administration."""

import hashlib
import json

from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from notifications.services import notify
from rides.services import Conflict
from .models import DriverDocument, DriverProfile


def require_staff_permission(user, permission):
    if not user.is_active or not user.has_perm(permission):
        raise PermissionDenied("Your staff account does not have permission for this action.")


def audit(user, obj, message):
    LogEntry.objects.create(user=user, content_type=ContentType.objects.get_for_model(obj),
        object_id=str(obj.pk), object_repr=str(obj)[:200], action_flag=CHANGE,
        change_message=json.dumps([{"changed": {"fields": [message]}}]))


def approval_errors(profile):
    errors = []
    if not profile.user.is_active or profile.user.role != "DRIVER":
        errors.append("The driver account must be active.")
    if profile.license_expiry_date < timezone.localdate():
        errors.append("The driving license has expired.")
    if not hasattr(profile, "vehicle"):
        errors.append("The driver must add a vehicle before approval.")
    if profile.documents.exclude(review_status="ACCEPTED").exists():
        errors.append("Accept all submitted documents before approving this driver.")
    return errors


def review_version(profile):
    """Ignore GPS/availability updates when checking for concurrent application edits."""
    vehicle = getattr(profile, "vehicle", None)
    data = {
        "profile": [profile.license_number, str(profile.license_expiry_date), profile.verification_status, profile.review_note],
        "user": [profile.user.is_active, profile.user.role, profile.user.email, profile.user.phone_number],
        "vehicle": [getattr(vehicle, name, None) for name in ("license_plate", "make", "model", "year", "color", "vehicle_class", "seats")],
        "documents": [[doc.pk, doc.doc_type, doc.file.name, doc.review_status, doc.review_note,
                       str(doc.uploaded_at), str(doc.reviewed_at)] for doc in sorted(profile.documents.all(), key=lambda doc: doc.pk)],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def check_version(profile, expected_version):
    if expected_version and review_version(profile) != expected_version:
        raise Conflict("This application changed after you opened it. Review the latest details and try again.")


@transaction.atomic
def review_driver(user, profile_id, *, decision, note="", expected_version=None, position_review=False):
    require_staff_permission(user, "drivers.change_driverprofile")
    profile = DriverProfile.objects.select_for_update().filter(pk=profile_id).first()
    if not profile:
        raise NotFound("Driver profile not found.")
    check_version(profile, expected_version)
    if decision not in DriverProfile.Verification.values:
        raise ValidationError("Choose approve, needs changes, or awaiting review.")
    note = note.strip()
    if len(note) > 300:
        raise ValidationError("Review notes must be 300 characters or fewer.")
    if decision == "REJECTED" and not note:
        raise ValidationError("Explain what the driver needs to correct.")
    if decision == "APPROVED":
        from accounts.models import PositionInvitation
        if not position_review or not PositionInvitation.objects.filter(user_id=profile.user_id, dashboard="DRIVER", status="ACCEPTED", expires_at__gt=timezone.now()).exists():
            raise Conflict("Invite this driver to the Driver position, wait for their acceptance, then complete the final review in Positions & access.")
        errors = approval_errors(profile)
        if errors:
            raise ValidationError(errors)
    if profile.verification_status == decision and profile.review_note == note:
        return profile
    profile.verification_status = decision
    profile.review_note = note
    if decision != "APPROVED":
        profile.is_online = False
    profile.save(update_fields=["verification_status", "review_note", "is_online", "updated_at"])
    audit(user, profile, f"Driver review: {profile.get_verification_status_display()}. {note}")
    notify(profile.user_id, f"Driver application: {profile.get_verification_status_display()}. {note}".strip()[:250])
    return profile


@transaction.atomic
def review_document(user, document_id, *, decision, note="", expected_version=None):
    require_staff_permission(user, "drivers.change_driverdocument")
    driver_id = DriverDocument.objects.filter(pk=document_id).values_list("driver_id", flat=True).first()
    if not driver_id:
        raise NotFound("Document not found.")
    profile = DriverProfile.objects.select_for_update().get(pk=driver_id)
    document = DriverDocument.objects.select_for_update().get(pk=document_id)
    check_version(profile, expected_version)
    if decision not in {"ACCEPTED", "REJECTED"}:
        raise ValidationError("Choose accept or reject.")
    note = note.strip()
    if len(note) > 300 or (decision == "REJECTED" and not note):
        raise ValidationError("Provide a rejection reason of at most 300 characters.")
    document.review_status, document.review_note = decision, note
    document.reviewed_at = timezone.now()
    document.save(update_fields=["review_status", "review_note", "reviewed_at"])
    if decision == "REJECTED":
        profile.verification_status = "PENDING"
        profile.is_online = False
        profile.review_note = "A document needs changes. Upload a corrected copy for review."
    profile.save(update_fields=["verification_status", "is_online", "review_note", "updated_at"])
    audit(user, document, f"Document review: {decision}. {note}")
    notify(profile.user_id, f"{document.get_doc_type_display()}: {document.get_review_status_display()}. {note}".strip()[:250])
    return document
