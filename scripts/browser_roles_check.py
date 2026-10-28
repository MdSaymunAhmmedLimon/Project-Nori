"""Verify invitations, scoped dashboards, notification delivery and cancellation in Edge.

Uses the existing disposable database and local routing fixture; no production data.
Run: .venv/Scripts/python.exe scripts/browser_roles_check.py
"""
from pathlib import Path


def run_scenarios(browser, base, sessions, candidate, driver, administrator, rider, ride):
    from playwright.sync_api import expect
    from accounts.models import PositionAssignment, PositionInvitation
    from drivers.models import DriverProfile
    from notifications.models import Notification
    from rides.models import Ride

    artifacts = Path(__file__).resolve().parents[1] / "artifacts" / "roles"
    artifacts.mkdir(parents=True, exist_ok=True)
    errors = []

    def database(callback):
        # Playwright's sync runner owns an event loop. Keep ORM reads off it.
        from concurrent.futures import ThreadPoolExecutor
        from django.db import connections
        def run():
            try:
                return callback()
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(run).result()

    def page_for(user):
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies([{"name": "sessionid", "value": sessions[user.pk], "url": base}])
        context.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base) else route.abort())
        page = context.new_page()
        page.set_default_timeout(15000)
        page.on("pageerror", lambda error: errors.append(str(error)))
        return page

    def screenshot(page, name):
        for width in (1440, 390, 320):
            page.set_viewport_size({"width": width, "height": 900})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), (name, width)
            page.screenshot(path=str(artifacts / f"{name}-{width}.png"), full_page=True)
        page.set_viewport_size({"width": 1440, "height": 1000})

    admin = page_for(administrator)
    user = page_for(candidate)
    driver_page = page_for(driver)
    rider_page = page_for(rider)

    admin.goto(base + "/app/")
    expect(admin).to_have_url(base + "/operations/")
    admin.goto(base + "/positions/categories/new/")
    admin.locator('[name="name"]').fill("Trip observer")
    admin.locator('textarea[name="description"]').fill("View ride activity without changing trips or accessing financial information.")
    admin.locator('[name="dashboard"]').select_option("TEAM")
    admin.locator('[name="permissions"][value="rides.view_ride"]').check()
    admin.get_by_role("button", name="Create category").click()
    expect(admin.locator('.ops-cards').last).to_contain_text("Trip observer")

    def send_invite(target, category):
        admin.goto(base + "/positions/invite/")
        admin.locator('[name="username"]').fill(target.username)
        admin.locator('[name="category"]').select_option(label=category)
        admin.locator('[name="note"]').fill("Please review this invitation and its responsibilities.")
        admin.get_by_role("button", name="Send invitation").click()
        expect(admin.locator('.page-heading h1')).to_have_text(category)
        return admin.url

    def accept(page, url):
        page.goto(url)
        page.locator('[name="note"]').fill("I accept the listed responsibilities.")
        page.get_by_role("button", name="Accept invitation", exact=True).click()
        expect(page.locator('.page-heading .pill')).to_have_text("Awaiting final review")

    def approve(url):
        admin.goto(url)
        admin.locator('[name="note"]').fill("Account, consent and offered powers have been checked again.")
        admin.locator('[name="confirmed"]').check()
        admin.get_by_role("button", name="Final approve", exact=True).click()
        expect(admin.locator('.page-heading .pill')).to_have_text("Approved")

    user.goto(base + "/notifications/")
    url = send_invite(candidate, "Moderator")
    expect(user.locator('[data-unread-badge]')).to_be_visible()
    user.reload()
    expect(user.locator('.notification-feed')).to_contain_text("Position invitation")
    screenshot(user, "notification-invitation")
    accept(user, url)
    assert not database(lambda: candidate.has_perm("rides.change_sosalert"))
    admin.goto(url)
    screenshot(admin, "final-review")
    approve(url)
    assert database(lambda: candidate.has_perm("rides.change_sosalert"))
    user.goto(base + "/app/")
    expect(user).to_have_url(base + "/team/")
    expect(user.locator('.page-heading')).to_contain_text("MODERATOR WORKSPACE")
    screenshot(user, "moderator-dashboard")
    assert user.request.get(base + "/operations/users/").status == 403
    assert user.request.get(base + "/positions/categories/new/").status == 403

    # Cancelling is a deliberate second step; dismissing leaves the ride intact.
    rider_page.goto(base + "/app/")
    expect(rider_page.locator('#trip-panel')).to_be_visible()
    rider_page.locator('#cancel-form [type="submit"]').click()
    expect(rider_page.locator('#cancellation-dialog')).to_be_visible()
    screenshot(rider_page, "cancellation-review")
    rider_page.locator('#keep-ride').click()
    assert database(lambda: Ride.objects.get(pk=ride.pk).status) == "ACCEPTED"
    rider_page.locator('#cancel-form [type="submit"]').click()
    rider_page.locator('#cancellation-reason').fill("My travel plans changed.")
    rider_page.get_by_role("button", name="Confirm cancellation", exact=True).click()
    expect(rider_page.locator('#trip-content')).to_contain_text("My travel plans changed.")
    assert database(lambda: Ride.objects.get(pk=ride.pk).status) == "CANCELLED"
    assert database(lambda: Notification.objects.filter(user=driver, message__contains="My travel plans changed").exists())

    # Driver positions have the same consent gate plus final credential checks.
    database(lambda: DriverProfile.objects.filter(user=driver).update(verification_status="PENDING", is_online=False))
    driver_url = send_invite(driver, "Driver")
    accept(driver_page, driver_url)
    approve(driver_url)
    assert database(lambda: DriverProfile.objects.get(user=driver).verification_status) == "APPROVED"
    driver_page.goto(base + "/app/")
    expect(driver_page.locator('#driver-review')).to_have_text("Approved")
    screenshot(driver_page, "driver-dashboard")

    # Revocation removes permissions on the next request, even in an existing session.
    admin.goto(base + "/positions/")
    form = admin.locator(f'form[action="/positions/users/{candidate.pk}/revoke/"]')
    form.locator('..').locator('summary').click()
    form.locator('[name="reason"]').fill("Assignment completed.")
    form.get_by_role("button", name="Revoke access").click()
    assert database(lambda: PositionAssignment.objects.get(user=candidate).revoked_at) is not None
    assert user.request.get(base + "/operations/support/").status == 403
    user.goto(base + "/notifications/")
    expect(user.locator('.notification-feed')).to_contain_text("Position access revoked")
    user.get_by_role("button", name="Mark all as read").click()
    expect(user.locator('[data-unread-badge]')).to_be_hidden()

    # An accepted custom position may still be rejected by the administrator.
    rejected_url = send_invite(candidate, "Trip observer")
    accept(user, rejected_url)
    admin.goto(rejected_url)
    admin.locator('[name="note"]').fill("The account does not meet this position's requirements.")
    admin.locator('[name="confirmed"]').check()
    admin.get_by_role("button", name="Reject", exact=True).click()
    expect(admin.locator('.page-heading .pill')).to_have_text("Rejected")
    assert not database(lambda: candidate.has_perm("rides.view_ride"))
    assert database(lambda: PositionInvitation.objects.filter(user=candidate, status="REJECTED").exists())
    admin.goto(base + "/positions/")
    screenshot(admin, "positions-dashboard")
    assert not errors, errors
    for page in (admin, user, driver_page, rider_page):
        page.context.close()
    print("PASS: custom category, invitation, live inbox, acceptance without powers, final approval, moderator isolation, driver consent/approval, cancellation confirmation, notifications, revocation and rejection; 320px/mobile/desktop layouts; no JavaScript errors.")


if __name__ == "__main__":
    from browser_experience_check import main
    main(scenario="positions")
