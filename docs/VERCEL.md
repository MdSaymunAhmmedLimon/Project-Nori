# Deploy Nori to Vercel

## Performance update

The API now batches trip, stop, vehicle, rating, and sender reads; the dashboard
uses lighter notification polling and avoids repeated rendering. Date filters
use timestamp ranges. No new environment variable, dependency, or service is
required. Keep using the existing PostgreSQL and Redis connections.

**This update includes index migrations.** Push
`rides/migrations/0005_performance_indexes.py` and
`notifications/migrations/0002_performance_indexes.py`, the new `rides/queries.py`,
and all other modified/new source files. The existing Vercel build command runs
`migrate` before collecting static files. The eight new indexes are additive and
preserve existing data. Earlier sections describing updates without migrations
apply to those features only.

After redeployment, check trip history, Activity counts and pagination, scheduled
rides, and the driver workflow. See [measured improvements](PERFORMANCE.md) for
the regression checks and the limits of local verification.

## Reservation management update

Riders can change pickup times and download calendar reminders from **My trips**.
The update also fixes expired-quote failures during scheduled dispatch, duplicate
reservations, and races between cancellation and dispatch. All data uses the
existing `ScheduledRide`, `RideQuote`, `Ride`, and notification tables. There are
no new dependencies, migrations, environment variables, or storage services.
Commit the new `rides/calendar.py`, `rides/tests/test_scheduling.py`, and
`docs/FEATURE_RESEARCH.md` files along with the modified files before pushing to
your connected GitHub repository.

Rider polling dispatches that rider's due reservations, and eligible drivers'
offer polling processes a bounded batch. The existing external
`dispatch_rides --loop` worker now also processes scheduled rides. For unattended
dispatch without active browsers, keep that worker running outside Vercel, or
invoke `python manage.py dispatch_scheduled_rides` regularly from an external
scheduler with the same production environment. This update does not configure
a cron job or guarantee pickup. Reservations over 15 minutes late are cancelled
when processed and the rider receives an in-app notification.

Calendar downloads are generated in memory and sent directly to the authenticated
reservation owner. Importing a calendar file does not subscribe to updates:
riders must update/remove reminders after changing/cancelling their reservation.

The Vercel dashboard provisions PostgreSQL through Marketplace integrations such
as Neon or Supabase. Keep this project's `DATABASE_URL` pointing to the existing
database's PostgreSQL connection string. If your integration uses a differently
named variable, map its connection string to `DATABASE_URL` in Vercel settings;
no database replacement is needed. Redis remains an existing app requirement.
See [Vercel Marketplace storage](https://vercel.com/docs/marketplace-storage).

## Trip reports and earnings update

The dashboard includes route/date/payment filters under **My trips**, a
**Download CSV** button for the applied filters, and date filters plus daily
breakdowns under driver **Earnings**. Reports are generated directly from the
existing database and returned as downloads; no persistent filesystem, background
worker, new environment variables, dependencies, or database migrations are needed
for these features. Upload the changed project files to GitHub and redeploy your
connected Vercel project using its existing configuration.

Local verification (uses an isolated test database):

```powershell
python manage.py test --settings=ride_sharing_app.test_settings --noinput
python scripts/browser_smoke.py
```

The optional browser smoke check needs `requirements-dev.txt` and a Playwright
browser (or `BROWSER_EXECUTABLE` pointing at a local Chromium-based browser).

## Booking shortcuts update

Riders can choose **Ride again** from trip history or a completed/cancelled trip,
swap pickup and destination in the booking form, and use saved places as either
endpoint. Saved places can be edited in **My profile** and paged through in both
the profile and booking screens. Rebooking requires a fresh fare and confirmation.
These features use existing tables and need no migrations, dependencies, or new
Vercel environment variables. Include `rides/tests/test_saved_place_editing.py`
when uploading the project; the browser smoke script also checks these flows.

## Favourite drivers and lost-item support update

Completed trips now offer **Save driver to favourites** and **Left something
behind?**. Riders can manage favourites and track lost-item reports in **My
profile**. Staff manage reports through **Operations → Manage lost items**, using
the existing Django admin. Superusers already have access; other staff need the
Lost item view/change permissions. Status updates appear in the rider's Activity
feed and refresh in their profile. Internal staff notes remain private.

This update uses the existing `FavouriteDriver` and `LostItem` tables; it needs no
new migration, dependency, Vercel setting, or background worker. The existing
build/migration step creates any missing Django permissions on deployment.

## Deployment setup

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

Configure the SMTP variables and `ADMIN_EMAIL` documented in `.env.example`
for SOS alerts to operations. Password recovery uses account security questions
and does not require email setup.
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
