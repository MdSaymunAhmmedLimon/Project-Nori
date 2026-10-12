import uuid

from django.core.management.base import BaseCommand
from django.utils import timezone

from rides.models import ScheduledRide
from rides.services import request_ride


class Command(BaseCommand):
    help = "Dispatch scheduled rides whose target departure time has arrived."

    def handle(self, *args, **options):
        now = timezone.now()
        due = ScheduledRide.objects.filter(
            scheduled_for__lte=now,
            ride__isnull=True,
            cancelled_at__isnull=True,
        ).select_related("rider", "quote")[:100]

        dispatched = 0
        for sr in due:
            try:
                ride, _ = request_ride(
                    sr.rider,
                    quote_id=sr.quote_id,
                    request_id=uuid.uuid4(),
                    payment_method=sr.payment_method,
                )
                sr.ride = ride
                sr.save(update_fields=["ride"])
                dispatched += 1
            except Exception as exc:
                self.stderr.write(f"Failed to dispatch scheduled ride {sr.pk}: {exc}")

        self.stdout.write(self.style.SUCCESS(f"Dispatched {dispatched} scheduled ride(s)."))
