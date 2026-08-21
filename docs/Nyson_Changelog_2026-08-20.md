# Backend changes — 2026-08-20 — action items for the frontend

Two things changed on `POST /products` and `PUT /products/{id}`. Both need a frontend form change to actually work correctly against the new API — not pushed yet, so nothing is live against you until you're ready.

## What you need to do

1. **Product-creation form: send `sku`, `price`, and (optionally) `quantity_available` in the same `POST /products` call.** If your form currently does this in two steps (create the product, then a follow-up `POST /products/{id}/variants` call), drop the second step entirely — the one call now does both.
2. **Remove any "add a variant" option from the create flow.** Only show it on a product's *edit* page, for the genuine case of adding a second, separately-stocked color/size to a product that already exists.
3. **Product-edit form: stop sending `sku`, `price`, or `quantity_available` on `PUT /products/{id}`.** They're no longer part of that request schema. Sending them won't error, but they'll be silently ignored — if your edit form currently relies on this call to update price/stock, that update will quietly stop working. Editing a variant's price/SKU directly still goes through `PUT /variants/{variant_id}` (unchanged).
4. **Handle `409` on product creation.** If the `sku` you send is already taken by any variant in the catalog, creation now fails with `409` and creates nothing at all (no orphaned product). Same conflict handling you likely already have for the old `POST /products/{id}/variants` SKU check — just needs to also cover this call now.

Nothing else needs to change — `ProductRead`'s shape is the same as before (see the note at the bottom on price/stock display, which is unrelated to today's change).

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

No `sku`/`price`/`quantity_available` fields exist on this schema at all anymore.

## Why this changed

Entering the real starting product catalog surfaced that the old two-call flow (create product, then create its variant) was producing duplicate-looking products — 45 distinct names ended up as 2-3 separate `Product` rows each, because each data-entry pass created a fresh product instead of reusing one. Collapsing creation into one call removes the chance to do that by construction.

## Update (2026-08-21): price/sku/stock now ARE on `GET /products`

The note below was accurate as of this doc's original writing but is now superseded — see `Nyson_Changelog_2026-08-21.md` for the follow-up that adds `sku`/`price`/`quantity_available` directly to `ProductRead`, specifically so the admin dashboard doesn't need a second `GET /variants` call per row.

~~`GET /products`/`GET /products/{id}` still don't return price/SKU/stock (price lives on the variant, not the product, since a product can have several variants at different prices). If your product list/detail views need price shown, that's still a separate `GET /variants?product_id=<id>` call per product, same as before today's change. Flag if you want this looked at as its own follow-up (e.g. embedding a default-variant summary directly on `ProductRead` when a product has exactly one variant) — didn't touch it here since it's a separate decision.~~

## Verified

**89/89 automated tests passing** (`pytest tests/ -v`) — 86 pre-existing plus 3 new tests covering the collapsed create (variant + stock record + movement log entry all created together), the duplicate-SKU rollback (rejects with 409, leaves nothing behind), and product edit succeeding with no sku/price/quantity fields present.

## Deploy status

**Pushed to `main` (`c0613dd`), 2026-08-21.** Render is auto-deploying now. This is a real breaking change to both endpoints — update your product-creation/edit forms per "What you need to do" above before your admin dashboard's product forms hit either endpoint again.
