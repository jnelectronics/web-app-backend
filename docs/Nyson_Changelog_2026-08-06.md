# Backend changes — 2026-08-06

Response to your 28-item integration report — all 28 addressed, tested (79/79 passing), and deployed to Render on `main`. This doc is a changelog of what actually changed; `Frontend_Integration_Contract.md` is the up-to-date reference for how things behave now, and `JN_API_Specification.md` has been corrected everywhere it drifted from deployed behavior.

## ⚠️ Breaking change — do this one first

**Login now uses `identifier`, not `email`.**

```diff
- POST /auth/login  { "email": "...", "password": "..." }
+ POST /auth/login  { "identifier": "...", "password": "..." }
```

`identifier` accepts either an email **or** a phone number — customers can now log in with either. Your existing login call will 422 until you rename the field.

## Every collection endpoint changed shape

Every list/collection response used to return a bare array. They now all return:

```json
{ "items": [...], "pagination": { "page": 1, "page_size": 20, "total_records": 137, "total_pages": 7 } }
```

Query params are `skip`/`limit` (not `page`/`page_size`). This affects `GET /products`, `/categories`, `/variants`, `/staff`, `/customers`, `/inventory`, `/inventory/movements`, `/audit-logs`, `/orders` (`list_my_orders`), and the new `/orders/staff`. Any place you were doing `response.data.map(...)` directly on one of these now needs `response.data.items.map(...)`.

## Products — the big one for product pages

`ProductRead` now includes what you flagged as missing:

- `description`, `is_featured`, `slug`
- `is_discounted` — computed from whether the product has a currently-active discount window, not stored
- `images: [...]` — embedded directly, no separate call needed to render a product card/page

`GET /products` gained real filtering/search/sort, all as query params: `category`, `search`, `featured`, `discounted`, `sort`, plus the `skip`/`limit` pagination above.

New: `GET /products/{id}/discounts` — public, lists all discounts for a product (past/current/future), if you need more than just the "is it discounted right now" boolean.

`ProductVariant` reads now include `in_stock: bool`, computed live against inventory.

## Cart — items now carry display data

`CartItemRead` used to only have `product_id`/`variant_id`/`quantity`. It now also carries `product_name`, `variant_label`, `sku`, `image_url` — resolved fresh on every read, so your cart page can render without a second round-trip per line item.

**Also fixed a real bug you may have hit without realizing it was a bug**: a guest cart's `X-Guest-Token` used to become permanently unusable the moment that cart converted (checkout, or now guest-merge — see below). Any add-to-cart after that would fail. Fixed at the DB level; if you were working around this by minting a new guest token after every checkout, you no longer need to.

## Guests can now actually pay

Previously a guest could place an order (`POST /orders`, no account) but had no way to pay for it — both payment endpoints required a Bearer token. Now:

- `POST /orders/{id}/payments` and `GET /payments/{id}` accept the same `X-Guest-Token` header cart already uses, checked against the token that created the order.
- `PaymentInitiate.provider` is now a real validated enum: `mobile_money`, `card`, `cash_on_delivery` (was previously any free-form string).
- New: **cash on delivery**. Picking `provider: "cash_on_delivery"` skips PesaPal entirely and creates a `pending` payment. Staff confirm collection via the new `PATCH /payments/{id}/mark-paid`.
- Checkout's `409` (insufficient stock) now includes `short_items` in the response body, listing exactly which line items are short and by how much — no more generic "insufficient inventory" with nothing actionable.

## Guest → account cart merge

Logging in, registering, or signing in with Google while holding an active guest cart (send `X-Guest-Token` on any of those three calls) now merges that guest cart into the customer's account cart automatically. No separate "claim my cart" step needed — the guest's items just show up after auth.

## Staff-facing additions

