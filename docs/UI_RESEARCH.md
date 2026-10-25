# Rider experience update

Reviewed official Pathao and Uber product pages on 25 October 2026. Product
availability varies by market; these references inform Nori's interaction design.

## Research and implementation

| Reference | Relevant pattern | Nori implementation |
| --- | --- | --- |
| [Pathao Bike](https://pathao.com/bike/) and [Pathao Car](https://pathao.com/car/) | Choose a service, enter pickup and destination, then request the ride. Different vehicle options serve different journeys. | Illustrated, keyboard-accessible Car, Moto, Comfort, and XL selection using existing vehicle classes and fare APIs. Public service links preselect the chosen class after login. |
| [Uber rider guide](https://www.uber.com/us/en/ride/how-it-works/) | Destination-first entry, separate immediate/scheduled entry points, map tracking, and checking driver and vehicle details. | Clear route entry, current-location and swap actions, reservation shortcut, map overview control, and safety guidance. Existing tracking and trip lifecycle remain connected. |
| [Uber upfront pricing](https://www.uber.com/us/en/ride/how-it-works/upfront-pricing/) | Show an estimated price before the rider requests a ride. | Explicit route and review stages, automatic focus on the fare review, readable vehicle names, and invalidation when route, vehicle, or booking mode changes. No invented prices or arrival times. |
| [Pathao safety overview](https://pathao.com/blog/safety-features/) | Bring safety information together before the trip; make driver checks and trip sharing discoverable. | An accessible safety dialog, driver/plate guidance, explanation of existing trip sharing, and a direct link to emergency-contact management. |

The interface retains Nori's green identity. The public landing page uses the
configured Leaflet map, supported by locally served icons and vehicle artwork.
On small screens, the map is compact, vehicle choices fit in a single row, and
existing bottom navigation remains available.

Additional usability work includes saved-place booking shortcuts, native radio
controls, search suggestions supporting arrows/Enter/Escape, screen-reader
selection state, loading indicators, mobile input sizing, dialog focus handling,
and reduced-motion support.

## Scope and limits

- Nori retains its own fare rules and scheduling window: more than 15 minutes
  and up to seven days ahead. Availability is not guaranteed.
- Cash and the existing configured card integration remain the payment options.
  Pathao's bKash, insurance, and other operational services are not claimed as
  Nori features.
- The safety dialog describes the existing SOS behavior accurately: it records
  an alert; it does not call emergency services.
- Map lines connect selected coordinates, rather than providing road navigation.
  The interface says so. Map availability depends on the configured tile service;
  location search remains available if tiles fail.
- No new infrastructure, dependencies, database migrations, or deployment steps
  are required for this update.

## Verification

Story: a rider chooses a service, selects locations, requests an API fare, reviews
the result, and confirms or schedules a ride; a driver completes the existing
trip lifecycle and the rider can inspect history and receipts.

- `python manage.py test --settings=ride_sharing_app.test_settings --noinput`:
  215 tests run, passing with four PostgreSQL-specific tests skipped on SQLite.
- `python scripts/browser_smoke.py`: exercises the browser, REST API, disposable
  database, and WebSockets through booking, driver matching, trip completion,
  cash payment, receipts, and reservations. Also checks the new vehicle payload,
  booking-mode invalidation, keyboard search, safety dialog, saved-place shortcut,
  and mobile/tablet overflow at 360, 390, and 768 pixels.
- Browser checks use local disposable accounts and stub external map tiles;
  screenshots in `artifacts/browser/` therefore show a grid instead of live city
  imagery. These checks do not verify live tile delivery or production services.
