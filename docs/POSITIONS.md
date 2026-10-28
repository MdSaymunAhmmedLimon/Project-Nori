# Positions, notifications, and cancellation

## Dashboards and powers

- Riders and drivers keep their separate booking and driving workspaces at `/app/`.
- Platform admins and approved users with operations-overview permission open `/operations/`.
- Approved moderators and custom team members open `/team/`, with only authorized queues.
- `/positions/` contains personal invitations, or the management console for administrators
  with `accounts.manage_positions`.
- The existing platform superuser bootstraps the first invitations. New assignments do
  not grant Django `is_staff` or `is_superuser`, and do not grant Django-admin access.

Seeded categories are Administrator, Moderator, and Driver. Administrators can create
custom categories and select powers for viewing trips, account access, driver review,
safety support, payouts, notifications, and position management. Write powers include
the corresponding read powers. The server checks permissions on every protected action.
Position management itself is an administrator power, and can appoint other administrators.

Existing staff retain their existing operations read access. Write actions, including
SOS resolution and payouts, require the corresponding explicit model permission.

## Required approval process

1. An administrator invites an active, registered account, selecting a category and note.
2. The recipient sees the invitation in Notifications and My invitations, reviews the
   exact offered powers, and accepts or declines.
3. Acceptance grants nothing. An administrator opens the accepted invitation, checks
   account details, offered powers and consent again, records a review note, confirms
   the review checkbox, and finally approves or rejects.

Invitations expire seven days after creation, including the final-review window. One
open invitation is allowed per account. Self-appointment and self-review are prohibited.
An unanswered invitation can be withdrawn; final approval/rejection requires acceptance.
Stale account, assignment or driver-review details require reloading the review page.

Offered permissions are snapshotted, so a later category change cannot silently add
powers. Each account has one assigned position; approving a replacement replaces the
previous position. A rejected offer leaves existing access intact. Revocation requires
a reason and takes effect on subsequent authorized requests, including existing sessions.
Invitation, acceptance, approval, rejection, withdrawal, replacement and revocation
events retain their actor, time and note.

Driver invitations require an existing driver account. Final approval also checks the
license, vehicle and submitted documents. Driver credentials can be reviewed separately,
but driving approval must use the accepted invitation. Revoking a driving position takes
the driver offline and returns credentials to pending; finish active trips first.
Existing approved drivers remain approved. Reapproval after credential changes uses a
new invitation. This is a consent and review workflow, not SMS or authenticator-based MFA.

## Notifications

The header bell and `/notifications/` provide a private inbox for every account type.
Messages include a category, title, timestamp, unread state and a local detail link.
Users can filter unread messages and mark one or all their own messages read. WebSocket
events update unread badges; periodic refresh provides recovery. The ride workspace
reuses its existing socket and polling. Open the inbox or Refresh updates for new items.

Invitations, responses, final decisions, revocations and ride cancellations produce
persisted notifications. Accepted invitations notify active position administrators.
Users with `accounts.send_notifications` can send an in-app message to a named account;
the sender is recorded. Email, SMS and operating-system push delivery are not configured.

## Cancellation

Riders and assigned drivers can review and confirm cancellation before a trip starts.
The dialog shows the server-calculated fee and demo-refund policy. If the fee changes
between review and confirmation, the server rejects the stale amount and requests a
fresh review. Existing configurable grace-period/arrival fees remain in force.

Authorized operators can cancel unstarted trips from **Rides**, with a required reason
and confirmation. These cancellations charge no fee, refund any demo prepayment once,
record the actor, notify both participants, and refresh outstanding driver offers.
Completed trips and trips already in progress cannot be cancelled through this flow.
Existing reservation cancellation continues to work in My trips.

## Database and verification

Apply migrations when deploying; local migrations were applied during implementation:

```powershell
python manage.py migrate --settings=ride_sharing_app.local_settings
python manage.py test --settings=ride_sharing_app.test_settings --noinput
.venv/Scripts/python.exe scripts/browser_roles_check.py
```

Browser checks use a disposable database and local routing fixture. PostgreSQL-specific
concurrency checks remain separate from the SQLite suite.
