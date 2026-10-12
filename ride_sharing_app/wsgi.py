"""
WSGI config for ride_sharing_app project.

It exposes the WSGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/wsgi/
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault(
    'DJANGO_SETTINGS_MODULE',
    'ride_sharing_app.vercel_settings' if os.environ.get('VERCEL') == '1'
    else 'ride_sharing_app.settings',
)

application = get_wsgi_application()
