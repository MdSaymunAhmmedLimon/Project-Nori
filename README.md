# Nori ride sharing

[![Deploy with Vercel](https://vercel.com/button)](https://vercel.com/new)

A Django application with rider and driver dashboards, a shared REST API, and live
trip updates. Riders can get a fare, request a nearby driver, follow their trip,
pay, print a trip receipt, and rate their journey. Drivers can submit their license and vehicle for
review, accept trips, share location, and view earnings. Staff manage approvals
through `/admin/` and view the network at `/operations/`.

## Run locally on Windows

Use Python 3.11 or newer. From the project directory in PowerShell, run:

```powershell
python run.py
```

Or double-click **start.bat**. In PyCharm, select **Nori Local** from the run
configurations and press Run. The launcher uses the project's `.venv`, installs
missing app dependencies, prepares the SQLite database, and starts the server.
There is no PostgreSQL, Redis, environment activation, or settings flag to set up
for local development. Keep the terminal or PyCharm process running while using
the app.

Open <http://127.0.0.1:8000/>. If that port is already in use, the launcher reports
it; use `python run.py --port 8001` to choose another. For a setup check without a
server, use `python run.py --check`.

Your users and rides persist in `local.sqlite3`. Existing `.env` values are kept.
When no signing key is configured, a private local key is generated once in
`.local/secret-key`, so sessions survive restarts. Local payments use cash.

Create your own administrator account once, in a second terminal:

```powershell
python manage.py createsuperuser
```

All `manage.py` commands use local settings by default and find the project
interpreter automatically. `python manage.py runserver` also works after the
initial setup. Use `python run.py` for automatic migrations before startup.
On macOS/Linux the same commands work; `.venv/bin/python` is the environment's
Python executable.

Local SQLite is for development. Deployment uses PostgreSQL for concurrent ride
assignment locking. Explicit `DJANGO_SETTINGS_MODULE` and `--settings` values
are respected by `manage.py`; ASGI/WSGI deployment entry points keep PostgreSQL.

## Try a complete trip

1. Register a rider and a driver in separate browser profiles or private windows.
2. In the driver's **My profile**, save an unexpired license and a vehicle.
3. Log in as the superuser at `/admin/`, open Driver profiles, select the driver,
   and run **Approve selected complete driver profiles**.
4. In the driver dashboard, share location and go online. Location is sent every
   25 seconds while sharing; **Stop sharing** stops those updates. After a page
   reload, use **Share location** again. Manual coordinates are also supported.
5. As the rider, select pickup and destination on the map, get the fare, and
   request a ride. The driver must be within the configured matching radius
   (10 km by default), with a location updated in the last 90 seconds.
6. Accept as the driver, head to pickup, mark arrival, start, and complete the trip.
   Confirm cash received, then check ratings, trip history, earnings, and activity.

For a manual example near the default map center, use pickup
`23.810300, 90.412500`, destination `23.780600, 90.419300`, and place the driver at
the pickup. The route labels describe the selected coordinates; typing an address
alone does not search for it. Fare and time estimates use straight-line distance
with a road factor. Map lines are not driving directions.

## PostgreSQL and Redis with Docker Compose

Install Docker with Compose, copy `.env.example` to `.env` if needed, and set
`DJANGO_SECRET_KEY` and `DB_PASSWORD` to your own values. For localhost development,
keep `DJANGO_DEBUG=True` so session cookies work over HTTP.

Generate a signing key using
`python manage.py shell -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`
and save it in single quotes in `.env`. Production settings require that key;
they do not use the generated local key.

```powershell
docker compose up --build -d
docker compose exec web python manage.py createsuperuser
docker compose logs -f web dispatcher
```

Compose waits for PostgreSQL and Redis, runs migrations in the `migrate` service,
and then starts the ASGI web server and ride dispatcher. Open
<http://127.0.0.1:8000/>; the database persists in the `postgres_data` volume.
Use `docker compose down` to stop the services while retaining that volume.

For an existing PostgreSQL installation, set the `DB_*` values in `.env`, then
set `$env:DJANGO_SETTINGS_MODULE='ride_sharing_app.settings'` in PowerShell before
running `python manage.py migrate`, `python manage.py createsuperuser`, and
`python manage.py runserver`. Compose selects those settings automatically.
Database connections time out after 10 seconds by
default (`DB_CONNECT_TIMEOUT`). Configure `REDIS_URL` when running multiple
processes; run `manage.py dispatch_rides --loop` in a separate terminal for
background matching and search expiry. Browser polling also triggers matching.

Before an internet deployment, use HTTPS, `DJANGO_DEBUG=False`, the public
`DJANGO_ALLOWED_HOSTS`, `PUBLIC_BASE_URL`, and `CSRF_TRUSTED_ORIGINS`, and run
`python manage.py check --deploy --settings=ride_sharing_app.settings`.
`/health/` reports database availability. Select a
map tile provider appropriate to your traffic.

## Deploy to Vercel

See [docs/VERCEL.md](docs/VERCEL.md) for the account connection and hosted service
setup. The project includes the Django framework preset, production settings,
and a build step that checks Redis and migrates PostgreSQL. Local startup is
unchanged.

## Account security and receipts

Use **My profile → Change password** to update a password while signed in.
The current browser stays signed in; other browser sessions and previously issued
API tokens lose access. The login page's **Forgot your password?** flow sends a
single-use reset link valid for one hour. Resetting a password requires signing
in again on every device.

For local development, recovery emails appear in the server terminal rather than
a real inbox. Copy the reset URL from that terminal into your browser. In a
deployment, configure `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_HOST_USER`,
`EMAIL_HOST_PASSWORD`, and `DEFAULT_FROM_EMAIL` in `.env`, using your email
provider's SMTP settings. Use `EMAIL_USE_TLS=True` for STARTTLS or
`EMAIL_USE_SSL=True` for implicit TLS, not both. Set `PUBLIC_BASE_URL` to your
public HTTPS origin so recovery links point to the correct app. Verify delivery
with your configured provider before inviting users.

Completed trips have **View receipt** after payment, or **View trip summary**
while payment is pending. Both the rider and assigned driver can open the page
and use **Print / save PDF**. The driver's copy also shows platform fees and net
earnings. Activity messages link back to the relevant trip.

## Checks

The isolated test settings do not use your development database or require its
services:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe manage.py check --settings=ride_sharing_app.test_settings
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=ride_sharing_app.test_settings
.\.venv\Scripts\python.exe manage.py test --settings=ride_sharing_app.test_settings
.\.venv\Scripts\python.exe scripts/browser_smoke.py
```

The browser check starts the real `run.py` launcher with a disposable database and
normal password hashing. It exercises the rider, driver, and staff workflows,
desktop/mobile screens, and recovery from a temporary API failure.
It also checks password change, local recovery email delivery, reset/login,
receipt rendering and PDF printing, and activity links.
Screenshots and the server log go in `artifacts/browser/`. On Windows it uses
installed Microsoft Edge when available. Otherwise install Playwright's Chromium
with `.\.venv\Scripts\python.exe -m playwright install chromium`, or set
`BROWSER_EXECUTABLE` to a compatible browser executable.

Two concurrency tests require PostgreSQL and are skipped on SQLite. With a
PostgreSQL test database configuration and a database user allowed to create test
databases, run `python manage.py test --settings=ride_sharing_app.settings` to include them.

## API and integrations

See [docs/API.md](docs/API.md) for authentication, ride actions, payments,
notifications, pagination, and WebSocket events. Browser sessions use CSRF;
other clients can use JWT authentication with the same API.

Cash payments work without a provider. Hosted card checkout requires your own
Stripe credentials and signed webhook configuration; automated card tests mock
the provider. Configure both `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`,
point Stripe callbacks to `/api/payments/webhook/stripe/`, and set the public base
URL before enabling card payments through the production settings. The local
settings always use cash. Earnings are accounting records; bank payouts, platform
fee settlement, SMS, trip-notification emails, and mobile push delivery are not
implemented. Password recovery emails use the SMTP configuration described above.
#   P r o j e c t - N o r i  
 