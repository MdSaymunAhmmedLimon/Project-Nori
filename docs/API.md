# Shared REST API

Web pages use Django sessions plus CSRF; Android and other API clients use JWT.
All endpoints below use JSON and trailing slashes. Protected requests without a
valid identity return 401; insufficient roles return 403; another user's private
resource returns 404. Invalid input returns 400. A ride action conflicting with
current state returns 409. Throttled requests return 429.

List responses use `{count, next, previous, results}` with 20 records per page.
Pass `?page=2` for subsequent pages. Times are ISO 8601 UTC; monetary values are
decimal strings with a separate currency. Latitude and longitude accept up to six
decimal places. Do not send owner IDs, roles, prices, or privilege flags unless
explicitly listed as writable for that endpoint.

## Accounts

| Method | Path | Body / result |
| --- | --- | --- |
| POST | `/api/auth/register/` | `username`, `email`, `password`; optional `role` (`RIDER` default or `DRIVER`), `first_name`, `last_name`, `phone_number`; 201 user |
| POST | `/api/auth/login/` | `username`, `password`; 200 `{access, refresh}` |
| POST | `/api/auth/refresh/` | `refresh`; replacement `{access, refresh}` |
| POST | `/api/auth/logout/` | `refresh`; blacklists that token, 200 |
| GET | `/api/auth/me/` | Current profile |
| PATCH | `/api/auth/me/` | `first_name`, `last_name`, `phone_number` only |
| POST | `/api/auth/password/change/` | `old_password`, `new_password1`, `new_password2`; 200 confirmation |

Use `Authorization: Bearer <access>` on protected requests. Access tokens last five
minutes. Refresh tokens last one day and rotate: discard the old refresh token and
store the replacement after every successful refresh. Logout revokes the submitted
refresh token; access tokens remain valid until expiry. Discard both tokens locally
on logout. Other login sessions are unaffected. Inactive and deleted users cannot
refresh. Store mobile credentials using the operating system's secure facilities.

Password changes require the current password, matching new passwords, and the
same strength validation used for registration. They invalidate all previously
issued access and refresh tokens, including open JWT WebSocket connections on
their next event or ping. A JWT client must discard its tokens and log in with the
new password. A browser using session authentication keeps its current session;
other browser sessions become invalid.

Browser password recovery starts at `/password/reset/`. The response does not
reveal whether an email has an active account. Reset links expire after one hour
and are single-use. A successful reset invalidates all prior sessions and API
tokens. Local development prints recovery emails to the server console;
deployments require SMTP settings and `PUBLIC_BASE_URL` (see README).

Example PowerShell registration/login; choose a unique username/email if repeating:

```powershell
$api = 'http://127.0.0.1:8000/api'
$secure = Read-Host 'Test-account password' -AsSecureString
$password = [System.Net.NetworkCredential]::new('', $secure).Password
$body = @{username='api_rider'; email='api_rider@example.com'; password=$password; role='RIDER'} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "$api/auth/register/" -ContentType 'application/json' -Body $body
$body = @{username='api_rider'; password=$password} | ConvertTo-Json
$tokens = Invoke-RestMethod -Method Post -Uri "$api/auth/login/" -ContentType 'application/json' -Body $body
$headers = @{Authorization="Bearer $($tokens.access)"}
Invoke-RestMethod -Uri "$api/auth/me/" -Headers $headers
Remove-Variable password,secure,body
```

## Driver onboarding, availability, and location

All driver endpoints require `DRIVER`. They access the authenticated driver's own
records, never a supplied owner ID.

| Method | Path | Body / result |
| --- | --- | --- |
| POST, GET, PATCH | `/api/drivers/profile/` | `license_number`, `license_expiry_date` (`YYYY-MM-DD`); includes read-only verification/availability/location fields |
| POST, GET, PATCH | `/api/drivers/vehicle/` | `license_plate`, `make`, `model`, `color`, `year`, optional `seats` (1–8, default 4) |
| POST | `/api/drivers/location/` | `latitude`, `longitude`; updated profile |
| POST | `/api/drivers/availability/` | `is_online` boolean; updated profile |

Create the profile before the vehicle. Only administrators can approve driver
profiles. Going online requires approval, an unexpired license, a vehicle, and a
location received within 90 seconds. Editing license or vehicle information returns
the driver to `PENDING` review and takes them offline. Such edits and going offline
are blocked during an assigned trip. Drivers should report location every 20–30
seconds while online; the location endpoint is limited to 30 requests/minute.

## Quotes and ride requests

POST `/api/rides/quotes/` as a rider:

```json
{
  "pickup_label": "Banani",
  "pickup_latitude": "23.810300",
  "pickup_longitude": "90.412500",
  "destination_label": "Gulshan",
  "destination_latitude": "23.780600",
  "destination_longitude": "90.419300"
}
```

Returns 201 with a quote `id`, location fields, `distance_km`, `estimated_minutes`,
`fare`, `platform_fee`, `currency`, and `expires_at`. The quote is tied to this rider,
valid for ten minutes, and usable for one ride. Locations must be 100 m–200 km apart
as a straight-line distance. Estimates use Haversine distance times a configurable
road factor, not a road-routing provider. The quoted fare is fixed for the trip.

POST `/api/rides/`:

```json
{
  "quote_id": "<UUID returned by the quote endpoint>",
  "request_id": "<a new UUID generated by the client>",
  "payment_method": "CASH"
}
```

