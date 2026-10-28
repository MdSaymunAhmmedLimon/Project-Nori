# Shared REST API

Web pages use Django sessions plus CSRF; Android and other API clients use JWT.
Endpoints use JSON and trailing slashes, except CSV reports and calendar downloads.
Protected requests without a valid identity return 401; insufficient roles return
403; another user's private
resource returns 404. Invalid input returns 400. A ride action conflicting with
current state returns 409. Throttled requests return 429.

List responses use `{count, next, previous, results}` with 20 records per page.
Pass `?page=2` for subsequent pages. Times are ISO 8601 UTC; monetary values are
decimal strings with a separate currency. Latitude and longitude accept up to six
decimal places. Do not send owner IDs, roles, prices, or privilege flags unless
explicitly listed as writable for that endpoint.

## Reservations

| Method | Path | Body / result |
| --- | --- | --- |
| GET | `/api/rides/schedule/` | Own reservations, ordered by pickup time; `?pending=1` excludes cancelled/dispatched entries; `page` paginates |
| POST | `/api/rides/schedule/` | `quote_id`, `scheduled_for` (ISO 8601 with offset), optional `payment_method` (`CASH` default); 201 reservation |
| PATCH | `/api/rides/schedule/{uuid}/` | Required `scheduled_for` only; 200 updated reservation |
| DELETE | `/api/rides/schedule/{uuid}/` | Cancel an undispatched reservation; 200 reservation with `cancelled_at` |
| GET | `/api/rides/schedule/{uuid}/calendar/` | Authenticated owner's upcoming reservation as a private, uncached `text/calendar` attachment |

Pickup must be more than 15 minutes and no more than seven days from the current
time. An existing reservation must still be in the future to change its time;
cancelled/dispatched/due reservations return 409 for edits and calendar downloads.
Changes preserve route, fare, and payment method. Another user's reservation is
404. Calendar downloads require manual import and do not sync later changes.

Creation requires a fresh, unused quote. Repeating identical booking details with
the same reserved quote returns that reservation; changing details on the same
quote returns 409. Immediate ride requests cannot consume a reserved quote.
The reservation's fare remains valid at scheduled dispatch even after quote expiry.
Promotional codes are not supported on scheduled bookings.

`GET /api/rides/current/` processes at most one due reservation belonging to the
current rider. Eligible driver offer polling and the existing dispatch worker
also process bounded batches. A reservation more than 15 minutes late is cancelled
when processed, with an in-app notification. Driver availability is not guaranteed.

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

Browser password recovery starts at `/password/reset/`. Enter your username,
answer the security question configured on your account, and choose a new
password. Accounts without a security question cannot use this recovery flow.
A successful reset invalidates prior sessions and API tokens. Recovery does
not send email or require SMTP configuration. SMTP is used for SOS alerts to
the operations address configured by `ADMIN_EMAIL`.

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

GET `/api/notifications/summary/` returns only `{unread_count}` for the
authenticated user. The dashboard uses this smaller response while Activity is
closed; the full paginated feed remains available when Activity is opened.

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

## Saved places and booking shortcuts

`PATCH /api/rides/saved-places/<id>/` updates a rider's own saved place. Accepts
any subset of `label`, `address`, `latitude`, and `longitude`; omitted fields keep
their values. Labels must be unique for that rider, ignoring case. Conflicting
labels return HTTP 409, invalid data returns 400, and another user's place returns
404, including for staff. Create and edit operations serialize per rider to keep
duplicate-label checks consistent. The existing list remains paginated.

The browser's **Ride again** action copies a completed or cancelled trip's route,
vehicle class, and intermediate stops into the booking form. It obtains current
ride state first and blocks rebooking during an active trip. The rider must request
a fresh quote through `/api/rides/quotes/` and explicitly confirm through
`/api/rides/`; the old fare, payment, promo, and trip are never reused. Swapping
pickup and destination reverses intermediate stops and invalidates any displayed
quote. Saved-place shortcuts select either endpoint using the stored address and
coordinates; editing a typed address clears its previous coordinates.

## Favourite drivers and lost items

