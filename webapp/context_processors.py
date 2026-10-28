from accounts.access import PERMISSIONS, dashboard_url, position_for
from notifications.models import Notification


def workspace_access(request):
    user = request.user
    if not user.is_authenticated:
        return {}
    permissions = set(PERMISSIONS) if user.is_superuser else user.get_all_permissions()
    legacy = user.is_active and user.is_staff
    access = {
        "overview": legacy or "accounts.view_operations" in permissions,
        "drivers": legacy or "drivers.view_driverprofile" in permissions,
        "users": legacy or "accounts.view_user" in permissions,
        "rides": legacy or "rides.view_ride" in permissions,
        "payouts": legacy or "drivers.view_payoutrequest" in permissions,
        "safety": legacy or "rides.view_sosalert" in permissions,
        "lost_items": legacy or "rides.view_lostitem" in permissions,
        "positions": "accounts.manage_positions" in permissions,
        "notify": "accounts.send_notifications" in permissions,
        "cancel": "accounts.cancel_operational_ride" in permissions,
    }
    return {"workspace_access": access, "workspace_position": position_for(user),
        "notification_unread": Notification.objects.filter(user=user, read_at__isnull=True).count(),
        "workspace_home": dashboard_url(user)}
