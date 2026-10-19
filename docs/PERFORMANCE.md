# Performance update

## Measured query reductions

The regression fixtures use 20 completed trips with driver profiles, vehicles,
ratings, stops, tips, and payments; 20 reservations; and 20 chat messages.
Query counts were captured against the isolated SQLite test database with
authenticated API requests. Authentication/session lookup queries in production
are additional. These numbers measure database round trips, not production latency.

| Request | Before | After |
| --- | ---: | ---: |
| Trip history, 20 trips | 104 | 5 |
| Upcoming reservations, 20 entries | 22 | 3 |
| Chat history, 20 messages | 24 | 2 |

`rides/tests/test_performance.py` enforces these budgets and verifies the returned
data and ownership restrictions. Public trip sharing also has a one-query budget.
Notification summary polling performs one count query and returns no message bodies.

## Backend changes

- Trip reads join single related records and fetch collections in batches. Rating
  averages use indexed, correlated aggregates instead of separate database calls
  for every rider and driver. Nullable driver/profile/vehicle relationships remain
  supported. Mutation/locking queries stay separate from these read queries.
- Reservation and offer lists prefetch stops; chat joins senders. Tracking and
  chat ownership checks avoid loading unrelated trip details.
- Dispatch stops early when all offer slots are filled, excludes previously
  offered drivers in SQL, and fetches only candidate IDs and coordinates. Existing
  eligibility, radius, candidate bounds, and transaction checks remain in place.
- Admin counts are combined into aggregate queries rather than multiple scans
  for each metric. Vehicle details use a join in the verification queue.
- Date filters compare timestamp bounds directly, allowing ordinary database
  indexes to be used. End dates remain inclusive using an exclusive next-midnight
  bound. Tests cover daylight-saving transitions and the maximum supported date.

## Database indexes

Two additive migrations create eight indexes for the actual read patterns:

- Rider and driver trip-history ordering.
- Completed driver trips by completion time.
- Due pending reservations and each rider's pending reservations.
- Latest tracking points for a ride.
- Notification feed ordering and unread notification counts.

The migration files are `rides/migrations/0005_performance_indexes.py` and
`notifications/migrations/0002_performance_indexes.py`. They do not change columns,
delete data, or require a replacement database. Vercel's existing build script
runs Django migrations. Include both files in the GitHub push.

## Browser changes

- The dashboard fetches only an unread count while Activity is closed. Opening
  Activity fetches the full current page immediately.
- Independent refresh requests run in parallel. A refresh waits for all its
  updates to settle before starting another.
- Unchanged notification lists and map routes are reused instead of rebuilt on
  every poll. Driver location markers still update normally.
- Place searches debounce input for 300 ms, abort obsolete requests, and ignore
  stale responses. Aborting a browser request does not guarantee cancellation of
  server work already underway.
- WebSocket retries back off from six seconds to a maximum of one minute.
  Eight-second polling remains available. Hidden tabs ignore socket-triggered
  refreshes and refresh immediately when shown again. Driver GPS sharing retains
  its existing cadence.

## Verification and limits

```powershell
python manage.py test --settings=ride_sharing_app.test_settings --noinput
python manage.py makemigrations --check --dry-run --settings=ride_sharing_app.test_settings
python scripts/browser_smoke.py
```

The browser workflow covers notification summaries, unchanged DOM/map reuse,
registration, driver matching, trip completion, receipts, reservation editing,
calendar downloads, and mobile layouts. Local tests apply the new migrations to
disposable databases. PostgreSQL concurrency tests require a PostgreSQL test
database and are skipped under SQLite.

Vercel configuration, Redis requirements, production database credentials, and
payment behavior are unchanged. No new dependency or external service is needed.
Production execution plans, database size, connection latency, and actual Vercel
function durations have not been measured; the live database was not accessed.
