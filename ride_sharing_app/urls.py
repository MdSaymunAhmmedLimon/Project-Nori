from django.contrib import admin
from django.urls import include, path
from notifications.views import MarkRead, NotificationList

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/drivers/", include("drivers.urls")),
    path("api/rides/", include("rides.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/notifications/", NotificationList.as_view(), name="notifications"),
    path("api/notifications/read/", MarkRead.as_view(), name="notifications-read"),
    path("", include("webapp.urls")),
]

admin.site.site_header = "Nori · Operations"
admin.site.site_title = "Nori administration"
admin.site.index_title = "Manage your ride network"
