from django.utils import timezone
from rest_framework import generics, serializers
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import Notification
from .services import publish


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ("id", "title", "category", "message", "action_url", "ride_id", "created_at", "read_at")


class NotificationPagination(PageNumberPagination):
    def paginate_queryset(self, queryset, request, view=None):
        self.unread_count = queryset.filter(read_at__isnull=True).count()
        return super().paginate_queryset(queryset, request, view)

    def get_paginated_response(self, data):
        response = super().get_paginated_response(data)
        response.data["unread_count"] = self.unread_count
        return response


class NotificationList(generics.ListAPIView):
    serializer_class = NotificationSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by("-created_at", "-pk")


class MarkRead(APIView):
    def post(self, request):
        updated = Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        if updated:
            publish(request.user.pk, "notifications_read")
        return Response({"detail": "Notifications marked as read."})


class NotificationSummary(APIView):
    def get(self, request):
        unread = Notification.objects.filter(user=request.user, read_at__isnull=True).count()
        return Response({"unread_count": unread})


class MarkOneRead(APIView):
    def post(self, request, pk):
        from django.shortcuts import get_object_or_404
        notification = get_object_or_404(Notification, pk=pk, user=request.user)
        if Notification.objects.filter(pk=notification.pk, read_at__isnull=True).update(read_at=timezone.now()):
            publish(request.user.pk, "notifications_read")
        return Response({"detail": "Notification marked as read."})
