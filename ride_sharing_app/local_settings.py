"""Default command-line development without PostgreSQL or Redis.

Use --settings=ride_sharing_app.local_settings with manage.py commands.
Real deployments and concurrency tests must use the PostgreSQL settings.
"""

import os
from pathlib import Path

from .development import prepare_local_environment

prepare_local_environment(Path(__file__).resolve().parent.parent)

from .settings import *  # noqa: E402,F403

DEBUG = True
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "[::1]"]
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": Path(os.environ.get("NORI_LOCAL_DATABASE", BASE_DIR / "local.sqlite3")),  # noqa: F405
        "OPTIONS": {"timeout": 20},
    }
}
REDIS_URL = ""
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False
SECURE_SSL_REDIRECT = False
CARD_PAYMENTS_ENABLED = False
STRIPE_SECRET_KEY = ""
STRIPE_WEBHOOK_SECRET = ""
EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
