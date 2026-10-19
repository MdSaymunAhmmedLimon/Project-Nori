
# Nori ride sharing

A Django application with rider and driver dashboards, fare estimates, matching,
trip tracking, scheduled rides, cash and optional Stripe payments, ratings,
saved places, trip sharing, and support tools.

## Latest update

Performance improvements reduce the 20-trip history fixture from 104 database
queries to five, reservations from 22 to three, and chat from 24 to two. Dashboard
polling and rendering also do less repeated work. Include the two new index
migrations when pushing to GitHub; Vercel's existing build applies them.
See [performance measurements and deployment details](docs/PERFORMANCE.md).

## Reservation features

Manage upcoming reservations in **My trips**: change pickup times, download a
calendar reminder, or cancel. Scheduled dispatch now preserves the reserved fare
after quote expiry and prevents cancellation/dispatch races. Existing Vercel
PostgreSQL tables are reused. The reservation feature itself needs no new columns
or dependency; the subsequent performance update adds database indexes.

- [Project findings and feature research](docs/FEATURE_RESEARCH.md)
- [Vercel deployment and database setup](docs/VERCEL.md)
- [REST API](docs/API.md)

## Local development

Use Python 3.11 or newer:

```powershell
python run.py
```

The launcher creates the project virtual environment, installs dependencies if
needed, prepares the local database, and serves the app at
`http://127.0.0.1:8000/`. Local development uses separate settings from Vercel.

## Checks

```powershell
python manage.py test --settings=ride_sharing_app.test_settings --noinput
python manage.py makemigrations --check --dry-run --settings=ride_sharing_app.test_settings
```

Optional desktop/mobile browser verification (Windows):

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe scripts/browser_smoke.py
```

The browser script uses installed Edge on Windows, or a Playwright Chromium
installation / `BROWSER_EXECUTABLE`. It uses a disposable database and saves
screenshots under the Git-ignored `artifacts/browser/` directory. PostgreSQL
concurrency checks are skipped by SQLite and need an isolated PostgreSQL test
database to run.

## GitHub and Vercel

Push the project source, including new files, to your connected GitHub repository
and redeploy with the existing Vercel settings. Keep credentials in Vercel
environment variables; `.env`, local databases, `.venv`, and artifacts are ignored.
Production requires PostgreSQL (`DATABASE_URL`) and Redis (`REDIS_URL`).

Scheduled matching runs during active client polling. With no active clients,
unattended dispatch requires the external worker described in the deployment
guide. A calendar reminder does not reserve a driver or guarantee pickup.
