"""Validated filters shared by trip history and downloadable reports."""

from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers

from .models import Ride


class DateRangeSerializer(serializers.Serializer):
    date_from = serializers.DateField(required=False)
    date_to = serializers.DateField(required=False)

    def validate(self, attrs):
        if attrs.get("date_from") and attrs.get("date_to") and attrs["date_from"] > attrs["date_to"]:
            raise serializers.ValidationError({"date_to": "End date must be on or after start date."})
        return attrs


class HistoryFilterSerializer(DateRangeSerializer):
    q = serializers.CharField(required=False, allow_blank=True, max_length=200)
    status = serializers.ChoiceField(choices=Ride.Status.choices, required=False)
    payment_method = serializers.ChoiceField(choices=Ride.PaymentMethod.choices, required=False)


def apply_date_range(queryset, filters, field):
    # Compare raw timestamps so PostgreSQL can use an ordinary B-tree index.
    # Preserve the same local-day semantics as Django's previous __date lookup.
    zone = timezone.get_current_timezone()
    if filters.get("date_from"):
        start = timezone.make_aware(datetime.combine(filters["date_from"], time.min), zone)
        queryset = queryset.filter(**{f"{field}__gte": start})
    if filters.get("date_to"):
        end_date = filters["date_to"]
        if end_date == date.max:
            end = timezone.make_aware(datetime.combine(end_date, time.max), zone)
            queryset = queryset.filter(**{f"{field}__lte": end})
        else:
            end = timezone.make_aware(datetime.combine(end_date + timedelta(days=1), time.min), zone)
            queryset = queryset.filter(**{f"{field}__lt": end})
    return queryset


def filter_history(queryset, params):
    serializer = HistoryFilterSerializer(data=params)
    serializer.is_valid(raise_exception=True)
    filters = serializer.validated_data
    queryset = apply_date_range(queryset, filters, "created_at")
    for field in ("status", "payment_method"):
        if filters.get(field):
            queryset = queryset.filter(**{field: filters[field]})
    if filters.get("q"):
        queryset = queryset.filter(
            Q(quote__pickup_label__icontains=filters["q"]) |
            Q(quote__destination_label__icontains=filters["q"])
        )
    return queryset.order_by("-created_at", "-id")
