"""Start local development with `python run.py` or the PyCharm Run button."""

import argparse
import importlib.util
import os
import socket
import subprocess
import sys

from project_runtime import ROOT, use_project_python


def main():
    parser = argparse.ArgumentParser(description="Prepare and start the local Nori app.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-reload", action="store_true", help="Disable automatic restarts when editing code.")
    parser.add_argument("--check", action="store_true", help="Prepare the database and check the app without starting a server.")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    if sys.version_info < (3, 11):
        parser.error("Python 3.11 or newer is required.")

    use_project_python(create=True)
    os.chdir(ROOT)
    required = ("django", "rest_framework", "rest_framework_simplejwt", "psycopg", "dotenv",
                "channels", "daphne", "channels_redis", "stripe", "whitenoise")
    if any(importlib.util.find_spec(module) is None for module in required):
        print("Installing the project's dependencies...", flush=True)
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")], check=True)

    # This launcher is explicitly local. Deployment entry points keep PostgreSQL.
    os.environ["DJANGO_SETTINGS_MODULE"] = "ride_sharing_app.local_settings"
    import django
    from django.core.management import call_command
    django.setup()

    if os.environ.get("RUN_MAIN") != "true":
        if not args.check:
            with socket.socket() as probe:
                if probe.connect_ex(("127.0.0.1", args.port)) == 0:
                    parser.exit(1, f"Port {args.port} is already in use. Open http://127.0.0.1:{args.port}/ if Nori is already running, or use --port {args.port + 1 if args.port < 65535 else 8000}.\n")
        print("Preparing the local database...", flush=True)
        call_command("migrate", interactive=False, verbosity=1)
        call_command("check")
        if args.check:
            print("Nori is ready. Start it with: python run.py", flush=True)
            return
        print(f"\nOpen http://127.0.0.1:{args.port}/\nKeep this process running. Press Ctrl+C to stop.\n", flush=True)
    call_command("runserver", f"127.0.0.1:{args.port}", use_reloader=not args.no_reload)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.returncode) from None
