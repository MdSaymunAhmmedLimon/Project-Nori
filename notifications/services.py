import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction

from .models import Notification

logger = logging.getLogger(__name__)


def publish(user_id, event="updated", **data):
    def send():
        try:
            async_to_sync(get_channel_layer().group_send)(
                f"user_{user_id}", {"type": "app.event", "event": event, **data}
            )
        except Exception:
            # Persisted state remains authoritative; clients recover by polling.
            logger.exception("Could not publish a live update")
    transaction.on_commit(send)


def notify(user_id, message, ride_id=None):
    Notification.objects.create(user_id=user_id, message=message, ride_id=ride_id)
    publish(user_id, "notification", ride_id=str(ride_id) if ride_id else None)
