"""Exercise deployment startup without connecting to production services."""

import os
from pathlib import Path
import secrets
import subprocess
import sys

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parents[2]


class VercelStartupTests(SimpleTestCase):
    def run_vercel(self, code, *, overrides=None, entrypoint=None, success=True):
        env = os.environ | {
            "VERCEL": "1",
            "VERCEL_ENV": "production",
            "VERCEL_URL": "nori-build.example.com",
            "VERCEL_BRANCH_URL": "nori-branch.example.com",
            "VERCEL_PROJECT_PRODUCTION_URL": "nori.example.com",
            "DJANGO_SECRET_KEY": secrets.token_urlsafe(64),
            "DJANGO_DEBUG": "True",
            "DJANGO_ALLOWED_HOSTS": "",
            "CSRF_TRUSTED_ORIGINS": "",
            "PUBLIC_BASE_URL": "",
            "DATABASE_URL": "postgresql://test_user:test_password@db.example.com/nori?sslmode=require",
            "REDIS_URL": "rediss://cache.example.com:6379/0",
        }
        env.pop("DJANGO_SETTINGS_MODULE", None)
        env.update(overrides or {})
        if entrypoint == "manage":
            command = [sys.executable, "manage.py", "shell", "-c", code]
        else:
            if entrypoint:
                startup = f"import ride_sharing_app.{entrypoint}; "
            else:
                env["DJANGO_SETTINGS_MODULE"] = "ride_sharing_app.vercel_settings"
                startup = "import django; django.setup(); "
            command = [sys.executable, "-c", startup + code]
        result = subprocess.run(
            command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=30,
        )
        output = result.stdout + result.stderr
        if success:
            self.assertEqual(result.returncode, 0, output)
        else:
            self.assertNotEqual(result.returncode, 0, output)
        return output

    def test_all_entrypoints_choose_production_settings_on_vercel(self):
        for entrypoint in ("manage", "asgi", "wsgi"):
            with self.subTest(entrypoint=entrypoint):
                self.run_vercel(
                    "from django.conf import settings; "
                    "assert settings.SETTINGS_MODULE == 'ride_sharing_app.vercel_settings'; "
                    "assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'; "
                    "assert not settings.DEBUG; "
                    "assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE",
                    entrypoint=entrypoint,
                )

    def test_production_deployment_checks_pass(self):
        self.run_vercel(
            "from django.core.management import call_command; "
            "call_command('check', deploy=True, fail_level='WARNING')"
        )

    def test_forwarded_https_and_project_hosts_are_accepted(self):
        self.run_vercel(
            "from django.test import Client; "
            "client = Client(enforce_csrf_checks=True); "
            "response = client.get('/', HTTP_HOST='nori.example.com', HTTP_X_FORWARDED_PROTO='https'); "
            "assert response.status_code == 200, response.status_code; "
            "assert 'max-age=3600' in response['Strict-Transport-Security']; "
            "assert client.get('/', HTTP_HOST='nori.example.com').status_code == 301; "
            "assert client.get('/', HTTP_HOST='foreign.vercel.app', HTTP_X_FORWARDED_PROTO='https').status_code == 400; "
            "assert client.post('/login/', {}, HTTP_HOST='nori.example.com', HTTP_X_FORWARDED_PROTO='https').status_code == 403"
        )

    def test_production_and_preview_links_use_the_correct_origin(self):
        for environment, expected in (
            ("production", "https://nori.example.com"),
            ("preview", "https://nori-build.example.com"),
        ):
            with self.subTest(environment=environment):
                self.run_vercel(
                    "from django.conf import settings; "
                    f"assert settings.PUBLIC_BASE_URL == {expected!r}; "
                    "assert 'https://nori-build.example.com' in settings.CSRF_TRUSTED_ORIGINS; "
                    "assert 'https://nori-branch.example.com' in settings.CSRF_TRUSTED_ORIGINS; "
                    "assert '.vercel.app' not in settings.ALLOWED_HOSTS",
                    overrides={"VERCEL_ENV": environment},
                )

    def test_custom_domain_is_trusted(self):
        self.run_vercel(
            "from django.conf import settings; "
            "assert settings.PUBLIC_BASE_URL == 'https://ride.example.org'; "
            "assert 'ride.example.org' in settings.ALLOWED_HOSTS; "
            "assert 'https://ride.example.org' in settings.CSRF_TRUSTED_ORIGINS",
            overrides={"PUBLIC_BASE_URL": "https://ride.example.org/"},
        )

    def test_database_credentials_and_provider_options_are_preserved(self):
        self.run_vercel(
            "from django.conf import settings; db = settings.DATABASES['default']; "
            "assert db['USER'] == 'test@user'; "
            "assert db['PASSWORD'] == 'test:/password'; "
            "assert db['NAME'] == 'nori_app'; "
            "assert db['PORT'] == '6543'; "
            "assert db['OPTIONS']['sslmode'] == 'verify-full'; "
            "assert db['OPTIONS']['channel_binding'] == 'require'; "
            "assert db['DISABLE_SERVER_SIDE_CURSORS']; "
            "assert db['CONN_MAX_AGE'] == 0",
            overrides={"DATABASE_URL": "postgresql://test%40user:test%3A%2Fpassword@db.example.com:6543/nori_app?sslmode=verify-full&channel_binding=require"},
        )

    def test_database_connections_require_tls_by_default(self):
        self.run_vercel(
            "from django.conf import settings; "
            "assert settings.DATABASES['default']['OPTIONS']['sslmode'] == 'require'",
            overrides={"DATABASE_URL": "postgres://test_user:test_password@db.example.com/nori"},
        )

    def test_missing_services_fail_instead_of_using_local_defaults(self):
        for name in ("DATABASE_URL", "REDIS_URL", "DJANGO_SECRET_KEY"):
            with self.subTest(name=name):
                output = self.run_vercel("", overrides={name: ""}, success=False)
                self.assertIn(f"Set {name}", output)

    def test_invalid_service_urls_and_insecure_public_origin_fail(self):
        for name, value, message in (
            ("DATABASE_URL", "sqlite:///local.sqlite3", "must be a PostgreSQL"),
            ("DATABASE_URL", "postgresql://test_user:test_password@db.example.com/nori?sslmode=disable", "must require TLS"),
            ("REDIS_URL", "https://cache.example.com", "not a REST API URL"),
            ("PUBLIC_BASE_URL", "http://nori.example.com", "HTTPS origin"),
            ("PUBLIC_BASE_URL", "https://nori.example.com/path", "HTTPS origin"),
        ):
            with self.subTest(name=name, value=value):
                output = self.run_vercel("", overrides={name: value}, success=False)
                self.assertIn(message, output)

    def test_malformed_database_url_does_not_leak_credentials(self):
        marker = "private-value-must-not-appear"
        output = self.run_vercel(
            "", overrides={"DATABASE_URL": f"postgresql://test_user:{marker}%ZZ@db.example.com/nori"},
            success=False,
        )
        self.assertIn("not a valid PostgreSQL connection URL", output)
        self.assertNotIn(marker, output)
