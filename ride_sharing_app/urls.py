from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from notifications.views import MarkRead, MarkOneRead, NotificationList, NotificationSummary

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/auth/", include("accounts.urls")),
    path("api/drivers/", include("drivers.urls")),
    path("api/rides/", include("rides.urls")),
    path("api/payments/", include("payments.urls")),
    path("api/notifications/", NotificationList.as_view(), name="notifications"),
    path("api/notifications/summary/", NotificationSummary.as_view(), name="notifications-summary"),
    path("api/notifications/read/", MarkRead.as_view(), name="notifications-read"),
    path("api/notifications/<int:pk>/read/", MarkOneRead.as_view(), name="notification-read"),
    path("", include("webapp.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)


admin.site.site_header = "Nori · Operations"
admin.site.site_title = "Nori administration"
admin.site.index_title = "Manage your ride network"
