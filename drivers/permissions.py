from rest_framework.permissions import BasePermission


class IsDriver(BasePermission):
    message = "This endpoint is available to drivers only."

    def has_permission(self, request, view):
        return bool(request.user.is_authenticated and request.user.role == "DRIVER")
