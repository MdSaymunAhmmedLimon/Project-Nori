# Nori Wallet and demo prepayments

Nori now includes a local SSLCOMMERZ-style checkout simulation. It does not
contact SSLCOMMERZ, its sandbox, bKash, Nagad, or a bank. No merchant credentials,
card details, PINs, OTPs, payment SDK, or external account are needed. The UI and
receipts explicitly label these transactions as demo transactions.

The success/failure/cancellation flow was informed by the
[official SSLCOMMERZ documentation](https://developer.sslcommerz.com/doc/v4/).
This local simulation is distinct from the provider's hosted sandbox.

## Try it

1. Log in as a rider and open **Nori Wallet**.
2. Choose a top-up amount (10–10,000 demo credits). Try bKash, Nagad, card, or
   internet banking in the demo dialog. Simulate a successful payment to add
   credits, or test failure/cancellation to leave the wallet unchanged.
3. Book a route and get a fare. Choose **Nori Wallet** to deduct credits before
   booking, **SSLCOMMERZ** to simulate direct prepayment, or cash to pay later.
   The existing real card option still depends on the existing Stripe settings.
4. Use **Pre-book a ride** to reserve more than 15 minutes and up to seven days
   ahead. Both demo prepayment methods also work for reservations.
5. Cancel a demo booking to see its full refund. Wallet credits return to the
   wallet. Direct SSLCOMMERZ demo payments are marked refunded to their simulated
   source and do not credit the wallet.
6. After completing a trip, select 1–5 stars, optional feedback tags, and a comment.
   Ratings persist and can only be submitted once by each trip participant.

## Accounting and lifecycle

- Wallet credits have no cash value and cannot be withdrawn or transferred.
- New wallets start at zero. Balance and paginated activity persist in the
  database; refreshing or logging out does not reset them.
- Fare amounts and currencies come from the authenticated rider's server quote.
  The client cannot supply or override a booking charge amount.
- Debit, prepayment, booking, and ledger writes share one database transaction.
  Insufficient credits, expired quotes, failed payments, and invalid bookings
  roll back without charging. The wallet balance cannot be negative.
- Top-ups use a rider-scoped request ID. Ride requests retain the existing
  idempotency key, and reservations reuse the same quote. Retrying a successful
  request cannot charge twice.
- Reservations retain their prepayment at dispatch even if the original quote
  expired. Dispatch does not debit the wallet again.
- Cancellation, driver-search timeout, and a missed reservation window refund
  demo payments exactly once. Demo cancellations have no cancellation fee.
- Demo payments and their tips are excluded from real earnings, payout balances,
  rider spend totals, and operations revenue. Demo trips do not trigger referral
  payment rewards. Demo receipts are marked clearly.
- Demo prepaid bookings do not support promo codes. Ordinary payment behavior
  is preserved.
- Scheduled dispatch still requires the existing polling/dispatcher mechanism.
  Neither payment nor advance booking guarantees driver availability.

## API additions

All endpoints require authentication; wallets are rider-only and scoped to the
current account. Session requests use the existing CSRF protection.

| Endpoint | Behavior |
| --- | --- |
| `GET /api/payments/wallet/` | Balance, currency, and `is_demo: true` |
| `GET /api/payments/wallet/transactions/?page=1` | 20 ledger entries per page |
| `POST /api/payments/wallet/top-up/` | `amount`, UUID `request_id`, `demo_channel`, `demo_outcome` |
| `POST /api/rides/` | Existing request with `payment_method: WALLET` or `SSL_DEMO` |
| `POST /api/rides/schedule/` | Existing reservation with either demo payment method |

SSLCOMMERZ simulation requires a `demo_channel` of `BKASH`, `NAGAD`, `CARD`, or
`BANK`, and a `demo_outcome` of `SUCCESS`, `FAILED`, or `CANCELLED`. Only success
creates a transaction. A wallet ride needs no gateway fields.

The ride payment response includes `is_demo`. Payment status can now also be
`REFUNDED`. Staff can inspect wallets, ledger entries, and demo prepayments in
the read-only Django admin.

## Setup and verification

The new migrations are `rides.0006` and `payments.0002`. They have been applied to
the project's local SQLite database. Other environments must run the normal
Django migrations before serving the new code. No new dependencies are needed.

Run the Django suite and `scripts/browser_smoke.py` using the README commands.
The automated checks cover ownership, validation, duplicate requests, exact
charges, insufficient balances, cancellation/timeout refunds, delayed dispatch,
demo receipts, earnings isolation, star ratings, and mobile checkout. Browser
checks use disposable accounts and databases; no real gateway is involved.
