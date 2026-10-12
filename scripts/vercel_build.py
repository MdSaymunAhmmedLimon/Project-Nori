"""Validate production services and migrate before Vercel collects static files."""

import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["DJANGO_SETTINGS_MODULE"] = "ride_sharing_app.vercel_settings"


def main():
    import django
    from django.core.cache import cache
    from django.core.management import call_command

    django.setup()
    call_command("check", deploy=True, fail_level="WARNING")
    # A build must not succeed with a broken shared authentication cache.
    key = f"nori:deployment-check:{secrets.token_hex(16)}"
    try:
        cache.set(key, "ok", timeout=30)
        if cache.get(key) != "ok":
            raise RuntimeError("The configured Redis cache failed its deployment check.")
    finally:
        cache.delete(key)
    call_command("migrate", interactive=False)
    # The Django framework preset runs collectstatic and publishes to the CDN.


if __name__ == "__main__":
    main()