All endpoints below require authentication. Rider actions are limited to their
own completed trips; staff status alone does not grant access to another rider's
reports or favourites.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/api/rides/favourite-drivers/` | Paginated saved drivers, names, and ratings; no phone numbers or email addresses |
| POST | `/api/rides/<ride_uuid>/favourite-driver/` | Save the trip's driver; 201 for new, 200 if already saved |
| DELETE | `/api/rides/favourite-drivers/<id>/` | Remove the rider's own favourite; 204 on success |
| GET | `/api/rides/lost-items/` | Paginated reports belonging to the rider, including status and resolution time |
| POST | `/api/rides/<ride_uuid>/lost-items/` | Submit `{description}` (1–500 characters) after a completed trip |

Saving a favourite does not change driver matching or reserve a driver. Lost-item
reports allow one open report per trip: retrying the same description returns the
existing report (200), while a different description returns 409. New reports
return 201. Riders receive an in-app notification on creation and status changes.

Staff with the `rides.change_lostitem` permission can manage reports in Django
admin, linked from Operations. Statuses are `REPORTED`, `CONTACTING`, `FOUND`,
`RETURNED`, and `NOT_FOUND`. The last two resolve the report; reopening clears its
resolution time and is blocked if another report for that trip is open. Internal
staff notes are excluded from rider responses and notifications. Reports cannot
be created or deleted through admin, preserving the rider's original description.

## Payments and earnings

| Method | Path | Behavior |
| --- | --- | --- |
| POST | `/api/payments/<ride_uuid>/cash/` | Assigned driver confirms cash actually received after completion |
| POST | `/api/payments/<ride_uuid>/checkout/` | Rider gets `{url}` for their completed, unpaid card trip |
| POST | `/api/payments/webhook/stripe/` | Stripe-signed server webhook; no browser/JWT identity |
| GET | `/api/payments/earnings/` | Driver's totals and today's summary, grouped by currency |

`GET /api/rides/` accepts optional `q` (pickup/destination text, up to 200
characters), `status`, `payment_method` (`CASH` or `CARD`), `date_from`, and
`date_to`. Dates use `YYYY-MM-DD`, include both boundaries, and filter trip request
time in UTC. Results remain paginated and restricted to the requesting user's
trips. Invalid filters or reversed dates return HTTP 400.

`GET /api/rides/export/` uses the same filters and access rules, returning a UTF-8
CSV attachment with all matching trips, independent of pagination. Reports over
5,000 trips or 4 MB return HTTP 400 with a request to narrow the date range. Quoted fare,
final payment amount, payment status, and cancellation fee have separate columns;
missing payment amounts are blank. Route labels are escaped for spreadsheet
formula safety. Responses are marked `no-store`.

`GET /api/payments/earnings/` accepts the same optional date range, based on trip
completion time in UTC. Existing `totals` and `today` fields remain; `totals` also
includes `average_net`. The `daily` array contains per-date, per-currency trips,
gross fares, platform fees, net, paid net, and pending net. It covers at most 31
calendar days ending on `date_to` (today if omitted), intersected with the selected
range. `trend_from` and `trend_to` describe this window. Summary totals cover the
full selected range. Fare earnings exclude tips and keep currencies separate.

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

## Notification and cancellation additions

Notification list entries now include `title`, `category`, and a local `action_url`
alongside the existing fields. `POST /api/notifications/{id}/read/` marks one of the
authenticated user's messages read. The existing summary and mark-all endpoints
remain unchanged; other users' messages return 404.

`GET /api/rides/{uuid}/actions/` previews cancellation for a participant and returns
`fee`, `currency`, `refund_note`, and current `status`. Confirm with
`POST /api/rides/{uuid}/actions/` and
`{"action":"cancel","reason":"Plans changed","expected_cancellation_fee":"0.00"}`.
A changed fee or an un-cancellable status returns 409. Fees remain server-authoritative.

Position management uses session-authenticated, CSRF-protected web forms under
`/positions/`; it is not exposed as a public role-editing API. See
[the workflow and permission model](POSITIONS.md).

## Road routing endpoints

Authenticated clients can `POST /api/rides/route/` with
`{"coordinates":[{"latitude":"23.810300","longitude":"90.412500"},{"latitude":"23.780600","longitude":"90.419300"}]}`.
Supply 2–10 locations in travel order. The response contains `distance_meters`,
`duration_seconds`, and GeoJSON `geometry` (`LineString`, longitude/latitude order).
Unreachable locations return HTTP 400; a routing outage returns HTTP 503.

`POST /api/rides/quotes/` uses the road route for `distance_km`, `estimated_minutes`,
and the fare, and returns the saved `route_geometry`. Up to eight intermediate
`stops` are included in the route. No straight-line fare fallback is used.

## Live device movement and messages

Drivers send GPS coordinates to `POST /api/drivers/location/` at most once every
five seconds while sharing from the web app. The assigned rider receives location
events and can read the latest coordinates from their ride detail response. The
blue device marker is local to the current browser; rider GPS is not uploaded.

Participants can `GET /api/rides/{uuid}/messages/` to read the conversation and
`POST` with `{"content":"I am at the entrance."}` to send a message. Sending is
available from driver acceptance until the trip ends; completed/cancelled trips
are read-only. Messages are limited to 500 characters. The recipient receives a
`chat_message` websocket event, and the browser polls as a fallback. Nonparticipants
receive 404, and sending before assignment or after the ride ends returns 409.
