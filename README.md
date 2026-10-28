
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

## Demo wallet and prepayments

Riders can open **Nori Wallet** to add simulated credits through the local
SSLCOMMERZ demo checkout. Wallet and direct demo prepayments work for immediate
rides and advance bookings. Cancelled demo bookings are refunded, and completed
trips support star ratings and feedback tags. No payment credentials or real
money are involved. See [demo payment behavior and API](docs/DEMO_PAYMENTS.md).

## Admin dashboard

The app now provides separate rider, driver, administrator, moderator, and custom
team workspaces. Start with **Admin dashboard → Positions & access** to create a
category, choose its powers, and invite an existing user. Every new position follows
**admin invitation → user acceptance → final admin approval or rejection**.
Acceptance alone grants no powers. Invitations expire after seven days, decisions
are recorded, and admins can withdraw invitations or revoke approved positions.
See [positions, notifications, and cancellation](docs/POSITIONS.md).

Staff sign-ins open `/operations/`. The dashboard includes driver approvals,
searchable rides and users, payout processing, SOS alerts, lost-item follow-up,
and the existing revenue and cancellation reports. Ordinary rider and driver
accounts cannot access these pages.

Create your first administrator locally with:

```powershell
python manage.py createsuperuser --settings=ride_sharing_app.local_settings
```

For a deployed database, use `python manage.py createsuperuser` with its configured
settings. No default administrator password is installed. A superuser can use
all dashboard actions; other staff need the matching model's change permission
(Driver profile, Driver document, User, Payout request, SOS alert, or Lost item),
assigned under **Advanced settings → Users / Groups**.

Open **Driver approvals → Review application** to inspect a driver's license,
vehicle, and documents. Approval requires an active driver account, an unexpired
license, a vehicle, and acceptance of every submitted document. New driver approvals
also require a Driver-position invitation and the driver's acceptance. Complete
the final decision in **Positions & access**. Previously approved drivers keep
their existing access. Approval allows
the driver to go online after sharing a recent location. Requesting changes takes
the driver offline, sends the review note, and records the staff decision. Editing
driver/vehicle information or replacing documents requires another review.

Payout actions record approvals and completed transfers; they do not send money.
Paid and rejected payouts cannot be changed through this dashboard. User access
controls protect staff accounts and users with active rides.

## Road routes and live trips

Trip quotes use road distance and driving time from an OSRM-compatible service,
including intermediate stops in their selected order. The map displays the same
road geometry saved with the quote. Driver pickup distances also use road routes;
straight-line distance is only a preliminary geographic filter. Routing failures
return a retryable error instead of estimating a fare from a straight line.

Set `ROUTING_BASE_URL` and `ROUTING_TIMEOUT_SECONDS` in the environment to configure
the router. Local development defaults to `https://router.project-osrm.org` with
an eight-second timeout; production should use a dedicated OSRM-compatible service
with both Route and Table APIs. Driving times are estimates, without live traffic.
Existing quotes retain their original agreed fares and distances. Apply migrations
with `python manage.py migrate --settings=ride_sharing_app.local_settings` locally
or `python manage.py migrate` for the configured deployment database.

The trip map keeps the road route visible when changing fare options and restores
an unfinished route in the same browser tab after reload. A purple road line shows
the driver's approach to pickup. Use **Show my location** to enable a moving blue
device marker, GPS accuracy circle, and **Follow me**. Drivers can also use **Share
location** to send their movement to the rider. Location requires browser permission
and HTTPS (localhost works for development); tracking stops when the page closes
or the user selects **Stop location**. GPS updates can pause when a mobile browser
is in the background.

The trip's **Messages** panel lets its rider and assigned driver chat until the
ride ends, with live updates and polling recovery. Drafts survive trip status
updates, failed sends keep their text, and previous messages remain readable in
trip history. Other accounts cannot access the conversation.

## Journey workspace

The rider dashboard includes a multi-stop planner (up to eight intermediate
stops), numbered map markers, and controls to reorder or remove stops. Stops
are included in the road route and fare, and the draft survives a tab reload.
**Go there again** uses your own completed trips and requests a fresh fare.

**Quick actions** opens with its button or Ctrl/Cmd+K. Search for a workspace
screen, saved places, scheduling, location, or safety, then use arrow keys and
Enter to open it. Escape closes the menu.

Active trips show journey progress and a road-based pickup/destination estimate
when fresh driver GPS is available. Multi-stop journeys show the full booked
travel estimate rather than claiming an inaccurate remaining time. Estimates
do not include live traffic. Quotes show their actual expiry countdown and
disable confirmation when expired.

Drivers have a readiness checklist, performance summaries, document uploads
and review feedback, and payout requests with status history. Rider statistics
show completed journeys and frequently travelled routes. The responsive UI
supports keyboard navigation and reduced-motion preferences.

## Checks

```powershell
python manage.py test --settings=ride_sharing_app.test_settings --noinput
python manage.py makemigrations --check --dry-run --settings=ride_sharing_app.test_settings
```

Optional desktop/mobile browser verification (Windows):

```powershell
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe scripts/browser_smoke.py
.venv/Scripts/python.exe scripts/browser_experience_check.py
```

The browser script uses installed Edge on Windows, or a Playwright Chromium
installation / `BROWSER_EXECUTABLE`. It uses a disposable database and saves
screenshots under the Git-ignored `artifacts/browser/` directory. PostgreSQL
concurrency checks are skipped by SQLite and need an isolated PostgreSQL test
database to run. The experience check uses a local routing fixture to verify
multi-stop bookings, quote expiry, keyboard navigation, arrival estimates,
payouts, document submission, and mobile layout. It isolates both database and
uploads and writes screenshots to `artifacts/experience/`.

## GitHub and Vercel

Push the project source, including new files, to your connected GitHub repository
and redeploy with the existing Vercel settings. Keep credentials in Vercel
environment variables; `.env`, local databases, `.venv`, and artifacts are ignored.
Production requires PostgreSQL (`DATABASE_URL`) and Redis (`REDIS_URL`).

Scheduled matching runs during active client polling. With no active clients,
unattended dispatch requires the external worker described in the deployment
guide. A calendar reminder does not reserve a driver or guarantee pickup.
