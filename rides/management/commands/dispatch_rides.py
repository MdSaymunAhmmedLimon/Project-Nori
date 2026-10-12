import time

from django.core.management.base import BaseCommand

from rides.services import dispatch_waiting


class Command(BaseCommand):
    help = "Expire unmatched rides and dispatch pending requests; use --loop for a worker."

    def add_arguments(self, parser):
        parser.add_argument("--loop", action="store_true")

    def handle(self, *args, **options):
        try:
            while True:
                dispatch_waiting()
                if not options["loop"]:
                    self.stdout.write(self.style.SUCCESS("Ride dispatch completed."))
                    break
                time.sleep(5)
        except KeyboardInterrupt:
            self.stdout.write("Dispatcher stopped.")
