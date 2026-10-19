from django.core.management.base import BaseCommand
from rides.services import dispatch_due_scheduled_rides


class Command(BaseCommand):
    help = "Dispatch scheduled rides whose target departure time has arrived."

    def handle(self, *args, **options):
        dispatched = dispatch_due_scheduled_rides(limit=100)
        self.stdout.write(self.style.SUCCESS(f"Dispatched {dispatched} scheduled ride(s)."))
