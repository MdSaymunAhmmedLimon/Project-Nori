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


def notify(user_id, message, ride_id=None, *, title="", category=None, action_url="", sent_by=None):
    if action_url and (not action_url.startswith("/") or action_url.startswith("//") or "\\" in action_url):
        raise ValueError("Notification links must be local application paths.")
    notification = Notification.objects.create(user_id=user_id, message=message[:250], ride_id=ride_id,
        title=title[:100], category=category or ("RIDE" if ride_id else "SYSTEM"),
        action_url=action_url or (f"/app/?ride={ride_id}" if ride_id else ""), sent_by=sent_by)
    publish(user_id, "notification", ride_id=str(ride_id) if ride_id else None)
    return notification
