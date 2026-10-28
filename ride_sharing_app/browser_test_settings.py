"""Disposable settings used only by scripts/browser_smoke.py."""

from .local_settings import *  # noqa: F403

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": os.environ["BROWSER_TEST_DB"]}}
DEBUG = True
ALLOWED_HOSTS = ["127.0.0.1", "localhost", "testserver"]
CARD_PAYMENTS_ENABLED = False
MEDIA_ROOT = Path(os.environ["BROWSER_TEST_DB"]).parent / "media"
