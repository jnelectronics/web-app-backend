# Backend changes — action items for the frontend

All changes below are already pushed to `main` (Render auto-deploys on push).

## Products: collapsed create + price/sku/quantity fields

## What you need to do

1. **Product-creation form: send `sku`, `price`, and (optionally) `quantity_available` in the same `POST /products` call.** If your form currently does this in two steps (create the product, then a follow-up `POST /products/{id}/variants` call), drop the second step entirely — the one call now does both.
2. **Remove any "add a variant" option from the create flow.** Only show it on a product's *edit* page, for the genuine case of adding a second, separately-stocked color/size to a product that already exists.
3. **Product-edit form: stop sending `sku`, `price`, or `quantity_available` on `PUT /products/{id}`.** They're no longer part of that request schema. Sending them won't error, but they'll be silently ignored — if your edit form currently relies on this call to update price/stock, that update will quietly stop working. Editing a variant's price/SKU directly still goes through `PUT /variants/{variant_id}` (unchanged).
4. **Handle `409` on product creation.** If the `sku` you send is already taken by any variant in the catalog, creation now fails with `409` and creates nothing at all (no orphaned product).
5. **If your admin dashboard's product list/edit views were making a separate `GET /variants?product_id=<id>` call just to show price/sku/stock, drop it.** `GET /products` and `GET /products/{id}` now return those fields directly (see below) — this part is additive/backwards-compatible, nothing breaks if you leave the extra call in, it's just redundant now.
6. **Send the admin's bearer token on product list/detail requests where you want real stock numbers.** `quantity_available` on `ProductRead` only comes back as a real number for a staff-authenticated request — a public/logged-out request always gets `null` there, by design (stock counts are staff-only information).

## New request shape — `POST /products`

```json
POST /api/v1/products
{
  "category_id": "...",
  "name": "oraimo Candy 1m 2.1A Micro USB Cable",
  "description": "...",
  "sku": "JNE-OCDM22P-754",
  "price": 3477.6,
  "quantity_available": 10
}
```

`sku` and `price` are now **required**. `quantity_available` is optional, defaults to `0`.

## New request shape — `PUT /products/{id}`

```json
PUT /api/v1/products/{id}
{
  "category_id": "...",
  "name": "Renamed Product",
  "description": "...",
  "is_featured": false,
  "is_new_arrival": false,
  "is_on_sale": false
}
```

No `sku`/`price`/`quantity_available` fields exist on this schema at all.

## New response fields — `GET /products`, `GET /products/{id}`

```json
{
  "id": "...",
  "name": "oraimo Candy 1m 2.1A Micro USB Cable",
  "...": "... (unchanged fields omitted) ...",
  "sku": "JNE-OCDM22P-754",
  "price": 3477.6,
  "quantity_available": 10
}
```

- `sku` / `price` — public, visible on every request, same as they already are on `VariantRead`.
- `quantity_available` — `null` unless the caller sent a valid staff bearer token; a real integer otherwise (`0` if the product has never been stocked).

These three fields come from the product's **default variant** — the oldest active `ProductVariant` under it. Since every product created through the collapsed flow above has exactly one variant, this is normally the only variant there is. If a product later grows a **second** variant (a genuine separately-stocked color/size, via `POST /products/{id}/variants`), these fields still only reflect its *first* variant — the full set of variants is still only available via `GET /variants?product_id=<id>`, unchanged. All three are `null` only in the rare edge case of a product whose one variant has since been soft-deleted with nothing replacing it.

## Why this changed

Entering the real starting product catalog surfaced that the old two-call creation flow (create product, then create its variant) had been silently producing duplicate-looking products — 45 distinct names ended up as 2-3 separate `Product` rows each, because a fresh product got created every time instead of reusing one. Collapsing creation into one call removes the chance to do that by construction. Adding price/sku/stock to the read responses directly followed from the admin dashboard's own design, which expected them there rather than requiring a second call per product.

## Verified

Full automated suite passing (90/90, `pytest tests/ -v`), including dedicated coverage for: the collapsed create (variant + stock record + movement log entry all created together), the duplicate-SKU rollback (rejects with 409, leaves nothing behind), product edit succeeding with no sku/price/quantity fields present, and the public-vs-staff gating on the new `quantity_available` field for both the detail and list endpoints.

## Deploy status

**Pushed to `main` (`c0613dd`, `7e77cd3`).** Render auto-deploys on push and should already be live.

---

## Promotion discounts now actually reduce cart/order/payment totals (2026-08-22)

Fixes the bug you reported: `discounted_price` on a cart item was correct, but `line_total`, cart `subtotal`, order `subtotal`/`total`, order item `unit_price`/`line_total`, and the amount charged via PesaPal were all still computed from the undiscounted list price. All of that now uses the discounted price whenever an active promotion applies, exactly as your bug report's acceptance criteria described.

### What you need to do

1. **Nothing required for the discount fix itself** — no request/response shapes changed for cart or checkout. `GET /cart`, `POST /cart/items`, `PATCH /cart/items/{id}`, `POST /orders`, and `POST /orders/{id}/payments` all just return correct numbers now.
2. **`amount` is no longer read from `POST /orders/{order_id}/payments`.** The endpoint now always charges `order.total` server-side, regardless of what (if anything) is sent as `amount` in the request body. You can safely stop sending it whenever convenient — it's harmless to leave in for now (unknown fields are ignored, not rejected), but it does nothing. If your own computed `amount` and the real order total were ever to disagree for any reason, the order total silently wins; nothing you send in that field is authoritative anymore.

### Why this changed

Root cause was that `discounted_price` was originally built as **display-only** — a comment in the code said so explicitly. That assumption stopped being correct once sale prices needed to actually be charged, not just shown. While fixing it, we also found the payment endpoint was trusting a client-supplied `amount` with no server-side check against the order at all — a separate, more serious gap (not just this bug) — so that's now closed too: the backend decides the charge amount, never the caller.

### Verified

Full automated suite passing (91/91), including a new end-to-end test that drives the real API (add to cart → checkout → initiate payment) for a USh 1,000 item with an active 50%-off discount, and asserts `500` at every stage: cart line total, cart subtotal, order subtotal/total, order item unit price/line total, and payment amount.

### Deploy status

**Pushed to `main` (`c710ee1`).** Render auto-deploys on push and should already be live.
