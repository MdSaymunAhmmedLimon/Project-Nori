"""Exercise the dashboard in Edge/Chromium with an isolated DB and routing fixture.

Run with .venv/Scripts/python.exe scripts/browser_experience_check.py.
The fixture tests integration; it does not validate the routing provider's roads.
"""
import asyncio
from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, urlsplit
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


class Router(BaseHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        points = [list(map(float, point.split(','))) for point in url.path.rsplit('/', 1)[1].split(';')]
        if '/table/' in url.path:
            payload = {'code': 'Ok', 'distances': [[1200] for _ in parse_qs(url.query)['sources'][0].split(';')]}
        else:
            payload = {'code': 'Ok', 'routes': [{'distance': 5700, 'duration': 840,
                       'geometry': {'type': 'LineString', 'coordinates': points}}]}
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(payload).encode())

    def log_message(self, *args):
        pass


def main(scenario="experience"):
    from playwright.sync_api import sync_playwright, expect

    artifacts = ROOT / 'artifacts' / 'experience'
    artifacts.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nori-experience-', dir=ROOT / '.venv', ignore_cleanup_errors=True) as temporary:
        database = str(Path(temporary) / 'test.sqlite3')
        router = ThreadingHTTPServer(('127.0.0.1', 0), Router)
        Thread(target=router.serve_forever, daemon=True).start()
        os.environ.update(DJANGO_SETTINGS_MODULE='ride_sharing_app.browser_test_settings',
                          BROWSER_TEST_DB=database, NORI_LOCAL_DATABASE=database,
                          DJANGO_SECRET_KEY=secrets.token_urlsafe(64),
                          ROUTING_BASE_URL=f'http://127.0.0.1:{router.server_address[1]}')
        import django
        django.setup()
        from django.core.management import call_command
        from django.contrib.auth import get_user_model
        from django.test import Client
        from django.utils import timezone
        from datetime import timedelta
        from rides.models import Ride, RideQuote
        from rides.tests.test_workflow import make_driver
        from payments.models import Payment
        import uuid

        call_command('migrate', verbosity=0)
        rider = get_user_model().objects.create_user(username='Ayesha', email='ayesha@example.com')
        driver = make_driver('Karim', is_online=False)
        admin = get_user_model().objects.create_superuser(username='operations', email='ops@example.com', password=secrets.token_urlsafe(24))
        live_rider = get_user_model().objects.create_user(username='Live', email='live@example.com')

        def create_ride(user, status):
            quote = RideQuote.objects.create(rider=user, pickup_label='Banani', destination_label='Gulshan 2 Circle',
                pickup_latitude='23.810300', pickup_longitude='90.412500', destination_latitude='23.792500',
                destination_longitude='90.416700', distance_km='5.70', estimated_minutes=14,
                route_geometry={'type': 'LineString', 'coordinates': [[90.4125, 23.8103], [90.414, 23.80], [90.4167, 23.7925]]},
                fare=200, platform_fee=30, currency='BDT', expires_at=timezone.now() + timedelta(minutes=10))
            return Ride.objects.create(rider=user, driver=driver, quote=quote, status=status,
                completed_at=timezone.now() if status == 'COMPLETED' else None, request_id=uuid.uuid4())

        completed = create_ride(rider, 'COMPLETED')
        live = create_ride(live_rider, 'ACCEPTED')
        Payment.objects.create(ride=completed, amount=200, platform_fee=30, currency='BDT', method='CASH', status='PAID')
        sessions = {}
        for user in (rider, driver, admin, live_rider):
            client = Client()
            client.force_login(user)
            sessions[user.pk] = client.cookies['sessionid'].value
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        base = f'http://127.0.0.1:{port}'
        with (artifacts / 'server.log').open('w', encoding='utf-8') as log:
            server = subprocess.Popen([sys.executable, 'manage.py', 'runserver', f'127.0.0.1:{port}', '--noreload', '--settings=ride_sharing_app.browser_test_settings'],
                cwd=ROOT, env=os.environ.copy(), stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            try:
                for _ in range(100):
                    try:
                        with urlopen(base, timeout=1):
                            break
                    except OSError:
                        time.sleep(.1)
                if os.name == 'nt':
                    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
                with sync_playwright() as playwright:
                    edge = Path(r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe')
                    browser = playwright.chromium.launch(headless=True, executable_path=os.environ.get('BROWSER_EXECUTABLE') or (str(edge) if edge.exists() else None))
                    if scenario == "positions":
                        from browser_roles_check import run_scenarios
                        run_scenarios(browser, base, sessions, rider, driver, admin, live_rider, live)
                        browser.close()
                        return
                    errors = []

                    def new_page(user):
                        context = browser.new_context(viewport={'width': 1440, 'height': 1050})
                        context.add_cookies([{'name': 'sessionid', 'value': sessions[user.pk], 'url': base}])
                        # Keep third-party tile availability out of workflow assertions.
                        context.route('**/tile.openstreetmap.org/**', lambda route: route.abort())
                        context.route('**/*.tile.openstreetmap.org/**', lambda route: route.abort())
                        page = context.new_page()
                        page.on('pageerror', lambda error: errors.append(str(error)))
                        page.set_default_timeout(15000)
                        page.goto(base + '/app/')
                        if not user.is_staff:
                            expect(page.locator('#connection-status')).to_have_text('Live updates connected')
                        return page

                    page = new_page(rider)
                    expect(page.locator('#recent-trips')).to_contain_text('Gulshan 2 Circle')
                    page.locator('[data-recent-ride]').click()
                    expect(page.locator('#pickup-label')).to_have_value('Banani')
                    for query in ('Dhanmondi', 'Uttara'):
                        page.locator('#add-booking-stop').click()
                        page.locator('#stop-search').fill(query)
                        expect(page.locator('#stop-search-results .command-option').first).to_be_visible()
                        page.locator('#stop-search-results .command-option').first.click()
                    labels = page.locator('#booking-stops-list li strong').all_text_contents()
                    page.locator('[data-stop-up="1"]').click()
                    assert page.locator('#booking-stops-list li strong').all_text_contents() == list(reversed(labels))
                    page.reload()
                    expect(page.locator('#booking-stops-list li')).to_have_count(2)
                    with page.expect_response(lambda response: '/api/rides/quotes/' in response.url and response.request.method == 'POST') as quote_response:
                        page.locator('#quote-form [type=submit]').click()
                    quote = quote_response.value.json()
                    assert quote_response.value.status == 201, quote
                    assert len(quote['stops']) == 2 and len(quote['route_geometry']['coordinates']) == 4
                    assert quote['stops'][0]['label'] == labels[1]
                    expect(page.locator('#fare-validity')).to_contain_text('Fare held for')
                    page.evaluate('window.scrollTo(0, 0)')
                    page.wait_for_timeout(300)
                    page.screenshot(path=str(artifacts / 'rider-desktop.png'), full_page=True)
                    # Server quote expiry stays authoritative; client prevents stale confirmation too.
                    page.evaluate('window.realDateNow = Date.now; Date.now = () => window.realDateNow() + 11 * 60 * 1000')
                    expect(page.locator('#request-ride')).to_be_disabled()
                    expect(page.locator('#fare-validity')).to_contain_text('expired')
                    page.evaluate('Date.now = window.realDateNow')
                    page.reload()
                    page.locator('#open-quick-actions').click()
                    page.locator('#quick-actions-search').fill('stats')
                    page.locator('#quick-actions-search').press('ArrowDown')
                    page.keyboard.press('Enter')
                    expect(page.locator('#stats-content')).to_contain_text('Completed journeys')
                    expect(page.locator('#stats-content')).to_contain_text('Banani')
                    page.keyboard.press('Control+k')
                    expect(page.locator('#quick-actions-dialog')).to_be_visible()
                    page.keyboard.press('Escape')
                    page.locator('[data-tab=ride]').click()
                    with page.expect_response(lambda response: '/api/rides/quotes/' in response.url and response.request.method == 'POST'):
                        page.locator('#quote-form [type=submit]').click()
                    page.locator('#request-ride').click()
                    expect(page.locator('#trip-panel')).to_be_visible()
                    expect(page.locator('#trip-content')).to_contain_text('STOP 2')
                    with closing(sqlite3.connect(database)) as db, db:
                        booked = db.execute("SELECT quote_id FROM rides_ride WHERE rider_id=? AND status='SEARCHING'", (rider.pk,)).fetchone()
                        assert booked
                        assert db.execute('SELECT COUNT(*) FROM rides_ridestop WHERE quote_id=?', booked).fetchone()[0] == 2
                    page.locator('#cancel-form [type=submit]').click()
                    page.locator('#confirm-cancellation-form [type=submit]').click()
                    expect(page.locator('#close-trip')).to_be_visible()
                    page.locator('#close-trip').click()
                    page.locator('[data-stop-remove="0"]').click()
                    expect(page.locator('#booking-stops-list li')).to_have_count(1)
                    page.locator('#clear-booking-stops').click()
                    expect(page.locator('#booking-stops')).to_be_hidden()
                    page.set_viewport_size({'width': 390, 'height': 844})
                    page.evaluate('window.scrollTo(0, 0)')
                    page.wait_for_timeout(300)
                    page.screenshot(path=str(artifacts / 'rider-mobile.png'), full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
                    live_page = new_page(live_rider)
                    expect(live_page.locator('#journey-estimate')).to_contain_text('to pickup')
                    expect(live_page.locator('[aria-current=step]')).to_contain_text('Pickup')
                    live_page.screenshot(path=str(artifacts / 'live-journey.png'), full_page=True)
                    driver_page = new_page(driver)
                    expect(driver_page.locator('#readiness-count')).to_contain_text('4 / 4')
                    driver_page.locator('[data-tab=stats]').click()
                    expect(driver_page.locator('#stats-content')).to_contain_text('Driving performance')
                    driver_page.locator('[data-tab=payouts]').click()
                    expect(driver_page.locator('.payout-balance')).to_contain_text('170.00')
                    driver_page.locator('#payout-request-form [name=amount]').fill('50')
                    driver_page.locator('#payout-request-form [name=account_details]').fill('Test account 12345')
                    driver_page.locator('#payout-request-form [type=submit]').click()
                    expect(driver_page.locator('.payout-record')).to_contain_text('pending')
                    expect(driver_page.locator('.payout-balance')).to_contain_text('120.00')
                    with closing(sqlite3.connect(database)) as db, db:
                        assert db.execute('SELECT amount, status FROM drivers_payoutrequest').fetchone() == (50, 'PENDING')
                        db.execute("UPDATE rides_ride SET status='CANCELLED' WHERE id=?", (live.pk.hex,))
                    driver_page.locator('[data-tab=account]').click()
                    expect(driver_page.locator('#driver-documents-list')).to_contain_text('No verification documents')
                    driver_page.locator('#driver-document-form [name=file]').set_input_files({'name': 'licence.pdf', 'mimeType': 'application/pdf', 'buffer': b'%PDF-1.4\nTest fixture'})
                    driver_page.locator('#driver-document-form [type=submit]').click()
                    expect(driver_page.locator('#driver-documents-list')).to_contain_text('pending')
                    driver_page.locator('[data-tab=ride]').click()
                    expect(driver_page.locator('#driver-review')).to_have_text('Awaiting review')
                    driver_page.screenshot(path=str(artifacts / 'driver-desktop.png'), full_page=True)
                    ops = new_page(admin)
                    ops.goto(base + '/operations/')
                    ops.screenshot(path=str(artifacts / 'operations-desktop.png'), full_page=True)
                    assert not errors, errors
                    browser.close()
                    print('PASS: multi-stop quote/order/persistence, recent trips, expiry, keyboard actions, stats, live ETA, readiness, payout submission, document upload, mobile overflow, admin rendering; no browser errors.')
            finally:
                try:
                    server.terminate()
                    try:
                        server.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        server.kill()
                        server.wait(timeout=5)
                finally:
                    router.shutdown()
                    router.server_close()
                    # Windows cannot remove temporary databases with open handles.
                    from django.db import connections
                    connections.close_all()


if __name__ == '__main__':
    main()
