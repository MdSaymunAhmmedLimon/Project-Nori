"""Bounded powers for approved positions; never grants staff or superuser access."""
from django.contrib.auth.backends import BaseBackend

PERMISSIONS = {
    "accounts.view_operations": "View operations and financial overview",
    "accounts.manage_positions": "Create categories, invite, approve and revoke positions",
    "accounts.view_user": "View users and their contact details",
    "accounts.change_user": "Activate or suspend ordinary user accounts",
    "accounts.send_notifications": "Send notifications to individual users",
    "rides.view_ride": "View all trips and participants",
    "accounts.cancel_operational_ride": "Cancel trips before they start (no cancellation fee)",
    "rides.view_sosalert": "View safety alerts",
    "rides.change_sosalert": "Resolve safety alerts",
    "rides.view_lostitem": "View lost-item reports",
    "rides.change_lostitem": "Update lost-item reports",
    "drivers.view_driverprofile": "View driver applications",
    "drivers.change_driverprofile": "Review driver credentials",
    "drivers.view_driverdocument": "View driver verification documents",
    "drivers.change_driverdocument": "Review driver verification documents",
    "drivers.view_payoutrequest": "View driver payouts",
    "drivers.change_payoutrequest": "Process driver payout requests",
}
DEPENDENCIES = {
    "accounts.change_user": {"accounts.view_user"},
    "accounts.cancel_operational_ride": {"rides.view_ride"},
    "rides.change_sosalert": {"rides.view_sosalert"},
    "rides.change_lostitem": {"rides.view_lostitem"},
    "drivers.change_driverprofile": {"drivers.view_driverprofile"},
    "drivers.change_driverdocument": {"drivers.view_driverdocument", "drivers.view_driverprofile"},
    "drivers.change_payoutrequest": {"drivers.view_payoutrequest"},
}


def position_for(user):
    if not user.is_authenticated or not user.is_active:
        return None
    from .models import PositionAssignment
    return PositionAssignment.objects.select_related("invitation").filter(
        user_id=user.pk, revoked_at__isnull=True, invitation__status="APPROVED",
    ).first()


class PositionBackend(BaseBackend):
    def get_all_permissions(self, user_obj, obj=None):
        if obj is not None:
            return set()
        position = position_for(user_obj)
        return set(position.invitation.permissions) & PERMISSIONS.keys() if position else set()

    def has_perm(self, user_obj, perm, obj=None):
        return perm in self.get_all_permissions(user_obj, obj)


def permission_labels(codes):
    return [PERMISSIONS[code] for code in codes if code in PERMISSIONS]


def is_operator(user):
    return user.is_authenticated and user.is_active and (user.is_staff or position_for(user) is not None)


def can_view(user, permission):
    return user.is_active and (user.is_staff or user.has_perm(permission))


def dashboard_url(user):
    if user.is_staff or user.has_perm("accounts.view_operations"):
        return "webapp:operations"
    position = position_for(user)
    if position and position.invitation.dashboard != "DRIVER":
        return "webapp:team-dashboard"
    return "webapp:dashboard"
