"""Read-only ride queries with a fixed number of database round trips."""

from django.db.models import Avg, OuterRef, Subquery

from .models import Rating


def with_ride_details(queryset):
    def average_rating(participant):
        return Subquery(
            Rating.objects.filter(recipient_id=OuterRef(participant))
            .order_by().values("recipient_id").annotate(value=Avg("score"))
            .values("value")[:1]
        )

    # Keep this out of locking queries: nullable outer joins cannot be locked
    # with SELECT FOR UPDATE on PostgreSQL.
    return queryset.select_related(
        "quote", "rider", "driver__driver_profile__vehicle", "payment", "tip",
    ).prefetch_related("quote__stops", "events", "ratings").annotate(
        rider_average_rating=average_rating("rider_id"),
        driver_average_rating=average_rating("driver_id"),
    )
