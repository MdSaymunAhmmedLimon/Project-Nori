"""Participant-scoped favourites and lost-item support workflows."""

from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from notifications.services import notify

from .models import FavouriteDriver, LostItem, Ride
from .services import Conflict, require_role


RESOLVED_ITEM_STATUSES = (LostItem.Status.RETURNED, LostItem.Status.NOT_FOUND)


def completed_rider_trip(user, ride_id):
    require_role(user, "RIDER")
    # Keep the nullable driver relation out of PostgreSQL's locking query.
    ride = Ride.objects.select_for_update().filter(pk=ride_id, rider=user).first()
    if not ride:
        raise NotFound("Trip not found.")
    if ride.status != Ride.Status.COMPLETED:
        raise Conflict("This action is available after a completed trip.")
    return ride


@transaction.atomic
def save_favourite_driver(user, ride_id):
    ride = completed_rider_trip(user, ride_id)
    if not ride.driver or not ride.driver.is_active or ride.driver.role != "DRIVER":
        raise Conflict("This driver is no longer available to save.")
    return FavouriteDriver.objects.get_or_create(rider=user, driver=ride.driver)


def remove_favourite_driver(user, favourite_id):
    require_role(user, "RIDER")
    favourite = FavouriteDriver.objects.filter(pk=favourite_id, rider=user).first()
    if not favourite:
        raise NotFound("Favourite driver not found.")
    favourite.delete()


@transaction.atomic
def report_lost_item(user, ride_id, description):
    ride = completed_rider_trip(user, ride_id)
    description = description.strip()
    if not description or len(description) > 500:
        raise ValidationError({"description": "Describe the item using 1 to 500 characters."})
    existing = LostItem.objects.filter(ride=ride, reporter=user).exclude(status__in=RESOLVED_ITEM_STATUSES).first()
    if existing:
        if existing.description == description:
            return existing, False
        raise Conflict("There is already an open report for this trip. Check its progress in My profile.")
    report = LostItem.objects.create(ride=ride, reporter=user, description=description)
    notify(user.pk, f"Lost-item report #{report.pk} received. Follow its progress in My profile.", ride.pk)
    return report, True


@transaction.atomic
def update_lost_item(user, report_id, *, status, admin_note):
    if not user.is_active or not user.has_perm("rides.change_lostitem"):
        raise PermissionDenied("You do not have permission to manage lost-item reports.")
    if status not in LostItem.Status.values:
        raise ValidationError({"status": "Choose a valid report status."})
    if len(admin_note) > 500:
        raise ValidationError({"admin_note": "Use at most 500 characters."})
    ride_id = LostItem.objects.filter(pk=report_id).values_list("ride_id", flat=True).first()
    if not ride_id:
        raise NotFound("Report not found.")
    # Match the creation lock order when reopening an older report.
    Ride.objects.select_for_update().get(pk=ride_id)
    report = LostItem.objects.select_for_update().get(pk=report_id)
    if status not in RESOLVED_ITEM_STATUSES and LostItem.objects.filter(ride_id=ride_id).exclude(
        pk=report.pk,
    ).exclude(status__in=RESOLVED_ITEM_STATUSES).exists():
        raise Conflict("Another report for this trip is open. Resolve it before reopening this report.")
    admin_note = admin_note.strip()
    if report.status == status and report.admin_note == admin_note:
        return report
    status_changed = report.status != status
    if status in RESOLVED_ITEM_STATUSES:
        if report.status != status or not report.resolved_at:
            report.resolved_at = timezone.now()
    else:
        report.resolved_at = None
    report.status = status
    report.admin_note = admin_note
    report.save(update_fields=["status", "admin_note", "resolved_at"])
    if status_changed:
        notify(report.reporter_id, f"Lost-item report #{report.pk} updated: {report.get_status_display()}.", report.ride_id)
    return report
