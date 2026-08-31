# Backend changes — action items for the frontend

All changes below are already pushed to `main` (Render auto-deploys on push).

## Pickup-aware order status lifecycle + Order Placed/Order Confirmed emails (2026-08-31)

### What you need to do

**Skip `out_for_delivery` for Kampala store pickup:**

1. A Kampala store pickup order (`fulfillment_method = "pickup"`, `location = "kampala"`, `delivery_fee = 0` — all three together, exactly your spec) now skips `out_for_delivery` entirely. `PATCH /orders/{id}/status`:
   - `packed` → `out_for_delivery` on a Kampala pickup order is rejected with `409` (`error_code: "INVALID_STATE_TRANSITION"`).
   - `packed` → `delivered` on a Kampala pickup order succeeds directly.
   - Every other transition, and every non-pickup order, is unchanged (delivery orders still require the full `pending → confirmed → packed → out_for_delivery → delivered` pipeline; `packed → delivered` directly is still rejected for them).
2. Outside-Kampala regional pickup is **unchanged for now** — it still goes through the full five-step pipeline, per your note that this wasn't confirmed otherwise yet.
3. `fulfillment_method` (`"pickup" | "delivery"`) and `location` (`"kampala" | "outside_kampala"`) are now present on `GET /orders/{id}` and every staff list response, exactly as you asked — derived server-side from the order's delivery-selection fields, not stored separately, so they can never disagree with them.
4. No new `OrderStatus` value was added — `delivered` is still the one terminal status for both pickup and delivery orders, as you specified. Use `fulfillment_method`/`location` (and which status-lifecycle email arrived — see below) to decide whether to render "Delivered" or "Collected" copy.
5. Customer edit/cancel eligibility needed no backend change — it already blocks at `out_for_delivery` and later, and a Kampala pickup order simply never reaches that status, so `packed` remains editable/cancellable for it exactly like every other pre-`out_for_delivery` status.

**New "Marked as Collected" email:**

6. When a Kampala store pickup order transitions into `delivered`, the customer now gets a distinct **"Your Order Has Been Collected"** email (pickup-worded — no mention of delivery/riders) instead of the door-to-door "Delivered" email. It still carries the same "Rate your experience" link as the Delivered email. A door-to-door delivery order's `delivered` transition still sends the original "Delivered" email, unchanged.

**Order Placed vs. Order Confirmed (bug fix):**

7. Checkout's own confirmation email no longer says "Order Confirmed" — it now says **"Order Placed"** (subject: "Your JN Electronics Order {order_number} Has Been Placed"), sent at the same point as before (right after checkout, before any staff review). This matches what actually happened: at that point the order is only `pending`.
8. A **new, separate** "Your Order Is Confirmed" email now fires specifically when a staff member advances the order from `pending` to `confirmed` — this is the email that should have existed all along for the word "confirmed" to mean anything. Same `guest_email`-gated behavior as every other order-status email (a phone-only guest checkout gets nothing).
9. No API shape changed for either of these — same `POST /orders` and `PATCH /orders/{id}/status` endpoints, same request/response bodies. This is purely which email gets sent and what it says.

### API contract (unchanged endpoints, no new routes)

| Method | Path | Change |
| --- | --- | --- |
| PATCH | `/orders/{id}/status` | Rejects `packed → out_for_delivery` with `409` for a Kampala store pickup order; allows `packed → delivered` directly for one |
| GET | `/orders/{id}` | Now includes `fulfillment_method`, `location` |
| GET | `/orders` (staff) | Same two fields on list items |

### Verification checklist

- [ ] Kampala store pickup order: `pending → confirmed → packed → delivered` succeeds
- [ ] Kampala store pickup order: `packed → out_for_delivery` returns `409`
- [ ] Kampala store pickup order's status history never contains `out_for_delivery`
- [ ] Kampala store pickup order's `delivered` transition sends the "Collected" email, not "Delivered"
- [ ] Delivery order (regression): `packed → out_for_delivery → delivered` still works; `packed → delivered` directly is still blocked
- [ ] `fulfillment_method`/`location` populated correctly on both order shapes
- [ ] Checkout email now reads "Order Placed", not "Order Confirmed"
- [ ] A separate "Order Confirmed" email arrives only once staff actually advance the order to `confirmed`

### Deploy status

Pushed to `main`. Render auto-deploys on push — not yet manually confirmed live on the deployed URL.
