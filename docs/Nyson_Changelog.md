# Backend changes — action items for the frontend

All changes below are already pushed to `main` (Render auto-deploys on push).

## Must be paid before it's placed — every order now requires a confirmed PesaPal payment (2026-09-16)

Norman (the client) ran real UAT testing and found a genuine bug: starting checkout, proceeding to PesaPal's page, and abandoning it without entering any payment details still left a placed order behind, and the customer still got an "Order Placed" email. That's fixed by changing WHEN an order actually counts as placed - this is the biggest contract change in this batch, please read carefully.

### What you need to do

**1. `POST /orders` (checkout) no longer returns a placed order - it returns an unpaid one, waiting for payment.**

- The response's `status` field is now `"awaiting_payment"`, not `"pending"`. Nothing has been decremented, emailed, or shown to staff yet - this order does not really exist yet from the business's point of view.
- Your checkout flow must immediately follow this call with `POST /orders/{id}/payments` (unchanged endpoint/shape) and send the customer to the `redirect_url` PesaPal hands back, exactly like today - the only difference is there is no "come back later and pay" option anymore. **There is no flow where a customer can leave an order unpaid and finish paying some other way** - cash-on-delivery/pay-in-store is gone entirely (see below).
- If the customer abandons/fails the PesaPal page, the order simply stays `awaiting_payment` forever - no email was sent, nothing was decremented, staff never saw it. If your UI shows an "order confirmation" screen right after checkout, it should now wait for payment to actually resolve (poll `GET /payments/{id}` the same way you already do) before treating the order as real - don't treat the `POST /orders` response alone as "done."

**2. The order only becomes real - `status: "pending"` - once PesaPal confirms payment.**

- This is now the actual moment: `GET /payments/{id}` (your existing polling endpoint) resolves to `status: "paid"`, and in that same moment the order flips from `awaiting_payment` to `pending`. Fetch the order again (`GET /orders/{id}`) after a payment resolves to `paid` if you need its up-to-date status.
- Everything downstream is unchanged from here: staff still separately confirm `pending -> confirmed`, then `packed -> ... -> delivered`, exactly as before.

**3. Cash-on-delivery / pay-in-store is removed entirely.**

- `POST /orders/{id}/payments`'s `provider` field no longer accepts `"cash_on_delivery"` at all - only `"mobile_money"` and `"card"` are valid now. Sending `cash_on_delivery` gets a `422`.
- Remove any "pay in store" / "pay on delivery" option from checkout, for every fulfillment method (delivery, Kampala pickup, or outside-Kampala regional pickup) - Norman's own words: "All orders being paid for via MM or Bank in Pesapal regardless of picking from the store, within or outside kampala."
- `PATCH /payments/{payment_id}/mark-paid` (the staff cash-collection endpoint) no longer exists - `404` if called.

**4. Staff never see an unpaid order, and can't act on one even if they somehow got its id.**

- `GET /orders/staff` (the default, unfiltered view) no longer includes `awaiting_payment` orders - only real, placed orders show up. (Still reachable with an explicit `?order_status=awaiting_payment` filter, if you ever need a "stuck checkouts" support view.)
- `PATCH /orders/{id}/status` has no valid transition out of `awaiting_payment` at all - attempting one gets a `409`, same error shape as any other invalid transition.
- The Admin Dashboard's order counts/recent-orders/sales-summary all exclude `awaiting_payment` orders too.

**5. The "New Order" admin email now shows the same items/qty/subtotal/total breakdown the customer email already had** (Norman's second ask) - purely a content change, not a contract change, nothing to build for this.

**6. The email logo changed** (Norman's third, lowest-priority ask) - purely visual, nothing for the frontend to do.

### API contract (what actually changed)

| Method | Path | Change |
| --- | --- | --- |
| POST | `/orders` | Now returns the order with `status: "awaiting_payment"`, not `"pending"`. No email sent yet, no stock reserved. |
| POST | `/orders/{id}/payments` | `provider` no longer accepts `"cash_on_delivery"` (`422` if sent) |
| PATCH | `/payments/{payment_id}/mark-paid` | **Removed** - `404` |
| GET | `/orders/staff` | Excludes `awaiting_payment` orders by default (opt in via `?order_status=awaiting_payment`) |
| PATCH | `/orders/{id}/status` | `409` on any attempted transition out of `awaiting_payment` |

### Verification checklist

- [ ] Checkout (`POST /orders`) returns `status: "awaiting_payment"`
- [ ] Abandoning/failing PesaPal's page leaves the order `awaiting_payment` forever - no email sent, nothing shown to staff
- [ ] A confirmed PesaPal payment flips the order to `status: "pending"` and NOW sends the "Order Placed" email + notifies staff
- [ ] `provider: "cash_on_delivery"` on `POST /orders/{id}/payments` returns `422`
- [ ] `PATCH /payments/{id}/mark-paid` returns `404`
- [ ] Staff's default order list never shows an unpaid/abandoned checkout
- [ ] The admin "New Order" email shows items/qty/subtotal/total, same as the customer email

### Deploy status

Pushed to `main`. Render auto-deploys on push - not yet manually confirmed live on the deployed URL.
