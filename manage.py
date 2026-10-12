#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys

from project_runtime import use_project_python


def main():
    """Run administrative tasks."""
    # Vercel manages its own interpreter and build-time virtual environment.
    if os.environ.get('VERCEL') != '1':
        use_project_python()
    os.environ.setdefault(
        'DJANGO_SETTINGS_MODULE',
        'ride_sharing_app.vercel_settings' if os.environ.get('VERCEL') == '1'
        else 'ride_sharing_app.local_settings',
    )
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django is not installed for this project. Run 'python run.py' "
            "to create the project environment and install its dependencies."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