- `GET /orders/staff` — a real, filterable (status/search/date) order list for staff. Separate from the admin dashboard's capped recent-orders widget.
- `GET /staff/me`, `PATCH /staff/me` — any logged-in staff member can now see/update their own profile (previously `GET /staff` 403'd for non-managers, so there was no way for a staff member to see their own info).
- `GET /staff/{id}` — single staff record (manage-roles only).
- `POST /staff/{id}/reset-password` — admin-generated temporary password for a locked-out staff account.
- `GET /inventory/movements` — branch-wide, filterable (branch/variant/movement_type/date range), paginated. Previously only readable one inventory record at a time.
- Audit log entries now embed `staff_full_name`/`staff_email` (nullable, in case that staff account is later removed) instead of a bare ID — and are filterable by `action`.

## "Toggle" endpoints now "set" endpoints

Status endpoints for staff, customers, banners, and product discounts used to blindly flip active↔inactive — not safe to retry, since a double-click or retried request would silently undo itself. They now take a body specifying the target state:

```diff
- PATCH /staff/{id}/status        (no body, just flips it)
+ PATCH /staff/{id}/status  { "is_active": false }
```

Same shape change for `/customers/{id}/status`, `/banners/{id}/status`, `/product-discounts/{id}/status`.

New: `GET /banners?include_inactive=true` (staff-only) if you need to show inactive banners in an admin view.

## PATCH /orders/{id} widened

Now also accepts `guest_full_name`, `guest_phone_number`, `guest_email` alongside the existing `delivery_address` — lets a guest correct contact details after placing an order, not just the address.

## Three frontend pages still needed (all currently 404)

Backend links to these three URLs in emails/redirects — all currently point at real domain paths that don't exist yet as pages:

1. **`https://www.jnelectronics.ug/reset-password`** — password reset. Read `token` from the query string, POST it to `POST /auth/password/reset` with the new password. See `Frontend_Integration_Contract.md`'s "Password reset" section.
2. **`https://www.jnelectronics.ug/order-confirmation`** — where PesaPal redirects the customer's browser after paying. See `Frontend_Integration_Contract.md`'s "Payments" section for what this page needs to do (poll `GET /payments/{payment_id}`).
3. **`https://www.jnelectronics.ug/staff/login`** — new as of today's changes, see the dedicated section below for details.

## New required field — `district` at checkout

**⚠️ Breaking change: `POST /orders` now requires a `district` field.** Same treatment as the `identifier` rename above — your existing checkout call will `422` until you add it.

```diff
  POST /orders
  {
    "guest_full_name": "...",
    "guest_phone_number": "...",
    "guest_email": "...",
    "delivery_address": "...",
+   "district": "Kampala"
  }
```

- **Type:** required string, e.g. `"Kampala"`, `"Wakiso"` — no fixed enum server-side, so validate/restrict the list on your end if you want one.
- **Why it exists:** separate from `delivery_address` (which stays a free-text full address) — `district` is the coarser field staff actually need at a glance, and now shows up directly in the "new order placed" staff email instead of a branch name.
- **Where it comes back:** every `OrderRead` response now includes `district` too — `POST /orders`, `GET /orders/{id}`, `GET /orders` (customer's own list), and `GET /orders/staff` all return it. Safe to read on any order page.
- Existing orders placed before this change have `district` backfilled to `"Unknown"` — expect to see that on old test/demo orders in the shared DB.

## URL still needed — staff dashboard login

`https://www.jnelectronics.ug/staff/login` doesn't exist yet (currently 404) — added today because the new "new order placed" and "payment received" staff emails now link there so staff can click straight through to log in. Needed from you, one of:

1. **A real URL**, if a staff login page already exists at a different path — share it and we'll point `STAFF_DASHBOARD_URL` at it (env var only, no code change).
2. **Confirmation you'll build it** at `/staff/login` — then no further action needed on either side once it's live.

Until either happens, that link in staff emails goes nowhere real — low urgency (internal-only, staff already know how to reach the dashboard), but worth closing out along with the other two pending pages (`/reset-password`, `/order-confirmation`) below.

## Spec doc corrections (no behavior change, just fixing wrong docs)

`JN_API_Specification.md` had drifted from what's actually deployed in 12 places — all fixed to match reality: several endpoints documented as `PATCH` are actually `PUT`/`DELETE`, `PATCH .../status` sketches replaced with the real soft-delete `DELETE`s, `page`/`page_size` corrected to `skip`/`limit`, the real FastAPI validation-error shape documented, `customer_type`/`is_active` field names corrected, PesaPal's webhook documented as the real `GET` (not `POST`) it always was. If anything in the spec still looks off against what the API actually returns, trust the deployed API and flag it — that's the standing rule in `Frontend_Integration_Contract.md` §1.3.
