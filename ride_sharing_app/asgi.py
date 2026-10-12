import os

os.environ.setdefault(
    "DJANGO_SETTINGS_MODULE",
    "ride_sharing_app.vercel_settings" if os.environ.get("VERCEL") == "1"
    else "ride_sharing_app.settings",
)

from django.core.asgi import get_asgi_application

django_application = get_asgi_application()

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.urls import path

from notifications.consumers import EventsConsumer

application = ProtocolTypeRouter({
    "http": django_application,
    "websocket": AuthMiddlewareStack(URLRouter([path("ws/events/", EventsConsumer.as_asgi())])),
})
