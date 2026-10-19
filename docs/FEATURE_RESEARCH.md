# Feature review and reservation update

Research reviewed on 19 October 2026. This is a targeted review of relevant
official product and platform documentation, not an exhaustive internet survey.

## Project findings

Nori is a Django/DRF application with a vanilla JavaScript dashboard, Leaflet
maps, Channels notifications, and optional Stripe payments. Existing functionality
includes rider/driver accounts, driver approval, vehicle classes, fare quotes,
matching, trip tracking, cash payments, ratings, tips, saved places, reservations,
trip sharing, emergency contacts, favourites, lost-item support, and trip reports.
Existing uncommitted improvements to reports, booking shortcuts, and support were
preserved during this update.

Production uses Vercel's Django preset, hosted PostgreSQL through `DATABASE_URL`,
and Redis through `REDIS_URL`. Local tests use an isolated SQLite database. The
production database was not accessed or changed during this review.

## Research and decisions

| Reference | Finding | Project decision |
| --- | --- | --- |
| [Lyft scheduled rides](https://help.lyft.com/hc/en-us/all/articles/115013078668-Scheduled-rides-for-passengers) | Riders can manage scheduled pickups, including editing or cancellation. | Add pickup-time editing to existing reservations; retain Nori's own seven-day booking limit and fare rules. |
| [Lyft favourite drivers](https://www.lyft.com/blog/posts/your-ride-your-way-now-you-can-favorite-your-drivers) | Favourites can influence future scheduled matching. | Nori already stores favourites. Matching priority is a possible later improvement; this update does not claim preferential matching. |
| [Uber pickup preferences](https://help.uber.com/en/riders/article/pickup-preferences?nodeId=79da7e22-af70-4634-9dda-d41320d2e6a7) | Persistent pickup instructions help riders coordinate with drivers. | Useful future work. Nori has a ride-preferences data field, but persistence, validated input, driver visibility, and privacy controls need a complete implementation. |
| [iCalendar RFC 5545](https://datatracker.ietf.org/doc/rfc5545/) | Calendar events support UTC times, reminder alarms, text escaping, and folded UTF-8 lines. | Add a calendar-file download with a 15-minute reminder. This is our proposed convenience feature, not a claim about a competitor's implementation. |
| [Vercel Marketplace storage](https://vercel.com/docs/marketplace-storage) | Vercel connects projects to managed databases with environment variables. | Reuse existing PostgreSQL tables and configuration. No new storage service, dependency, or migration. |

## Implemented behavior

- **My trips** lists pending reservations separately, with pagination. Completed
  or cancelled reservations no longer hide upcoming entries on the first page.
- Riders can change a future, undispatched reservation to a pickup more than
  15 minutes and up to seven days ahead. Route, fare, and payment method stay
  unchanged. Editing remains intact while the dashboard polls.
- **Add to calendar** downloads a private `.ics` event with an estimated trip
  duration and a reminder 15 minutes before pickup. Import it into a compatible
  calendar app. It is a snapshot: changes and cancellation require manually
  updating/removing the event. Calendar applications control reminder delivery.
- The date picker labels the device timezone, enforces the booking window, and
  rejects invalid local times, including nonexistent daylight-saving times.
- New reservations require a fresh quote. Repeated submission of identical
  reservation details returns the existing reservation. A reserved quote cannot
  simultaneously be used for an immediate ride.
- Due reservations retain their agreed fare even after the original quote
  expires. Dispatch, editing, and cancellation use consistent transaction locks.
- Rider polling processes one due reservation for that rider. Eligible drivers'
  offer polling processes up to five due reservations. Management commands share
  the same dispatch service. There is no long-running process inside a function.
- Undispatched reservations more than 15 minutes late are cancelled when next
  processed, with an in-app notification. No old ride launches on reconnect.
- Scheduled bookings hide the immediate-ride promo control because scheduling
  does not support applying those discounts.

## Operational limits and verification

Matching still depends on available drivers. With no active clients, unattended
dispatch requires the existing external dispatcher; the browser and calendar do
not guarantee execution or pickup. See [Vercel deployment](VERCEL.md).

Run the automated suite and the desktop/mobile browser workflow as documented in
the README. PostgreSQL concurrency tests require an isolated PostgreSQL test
database; SQLite skips these checks. Deployment credentials and live production
services are not needed for the local suite.
