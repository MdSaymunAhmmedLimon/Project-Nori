# Deploy Nori to Vercel

The root `vercel.json` selects Vercel's Django framework preset and Python 3.12.
Vercel discovers the ASGI application through `manage.py`, collects static assets,
and serves them from its CDN. The app automatically selects
`ride_sharing_app.vercel_settings` when `VERCEL=1`; local and Docker commands retain
their existing settings.

## Required account and services

Connect your Vercel account and create or select a project with this directory as
its root. Enable **Automatically expose System Environment Variables** in project
settings so the application receives its deployment and production hostnames.

Attach a hosted PostgreSQL database and a Redis service. PostgreSQL is required
for persistent accounts, rides, and transaction locking. Redis shares login rate
limits and Channels events between function instances. Redis must support the
standard Redis protocol and Channels operations; a REST-only endpoint is not
compatible. Neither service runs inside the Vercel function.

Set these project environment variables for Production and, with separate data
services, Preview:

| Variable | Value |
| --- | --- |
| `DJANGO_SECRET_KEY` | A new random production signing key, at least 50 characters |
| `DATABASE_URL` | Hosted PostgreSQL URL, including credentials and `sslmode=require` or `verify-full` |
| `REDIS_URL` | Redis connection URL, normally `rediss://...` for hosted TLS |
| `PUBLIC_BASE_URL` | Optional HTTPS origin, such as `https://nori.example.com`; defaults to the production hostname in Production and the deployment hostname in Preview |
| `DJANGO_ALLOWED_HOSTS` | Optional comma-separated additional domains; this project's Vercel hosts and public URL are included automatically |
| `CSRF_TRUSTED_ORIGINS` | Optional additional HTTPS origins |

Generate a dedicated signing key locally and save it as a sensitive Vercel
environment variable:

```powershell
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Keep credentials in Vercel environment variables. `.vercelignore` excludes `.env`,
local SQLite data, logs, screenshots, and the virtual environment from uploads.
Vercel settings do not load the local `.env` or silently fall back to SQLite.
Do not point preview deployments at the production database or Redis.

Configure the SMTP variables documented in `.env.example` for password recovery.
The existing Stripe variables enable optional card payments; cash works without
Stripe. This deployment does not copy local accounts or trips into PostgreSQL.

## Publish

This workspace has a project-local Node.js and Vercel CLI installation in
`.local/deployment/`. The launcher below uses that installation and keeps CLI
credentials, caches, and temporary files inside the project. That directory is
excluded from Git, Docker, and Vercel uploads.

```powershell
python scripts/vercel.py --version
python scripts/vercel.py login
python scripts/vercel.py link
```

Configure the required environment variables in the linked project, then deploy:

```powershell
python scripts/vercel.py deploy --prod
```

Complete the Vercel account sign-in in your browser when the CLI provides its
verification link. The local installation cannot sign into your account for you.
On another machine without these local tools, install Node.js and use
`npx vercel@latest` in place of `python scripts/vercel.py` (CLI 50.38.0 or newer).

`scripts/vercel_build.py` runs Django's production checks, verifies Redis, and
applies database migrations. Failed service connections stop the build. The
Django preset then runs `collectstatic`. Migration changes must remain compatible
with the previous deployment while Vercel switches traffic. HSTS preloading is
deliberately disabled; only that optional deployment warning is silenced.

The ASGI entrypoint preserves Channels support. The browser also refreshes ride
state every eight seconds and triggers matching through its API requests.
Continuous background dispatch with no active browser requires a separate worker
running `python manage.py dispatch_rides --loop`; do not launch that loop inside
a Vercel function. WebSockets depend on the project's Vercel feature availability
and platform limits; polling continues if a socket cannot connect.

After deployment, verify `/`, `/login/`, `/static/webapp/style.css`, and `/health/`.
The health endpoint returns HTTP 200 with `{"status":"ok"}` when PostgreSQL is
reachable. Create a staff account using `manage.py createsuperuser` with
`DJANGO_SETTINGS_MODULE=ride_sharing_app.vercel_settings` and the same hosted
service environment variables loaded securely in the shell. Then verify rider
registration, driver approval, and the complete trip workflow.

References: [Django on Vercel](https://vercel.com/docs/frameworks/full-stack/django),
[Python versions](https://vercel.com/docs/functions/runtimes/python/python-version),
and [system environment variables](https://vercel.com/docs/environment-variables/system-environment-variables).