Returns 201 with a ride, immediately searching for eligible nearby drivers. The
request ID makes retries safe: resend the **same ID and details** after a network
timeout and receive the original ride with 200. Reusing the ID with different details
returns 409. A rider may have only one active ride. `CARD` is available only when the
server is configured for hosted checkout. Clients cannot set the fare.

GET `/api/rides/current/` returns the current ride, or an empty 200 response when
none is active. GET `/api/rides/` returns only the current user's ride history;
`?status=COMPLETED` or `?status=CANCELLED` filters it.
GET `/api/rides/<ride_uuid>/` returns a participant's ride detail, quote, permitted
contact details, events, ratings, and payment status.

## Driver offers and trip lifecycle

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/rides/offers/` | Current driver's unexpired nearby offers |
| POST | `/api/rides/offers/<offer_id>/accept/` | Accept an offer; returns ride |
| POST | `/api/rides/offers/<offer_id>/reject/` | Reject an offer |
| POST | `/api/rides/<ride_uuid>/actions/` | `action`, optional cancellation `reason` |

Offer IDs are integers; ride IDs are UUIDs. Offers contain route and fare information
without rider contact information. Acceptance rechecks driver eligibility and uses
row locks plus database constraints to prevent double assignment. An offer lasts
60 seconds. Up to five nearby drivers are offered a ride at once; rejected/expired
slots allow another wave of drivers. Unaccepted searches expire after 15 minutes.

Only the assigned driver can advance the trip, in this order:

```text
REQUESTED → SEARCHING → ACCEPTED
  → DRIVER_ARRIVING (action: arriving)
  → DRIVER_ARRIVED  (action: arrived)
  → IN_PROGRESS    (action: start)
  → COMPLETED      (action: complete)
```

Either participant may use `{"action":"cancel","reason":"Plans changed"}` before
the trip starts, producing `CANCELLED`. In-progress/completed rides cannot be
cancelled. Repeating an action already at its target status is harmless; attempts to
skip states return 409. Cancellation before a start creates no payment.

## Tracking, notifications, and ratings

GET `/api/rides/<ride_uuid>/tracking/` returns up to the most recent 1,000 stored
points in chronological order. Only participants can access this. The driver's
current location/contact details appear in ride detail only while assigned/active;
completed rides never reveal a driver's subsequent live location. A reported point
is stored at most once every five seconds for that ride. GPS positions are device
reports and are not proof of physical presence.

POST `/api/rides/<ride_uuid>/rating/` with `score` (1–5) and optional `comment`
(maximum 500 characters). Only completed-trip participants may rate, once each.
The backend chooses the other participant as recipient.

GET `/api/notifications/` lists the user's persisted notifications, newest first.
Use `?page=2` and the `next`/`previous` links to read older updates. In addition to
the standard pagination fields, `unread_count` counts unread notifications across
all pages for the authenticated user.
POST `/api/notifications/read/` marks their unread notifications as read.
Repeated calls are harmless; other users' notifications are unaffected. A
`notifications_read` WebSocket event updates the user's other open sessions.
Notifications are in-app; SMS, email, and mobile push delivery are not configured.
The browser activity list links ride-related updates to their trip details.

## WebSocket events

Connect to `/ws/events/` over `ws` locally or `wss` with HTTPS. Same-origin browser
sessions authenticate with their HTTP-only session cookie. Other clients send:

```json
{"type":"authenticate","access":"<JWT access token>"}
```

Send this as the first frame within ten seconds, not as a URL query parameter.
The server sends `{"event":"connected"}` after authentication. A connection only
receives events for its user. Possible update events are `notification`,
`offers_changed`, `notifications_read`, and `location`, optionally including `ride_id`. Refetch the
relevant REST resource on an event. Events are update hints, not the authoritative
state; reconnect and poll after missed messages.

Send `{"type":"ping"}` for `pong` and account/session revalidation. Expired JWTs or
invalidated sessions cannot receive further events; reconnect with renewed credentials.
Connections close with 4401 for authentication failure and 4403 for an untrusted
browser origin. The included web client polls every eight seconds as a fallback.

## Payments and earnings

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/payments/<ride_uuid>/cash/` | Assigned driver confirms cash actually received after completion |
| POST | `/api/payments/<ride_uuid>/checkout/` | Rider gets `{url}` for their completed, unpaid card trip |
| POST | `/api/payments/webhook/stripe/` | Stripe-signed server webhook; no browser/JWT identity |
| GET | `/api/payments/earnings/` | Driver's totals and today's summary, grouped by currency |

Completion creates one immutable fare/payment record. Cash collection and duplicate
provider callbacks are idempotent. The browser redirect from Stripe never marks a
trip as paid. Signed callbacks must match the stored session, amount, currency, and
payment metadata. Before replacing an expired checkout, the server reconciles its
provider status to avoid asking for payment again when a callback was delayed.
In that case checkout may return `{url: null}`; refetch the ride payment status.

Earnings expose gross fares, platform fees, net earnings, paid/pending net amounts,
cash collected, and card net amounts. These are accounting totals, not bank payouts.
Cash received includes the platform fee; settlement and payouts are separate
operational responsibilities. Card testing uses mocks unless merchant credentials
and a webhook destination are configured.

The session-authenticated browser page `/app/rides/<ride_uuid>/receipt/` provides
a printable receipt for a completed, paid trip. An unpaid completed trip displays
a pending summary instead. Only that trip's rider and assigned driver can access
it; the driver's copy includes fee and net-earnings totals. Amounts come from the
stored payment record. Private pages are marked `no-store` and cannot be changed
with POST requests.
