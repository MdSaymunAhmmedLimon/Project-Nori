"""Production settings for Vercel's Django runtime and managed data services."""

import os
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured
from psycopg import ProgrammingError
from psycopg.conninfo import conninfo_to_dict

from .settings import *  # noqa: F403
from .settings import required_env

DEBUG = False
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_HSTS_SECONDS = 3600
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# Preloading is a separate domain-owner decision, not required to deploy.
SECURE_HSTS_PRELOAD = False
SILENCED_SYSTEM_CHECKS = ["security.W021"]

# Trust this project's domains rather than every tenant of vercel.app.
_vercel_hosts = [
    os.environ.get(name, "").strip()
    for name in ("VERCEL_URL", "VERCEL_BRANCH_URL", "VERCEL_PROJECT_PRODUCTION_URL")
]
ALLOWED_HOSTS = list(dict.fromkeys(
    host.strip() for host in [
        *os.environ.get("DJANGO_ALLOWED_HOSTS", "").split(","), *_vercel_hosts,
    ] if host.strip()
))

_default_host = os.environ.get("VERCEL_URL", "")
if os.environ.get("VERCEL_ENV") == "production":
    _default_host = os.environ.get("VERCEL_PROJECT_PRODUCTION_URL") or _default_host
PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
if not PUBLIC_BASE_URL and _default_host:
    PUBLIC_BASE_URL = f"https://{_default_host}"
_public_origin = urlsplit(PUBLIC_BASE_URL)
if (
    _public_origin.scheme != "https" or not _public_origin.hostname
    or _public_origin.username or _public_origin.password
    or _public_origin.path or _public_origin.query or _public_origin.fragment
):
    raise ImproperlyConfigured("Set PUBLIC_BASE_URL to the app's HTTPS origin, or enable Vercel system environment variables.")
if _public_origin.hostname not in ALLOWED_HOSTS:
    ALLOWED_HOSTS.append(_public_origin.hostname)
CSRF_TRUSTED_ORIGINS = list(dict.fromkeys([
    PUBLIC_BASE_URL,
    *(f"https://{host}" for host in _vercel_hosts if host),
    *(origin.strip() for origin in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if origin.strip()),
]))

_database_url = required_env("DATABASE_URL")
if not _database_url.startswith(("postgres://", "postgresql://")):
    raise ImproperlyConfigured("DATABASE_URL must be a PostgreSQL connection URL.")
try:
    _connection = conninfo_to_dict(_database_url)
except ProgrammingError:
    # libpq errors can include credentials from a malformed URL.
    raise ImproperlyConfigured("DATABASE_URL is not a valid PostgreSQL connection URL.") from None
if not all(_connection.get(key) for key in ("host", "dbname", "user")):
    raise ImproperlyConfigured("DATABASE_URL must specify a host, database, and user.")
_connection.setdefault("sslmode", "require")
_connection.setdefault("connect_timeout", "10")
if _connection["sslmode"] not in {"require", "verify-ca", "verify-full"}:
    raise ImproperlyConfigured("DATABASE_URL must require TLS: use sslmode=require or verify-full.")
DATABASES = {"default": {
    "ENGINE": "django.db.backends.postgresql",
    "NAME": _connection.pop("dbname"),
    "USER": _connection.pop("user"),
    "PASSWORD": _connection.pop("password", ""),
    "HOST": _connection.pop("host"),
    "PORT": _connection.pop("port", "5432"),
    "OPTIONS": _connection,
    "CONN_MAX_AGE": 0,
    # Managed poolers may use transaction pooling.
    "DISABLE_SERVER_SIDE_CURSORS": True,
}}

# All function instances share authentication limits and live update broadcasts.
REDIS_URL = required_env("REDIS_URL")
if urlsplit(REDIS_URL).scheme not in {"redis", "rediss"}:
    raise ImproperlyConfigured("REDIS_URL must use redis:// or rediss://, not a REST API URL.")

STATIC_URL = "/static/"
