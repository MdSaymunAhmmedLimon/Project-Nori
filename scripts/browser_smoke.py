"""Exercise the rider/driver web workflow in a disposable local database."""

import os
import asyncio
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from playwright.sync_api import expect, sync_playwright

    artifacts = ROOT / "artifacts" / "browser"
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="nori-browser-", dir=ROOT / ".venv", ignore_cleanup_errors=True) as temporary:
        assert Path(temporary).resolve().is_relative_to(ROOT.resolve())
        os.environ.update({"DJANGO_SETTINGS_MODULE":"ride_sharing_app.browser_test_settings",
            "BROWSER_TEST_DB":str(Path(temporary) / "browser.sqlite3"),
            "NORI_LOCAL_DATABASE":str(Path(temporary) / "browser.sqlite3"),
            "DJANGO_SECRET_KEY":secrets.token_urlsafe(64), "TEMP":temporary, "TMP":temporary, "TMPDIR":temporary})
        import django
        django.setup()
        from django.contrib.auth import get_user_model
        from django.core.management import call_command
        call_command("migrate", verbosity=0)
        password = secrets.token_urlsafe(24)
        security_answer = secrets.token_urlsafe(24)
        admin_user = get_user_model().objects.create_superuser(username="browser_admin", email="admin@example.com", password=password)
        from django.utils import timezone
        from notifications.models import Notification
        Notification.objects.bulk_create([
            Notification(user=admin_user, message=f"Activity update {number}", read_at=timezone.now() if number >= 5 else None)
            for number in range(25)
        ])
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        base = f"http://127.0.0.1:{port}"
        log_path = artifacts / "server.log"
        with log_path.open("w", encoding="utf-8") as log:
            server = subprocess.Popen(
                [sys.executable, "run.py", "--port", str(port), "--no-reload"],
                cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=log,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            try:
                for _ in range(100):
                    try:
                        with urlopen(base, timeout=1) as response:
                            if response.status == 200:
                                break
                    except (URLError, TimeoutError):
                        if server.poll() is not None:
                            raise RuntimeError(f"Server exited; see {log_path}")
                        time.sleep(0.1)
                else:
                    raise RuntimeError(f"Server did not start; see {log_path}")
                if os.name == "nt":
                    # Daphne uses SelectorEventLoop; Playwright needs subprocess support.
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                with sync_playwright() as playwright:
                    executable = os.environ.get("BROWSER_EXECUTABLE")
                    edge = Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")
                    if not executable and edge.exists():
                        executable = str(edge)
                    browser = playwright.chromium.launch(headless=True, executable_path=executable, args=["--disable-gpu"])
                    errors = []
                    pages = []

                    def new_page(mobile=False):
                        context = browser.new_context(viewport={"width":390 if mobile else 1440, "height":844 if mobile else 1000},
                            geolocation={"latitude":23.8103,"longitude":90.4125}, permissions=["geolocation"], timezone_id="Asia/Dhaka")
                        # Never make automated tile requests to community map servers.
                        def intercept(route):
                            if route.request.url.startswith(base):
                                route.continue_()
                            elif "tile.openstreetmap.org" in route.request.url:
                                route.fulfill(content_type="image/svg+xml", body='<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256"><rect width="256" height="256" fill="#e6ecdd"/><path d="M0 64H256M0 192H256M64 0V256M192 0V256" stroke="#f7f8f2" stroke-width="12"/></svg>')
                            else:
                                route.abort()
                        context.route("**/*", intercept)
                        page = context.new_page()
                        page.on("pageerror", lambda error: errors.append(str(error)))
                        page.set_default_timeout(15000)
                        pages.append(page)
                        return page

                    def register(page, username, role):
                        page.goto(base + "/register/")
                        page.locator('[name="username"]').fill(username)
                        page.locator('[name="email"]').fill(username + "@example.com")
                        page.locator('[name="first_name"]').fill(username.title())
                        page.locator('[name="role"]').select_option(role)
                        page.locator('[name="password1"]').fill(password)
                        page.locator('[name="password2"]').fill(password)
                        page.locator('[name="security_question"]').select_option("What was the name of your first pet?")
                        page.locator('[name="security_answer"]').fill(security_answer)
                        page.get_by_role("button", name="Create account").click()
                        page.wait_for_url(base + "/app/")
                        expect(page.locator('#connection-status')).to_have_text("Live updates connected", timeout=15000)

                    def plan_trip(page):
                        for field, value in {"pickup_label":"Banani", "destination_label":"Gulshan", "pickup_latitude":"23.810300", "pickup_longitude":"90.412500", "destination_latitude":"23.780600", "destination_longitude":"90.419300"}.items():
                            control = page.locator(f'#quote-form [name="{field}"]')
                            if field.endswith("_label"):
                                control.fill(value)
                            else:
                                # Map/search selections now store coordinates in hidden inputs.
                                control.evaluate("(node, value) => { node.value = value; node.dispatchEvent(new Event('change', {bubbles: true})); }", value)

                    rider = new_page()
                    rider.goto(base)
                    rider.screenshot(path=str(artifacts / "home-desktop.png"), full_page=True)
                    register(rider, "browser_rider", "RIDER")
                    rider.wait_for_function("performance.getEntriesByType('resource').some(entry => entry.name.includes('/api/notifications/summary/'))")
                    assert not rider.evaluate("performance.getEntriesByType('resource').some(entry => entry.name.includes('/api/notifications/?'))"), 'Hidden Activity should only fetch its unread count'
                    rider.screenshot(path=str(artifacts / "rider-dashboard.png"), full_page=True)
                    rider.locator('[data-tab="account"]').click()
                    for field, value in {"label":"Home", "address":"Banani 11", "latitude":"23.793700", "longitude":"90.404300"}.items():
                        rider.locator(f'#save-place-form [name="{field}"]').fill(value)
                    rider.locator('#save-place-form [type="submit"]').click()
                    expect(rider.locator('#saved-places-list')).to_contain_text("Home")
                    rider.locator('[data-edit-place]').click()
                    rider.locator('#save-place-form [name="label"]').fill("Work")
                    rider.locator('#save-place-form [name="address"]').fill("Banani office")
                    rider.locator('#save-place-form [type="submit"]').click()
                    expect(rider.locator('#saved-places-list')).to_contain_text("Work")
                    expect(rider.locator('#save-place-heading')).to_have_text("Add a place")
                    rider.screenshot(path=str(artifacts / "saved-places.png"), full_page=True)
                    rider.locator('#saved-places-list [data-use-place="pickup"]').click()
                    expect(rider.locator('#pickup-label')).to_have_value("Banani office")
                    expect(rider.locator('#pickup-latitude')).to_have_value("23.793700")
                    rider.locator('.booking-saved-places summary').click()
                    rider.locator('#booking-saved-places-list [data-use-place="destination"]').click()
                    expect(rider.locator('#destination-label')).to_have_value("Banani office")
                    rider.locator('.booking-saved-places summary').click()
                    plan_trip(rider)
                    rider.get_by_role("button", name="See my fare").click()
                    expect(rider.locator('#quote-result')).to_be_visible()
                    rider.locator('#swap-route').click()
                    expect(rider.locator('#quote-result')).to_be_hidden()
                    expect(rider.locator('#pickup-label')).to_have_value("Gulshan")
                    expect(rider.locator('#destination-label')).to_have_value("Banani")
                    expect(rider.locator('#pickup-latitude')).to_have_value("23.780600")
                    rider.locator('#swap-route').click()
                    expect(rider.locator('#pickup-label')).to_have_value("Banani")
                    rider.screenshot(path=str(artifacts / "booking-shortcuts.png"), full_page=True)
                    driver = new_page()
                    register(driver, "browser_driver", "DRIVER")
                    driver.locator('[data-tab="account"]').click()
                    driver.locator('#driver-profile-form [name="license_number"]').fill("DL-BROWSER-100")
                    from datetime import date, timedelta
                    driver.locator('#driver-profile-form [name="license_expiry_date"]').fill((date.today()+timedelta(days=365)).isoformat())
                    driver.get_by_role("button", name="Save driver profile").click()
                    expect(driver.locator('#toast')).to_contain_text("saved for review")
                    for field, value in {"license_plate":"BROWSER-100", "make":"Toyota", "model":"Corolla", "color":"Silver", "year":"2024", "seats":"4"}.items():
                        driver.locator(f'#vehicle-form [name="{field}"]').fill(value)
                    driver.get_by_role("button", name="Save vehicle").click()
                    expect(driver.locator('#toast')).to_contain_text("Vehicle saved")

                    admin = new_page()
                    admin.goto(base + "/admin/login/")
                    admin.locator('[name="username"]').fill("browser_admin")
                    admin.locator('[name="password"]').fill(password)
                    admin.get_by_role("button", name="Log in").click()
                    admin.wait_for_url(base + "/admin/")
                    admin.goto(base + "/admin/drivers/driverprofile/")
                    admin.locator('[name="_selected_action"]').check()
                    admin.locator('select[name="action"]').first.select_option("approve_drivers")
                    admin.locator('button[name="index"]').first.click()
                    expect(admin.locator('.messagelist')).to_contain_text("Approved 1")

                    admin.goto(base + "/app/")
                    expect(admin.locator('#notification-count')).to_have_text("5")
                    admin.locator('[data-tab="notifications"]').click()
                    expect(admin.locator('#notifications-page')).to_have_text("Page 1 · 25 updates")
                    expect(admin.locator('#notifications-prev')).to_be_disabled()
                    admin.locator('#notifications-next').click()
                    expect(admin.locator('#notifications-page')).to_have_text("Page 2 · 25 updates")
                    expect(admin.locator('#notifications-list .notification')).to_have_count(5)
                    expect(admin.locator('#notifications-next')).to_be_disabled()
                    admin.locator('#mark-read').click()
                    expect(admin.locator('#notification-count')).to_be_hidden()
                    expect(admin.locator('#notifications-list .notification.read')).to_have_count(5)
                    expect(admin.locator('#mark-read')).to_be_disabled()
                    admin.locator('#notifications-list .notification').first.evaluate("node => node.dataset.renderCheck = 'preserved'")
                    with admin.expect_response('**/api/notifications/?page=2') as next_activity:
                        pass
                    next_activity.value.body()
                    expect(admin.locator('#notifications-list .notification').first).to_have_attribute('data-render-check', 'preserved')
                    admin.screenshot(path=str(artifacts / "activity-history.png"), full_page=True)

                    driver.reload()
                    expect(driver.locator('#driver-review')).to_have_text("Approved")
                    driver.get_by_role("button", name="Share location").click()
                    expect(driver.locator('#gps-status')).to_contain_text("Location updated")
                    driver.get_by_role("button", name="Stop sharing", exact=True).click()
                    expect(driver.locator('#gps-status')).to_contain_text("Location sharing is off")
                    driver.get_by_role("button", name="Share location", exact=True).click()
                    expect(driver.get_by_role("button", name="Stop sharing", exact=True)).to_be_visible()
                    driver.get_by_role("button", name="Go online", exact=True).click()
                    expect(driver.locator('#availability-badge')).to_have_text("Online")
                    plan_trip(rider)
                    pending_quotes = []
                    rider.route("**/api/rides/quotes/", lambda route: pending_quotes.append(route))
                    with rider.expect_request("**/api/rides/quotes/"):
                        rider.get_by_role("button", name="See my fare").click()
                    rider.locator('#destination-label').fill("Changed destination")
                    pending_quotes.pop().continue_()
                    expect(rider.get_by_role("button", name="See my fare")).to_be_enabled()
                    expect(rider.locator('#quote-result')).to_be_hidden()
                    rider.unroute("**/api/rides/quotes/")
                    rider.locator('#destination-label').fill("Gulshan")
                    rider.get_by_role("button", name="See my fare").click()
                    expect(rider.locator('#request-ride')).to_be_visible()
                    rider.get_by_role("button", name="Request a ride").click()
                    expect(rider.locator('#trip-content h2')).to_have_text("Finding your driver")
                    expect(driver.get_by_role("button", name="Accept ride")).to_be_visible(timeout=20000)
                    driver.get_by_role("button", name="Accept ride").click()
                    expect(rider.locator('#trip-content h2')).to_have_text("Your driver is assigned", timeout=20000)
                    rider.evaluate('''() => {
                        window.routeBuilds = 0;
                        const original = L.polyline;
                        L.polyline = (...args) => { window.routeBuilds++; return original(...args); };
                    }''')
                    with rider.expect_response('**/api/rides/current/') as next_trip:
                        pass
                    next_trip.value.body()
                    assert rider.evaluate('window.routeBuilds') == 0, 'Polling must reuse an unchanged route'
                    driver.screenshot(path=str(artifacts / "driver-active-trip.png"), full_page=True)
                    for label in ("Head to pickup", "Mark as arrived", "Start trip", "Complete trip"):
                        driver.get_by_role("button", name=label).click()
                    expect(driver.locator('#trip-content h2')).to_have_text("You have arrived")
                    driver.get_by_role("button", name="Confirm cash received").click()
                    expect(driver.locator('.receipt')).to_contain_text("Paid")
                    expect(rider.locator('#rating-form')).to_be_visible(timeout=20000)
                    rider.locator('#rating-form textarea').fill("A smooth browser test journey.")
                    rider.get_by_role("button", name="Send rating").click()
                    expect(rider.locator('#trip-content')).to_contain_text("Thanks for sharing")
                    rider.locator('#save-favourite-driver').click()
                    expect(rider.locator('#save-favourite-driver')).to_have_text("Driver saved")
                    rider.locator('.trip-support summary').click()
                    rider.locator('#lost-item-form textarea').fill("Blue backpack on the back seat")
                    rider.locator('#lost-item-form [type="submit"]').click()
                    expect(rider.locator('#toast')).to_contain_text("Report received")
                    rider.locator('#view-lost-items').click()
                    expect(rider.locator('#favourite-drivers-list')).to_contain_text("Browser_Driver")
                    expect(rider.locator('#lost-items-list')).to_contain_text("Blue backpack")
                    expect(rider.locator('#lost-items-list .pill')).to_have_text("Reported")
                    report = rider.request.get(base + "/api/rides/lost-items/").json()["results"][0]
                    admin.goto(base + "/operations/")
                    admin.get_by_role("link", name="Manage lost items").click()
                    admin.goto(base + f"/admin/rides/lostitem/{report['id']}/change/")
                    admin.locator('[name="status"]').select_option("FOUND")
                    admin.locator('[name="admin_note"]').fill("Internal follow-up details")
                    admin.get_by_role("button", name="Save", exact=True).click()
                    expect(rider.locator('#lost-items-list .pill')).to_have_text("Found", timeout=20000)
                    expect(rider.locator('#lost-items-list')).not_to_contain_text("Internal follow-up details")
                    admin.goto(base + f"/admin/rides/lostitem/{report['id']}/change/")
                    admin.locator('[name="status"]').select_option("RETURNED")
                    admin.get_by_role("button", name="Save", exact=True).click()
                    rider.locator('[data-tab="account"]').click()
                    expect(rider.locator('#lost-items-list .pill')).to_have_text("Returned")
                    rider.screenshot(path=str(artifacts / "trip-support.png"), full_page=True)
                    rider.locator('[data-remove-favourite]').click()
                    expect(rider.locator('#favourite-drivers-list')).to_contain_text("No favourite drivers yet")
                    rider.locator('[data-tab="ride"]').click()
                    expect(rider.locator('#save-favourite-driver')).to_be_enabled()
                    rider.locator('#save-favourite-driver').click()
                    expect(rider.locator('#save-favourite-driver')).to_have_text("Driver saved")
                    rider.get_by_role("link", name="View receipt").click()
                    expect(rider.locator('.receipt-document')).to_contain_text("Payment received.")
                    expect(rider.get_by_role("button", name="Print / save PDF")).to_be_visible()
                    rider.screenshot(path=str(artifacts / "trip-receipt.png"), full_page=True)
                    rider.pdf(path=str(artifacts / "trip-receipt.pdf"), format="A4")
                    rider.get_by_role("link", name="Back to trip").click()
                    expect(rider.locator('#trip-content h2')).to_have_text("You have arrived")
                    driver.locator('[data-tab="earnings"]').click()
                    expect(driver.locator('#earnings-content')).to_contain_text("Your net earnings")
                    expect(driver.locator('#earnings-content .report-table tbody tr')).to_have_count(1)
                    driver.locator('#earnings-filters [name="date_to"]').fill("2000-01-01")
                    driver.get_by_role("button", name="Apply dates").click()
                    expect(driver.locator('#earnings-content')).to_contain_text("No completed trips in this period")
                    driver.get_by_role("button", name="All time", exact=True).click()
                    expect(driver.locator('#earnings-content')).to_contain_text("Average net per trip")
                    driver.screenshot(path=str(artifacts / "earnings-report.png"), full_page=True)
                    rider.locator('[data-tab="history"]').click()
                    expect(rider.locator('#history-list')).to_contain_text("Gulshan")
                    expect(rider.locator('#history-next')).to_be_disabled()
                    rider.locator('#history-filters [name="q"]').fill("No matching destination")
                    rider.get_by_role("button", name="Apply filters").click()
                    expect(rider.locator('#history-list')).to_contain_text("No trips match these filters")
                    rider.locator('#history-filters [name="q"]').fill("Gulshan")
                    rider.get_by_role("button", name="Apply filters").click()
                    expect(rider.locator('#history-list [data-history]')).to_have_count(1)
                    with rider.expect_download() as download_info:
                        rider.get_by_role("button", name="Download CSV").click()
                    download = download_info.value
                    assert download.suggested_filename == "nori-trips.csv"
                    report = Path(download.path()).read_text(encoding="utf-8-sig")
                    assert "Gulshan" in report and "Final fare" in report
                    rider.get_by_role("button", name="Reset", exact=True).click()
                    expect(rider.locator('#history-filters [name="q"]')).to_have_value("")
                    rider.screenshot(path=str(artifacts / "trip-history.png"), full_page=True)
                    rider.locator('#history-list [data-history]').first.click()
                    expect(rider.locator('#booking-panel')).to_be_hidden()
                    rider.locator('#close-trip').click()
                    expect(rider.locator('#trip-panel')).to_be_hidden()
                    expect(rider.locator('#booking-panel')).to_be_visible()
                    expect(rider.locator('#ride-map .map-pin')).to_have_count(0)

                    # Rebooking prepares the route and requires a new quote and confirmation.
                    rider.locator('[data-tab="history"]').click()
                    rider.locator('[data-rebook]').click()
                    expect(rider.locator('#booking-panel')).to_be_visible()
                    expect(rider.locator('#pickup-label')).to_have_value("Banani")
                    expect(rider.locator('#destination-label')).to_have_value("Gulshan")
                    expect(rider.locator('#quote-result')).to_be_hidden()
                    trip_list = rider.request.get(base + "/api/rides/").json()
                    assert trip_list["count"] == 1, "Rebooking must not request a trip before confirmation"

                    # A historical trip must not hide access to a newly active trip.
                    plan_trip(rider)
                    rider.get_by_role("button", name="See my fare").click()
                    rider.get_by_role("button", name="Request a ride").click()
                    expect(rider.locator('#trip-content h2')).to_have_text("Finding your driver")
                    rider.locator('[data-tab="history"]').click()
                    rider.locator('#history-filter').select_option("COMPLETED")
                    expect(rider.locator('#history-list [data-history]')).to_have_count(1)
                    rider.locator('#history-list [data-history]').click()
                    expect(rider.locator('#trip-content h2')).to_have_text("You have arrived")
                    rider.locator('#ride-again').click()
                    expect(rider.locator('#toast')).to_contain_text("Finish or cancel your current trip")
                    expect(rider.locator('#trip-content h2')).to_have_text("You have arrived")
                    rider.locator('#return-active-trip').click()
                    expect(rider.locator('#trip-content h2')).to_have_text("Finding your driver")
                    rider.get_by_role("button", name="Cancel ride", exact=True).click()
                    expect(rider.locator('#trip-content h2')).to_have_text("Ride cancelled")
                    rider.locator('#close-trip').click()
                    # Reservations use local time, remain editable across polling,
                    # and produce a private calendar download without a new ride.
                    plan_trip(rider)
                    rider.locator('#booking-type-select').select_option('SCHEDULE')
                    expect(rider.locator('#schedule-timezone')).to_contain_text('Asia/Dhaka')
                    expected_pickup = rider.locator('#schedule-datetime').evaluate('(input) => new Date(input.value).toISOString()')
                    rider.get_by_role('button', name='See my fare').click()
                    expect(rider.locator('#promo-code-input')).to_be_hidden()
                    rider.locator('#request-ride').click()
                    expect(rider.locator('.scheduled-card')).to_have_count(1)
                    from django.utils.dateparse import parse_datetime
                    reservation = rider.request.get(base + '/api/rides/schedule/?pending=1').json()['results'][0]
                    assert parse_datetime(reservation['scheduled_for']) == parse_datetime(expected_pickup)
                    rider.get_by_role('button', name='Change pickup time').click()
                    edited_time = rider.evaluate('''() => {
                        const d = new Date(Date.now() + 2 * 3600000);
                        const pad = n => String(n).padStart(2, '0');
                        return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
                    }''')
                    rider.locator('.reschedule-form input').fill(edited_time)
                    with rider.expect_response('**/api/rides/current/'):
                        pass  # Let the regular poll run while the form has unsaved edits.
                    expect(rider.locator('.reschedule-form input')).to_have_value(edited_time)
                    expected_updated = rider.locator('.reschedule-form input').evaluate('(input) => new Date(input.value).toISOString()')
                    rider.get_by_role('button', name='Save pickup time').click()
                    expect(rider.locator('.reschedule-form')).to_be_hidden()
                    reservation = rider.request.get(base + '/api/rides/schedule/?pending=1').json()['results'][0]
                    assert parse_datetime(reservation['scheduled_for']) == parse_datetime(expected_updated)
                    with rider.expect_download() as calendar_download:
                        rider.get_by_role('button', name='Add to calendar').click()
                    calendar_path = artifacts / 'reservation.ics'
                    calendar_download.value.save_as(calendar_path)
                    assert b'BEGIN:VCALENDAR\r\n' in calendar_path.read_bytes()
                    assert b'TRIGGER:-PT15M\r\n' in calendar_path.read_bytes()
                    rider.screenshot(path=str(artifacts / 'reservations-desktop.png'), full_page=True)

                    mobile = new_page(mobile=True)
                    mobile.goto(base)
                    mobile.screenshot(path=str(artifacts / "home-mobile.png"), full_page=True)
                    mobile.goto(base + "/login/")
                    mobile.route("**/api/auth/me/", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail":"Temporary test outage"}'), times=1)
                    mobile.locator('[name="username"]').fill("browser_rider")
                    mobile.locator('[name="password"]').fill(password)
                    mobile.get_by_role("button", name="Log in", exact=True).click()
                    mobile.wait_for_url(base + "/app/")
                    expect(mobile.locator('#quote-form')).to_be_visible()
                    expect(mobile.locator('#account-identity')).to_contain_text("browser_rider", timeout=20000)
                    mobile.screenshot(path=str(artifacts / "dashboard-mobile.png"), full_page=True)
                    assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile page overflows horizontally"
                    mobile.locator('[data-tab="history"]').click()
                    expect(mobile.locator('#history-filters')).to_be_visible()
                    expect(mobile.locator('#history-list [data-history]')).to_have_count(2)
                    assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Mobile trip filters overflow horizontally"
                    mobile.screenshot(path=str(artifacts / "trip-history-mobile.png"), full_page=True)
                    mobile.get_by_role('button', name='Change pickup time').click()
                    expect(mobile.locator('.reschedule-form')).to_be_visible()
                    assert mobile.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Mobile reservation editor overflows'
                    mobile.screenshot(path=str(artifacts / 'reservations-mobile.png'), full_page=True)
                    # Cancellation must also refresh a card with its editor open.
                    mobile.get_by_role('button', name='Cancel reservation').click()
                    expect(mobile.locator('.scheduled-card')).to_have_count(0)
                    cancelled = mobile.request.get(base + '/api/rides/schedule/').json()['results'][0]
                    assert cancelled['id'] == reservation['id'] and cancelled['cancelled_at'] is not None
                    mobile.locator('[data-tab="account"]').click()
                    mobile.get_by_role("link", name="Change password", exact=True).click()
                    changed_password = secrets.token_urlsafe(24)
                    mobile.locator('[name="old_password"]').fill(password)
                    mobile.locator('[name="new_password1"]').fill(changed_password)
                    mobile.locator('[name="new_password2"]').fill(changed_password)
                    mobile.get_by_role("button", name="Update password").click()
                    expect(mobile.locator('h2')).to_have_text("Password updated")
                    mobile.get_by_role("link", name="Return to dashboard").click()
                    expect(mobile.locator('#account-identity')).to_contain_text("browser_rider")
                    mobile.get_by_role("button", name="Toggle navigation").click()
                    mobile.get_by_role("button", name="Log out", exact=True).click()
                    mobile.goto(base + "/login/")
                    mobile.get_by_role("link", name="Forgot your password?").click()
                    mobile.locator('[name="username"]').fill("browser_rider")
                    mobile.get_by_role("button", name="Find account").click()
                    expect(mobile.locator('h2')).to_have_text("Security verification")
                    mobile.locator('[name="security_answer"]').fill(security_answer)
                    reset_password = secrets.token_urlsafe(24)
                    mobile.locator('[name="new_password1"]').fill(reset_password)
                    mobile.locator('[name="new_password2"]').fill(reset_password)
                    mobile.screenshot(path=str(artifacts / "password-reset-mobile.png"), full_page=True)
                    assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Password form overflows horizontally"
                    mobile.get_by_role("button", name="Reset password", exact=True).click()
                    expect(mobile.locator('h2')).to_have_text("Password reset complete")
                    mobile.get_by_role("link", name="Back to login").click()
                    mobile.locator('[name="username"]').fill("browser_rider")
                    mobile.locator('[name="password"]').fill(reset_password)
                    mobile.get_by_role("button", name="Log in", exact=True).click()
                    mobile.wait_for_url(base + "/app/")
                    expect(mobile.locator('#account-identity')).to_contain_text("browser_rider")
                    mobile.locator('[data-tab="notifications"]').click()
                    expect(mobile.locator('#notifications-list a').first).to_be_visible()
                    mobile.locator('#notifications-list a').first.click()
                    expect(mobile.locator('#trip-content h2')).to_have_text("Ride cancelled")
                    for page in pages:
                        page.close()
                    browser.close()
                    assert not errors, f"JavaScript errors: {errors}"
                    print("Reservation workflow passed: local timezone, booking, editing across polling, calendar download, mobile editor and cancellation.")
                    print("Performance workflow passed: summary-only background activity, stable notification DOM and reuse of unchanged map routes.")
                    print("Browser workflow passed: registration, onboarding, admin approval, GPS controls, matching, trip lifecycle, cash, printable receipts, ratings, earnings, history, notification pagination and trip links, stale quote protection, current-trip navigation, startup recovery, mobile layout, password change and recovery, live WebSockets.")
                    print(f"Screenshots: {artifacts}")
            finally:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/PID", str(server.pid), "/T", "/F"], capture_output=True)
                else:
                    server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
                from django.db import connections
                connections.close_all()


if __name__ == "__main__":
    main()
