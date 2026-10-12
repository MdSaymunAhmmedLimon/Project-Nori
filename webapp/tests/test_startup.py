import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch

from django.test import SimpleTestCase

from ride_sharing_app.development import prepare_local_environment


ROOT = Path(__file__).resolve().parents[2]


class LocalStartupTests(SimpleTestCase):
    def test_first_start_creates_a_persistent_secret_without_an_env_file(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".venv") as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(ROOT))
            with patch.dict(os.environ, {"DJANGO_SECRET_KEY": ""}):
                prepare_local_environment(directory)
                first = os.environ["DJANGO_SECRET_KEY"]
                self.assertGreaterEqual(len(first), 64)
                os.environ["DJANGO_SECRET_KEY"] = ""
                prepare_local_environment(directory)
                self.assertEqual(os.environ["DJANGO_SECRET_KEY"], first)
                self.assertFalse((Path(directory) / ".env").exists())

    def test_existing_secret_is_preserved(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".venv") as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(ROOT))
            with patch.dict(os.environ, {"DJANGO_SECRET_KEY": "existing-project-key"}):
                prepare_local_environment(directory)
                self.assertEqual(os.environ["DJANGO_SECRET_KEY"], "existing-project-key")
                self.assertFalse((Path(directory) / ".local").exists())

    def run_manage(self, *arguments, environment=None):
        env = os.environ.copy()
        env.pop("DJANGO_SETTINGS_MODULE", None)
        env.update(environment or {})
        result = subprocess.run(
            [sys._base_executable, str(ROOT / "manage.py"), *arguments],
            cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result.stdout

    def test_plain_manage_command_uses_local_database_and_project_interpreter(self):
        output = self.run_manage("shell", "-c", (
            "from pathlib import Path; import sys; from django.conf import settings; "
            "assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'; "
            "assert Path(sys.prefix).name == '.venv'; "
            "assert not settings.SESSION_COOKIE_SECURE; print('Local startup verified')"
        ))
        self.assertIn("Local startup verified", output)

    def test_explicit_production_settings_are_preserved(self):
        code = (
            "from django.conf import settings; "
            "assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.postgresql'; "
            "print('Production settings preserved')"
        )
        output = self.run_manage("shell", "-c", code, "--settings=ride_sharing_app.settings")
        self.assertIn("Production settings preserved", output)
        output = self.run_manage("shell", "-c", code, environment={"DJANGO_SETTINGS_MODULE": "ride_sharing_app.settings"})
        self.assertIn("Production settings preserved", output)

    def test_launcher_prepares_a_new_database_using_system_python(self):
        with tempfile.TemporaryDirectory(dir=ROOT / ".venv") as directory:
            self.assertTrue(Path(directory).resolve().is_relative_to(ROOT))
            database = Path(directory) / "fresh.sqlite3"
            env = os.environ | {"NORI_LOCAL_DATABASE": str(database)}
            result = subprocess.run(
                [sys._base_executable, str(ROOT / "run.py"), "--check"],
                cwd=ROOT, env=env, text=True, capture_output=True, timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(database.exists())
            import sqlite3
            with sqlite3.connect(database) as connection:
                self.assertIsNotNone(connection.execute("SELECT name FROM sqlite_master WHERE name = 'accounts_user'").fetchone())
            connection.close()
            self.assertIn("Nori is ready", result.stdout)
