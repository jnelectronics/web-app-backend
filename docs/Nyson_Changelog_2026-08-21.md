# Backend changes — 2026-08-21 — action items for the frontend

One additive change: `GET /products` and `GET /products/{id}` now return `sku`, `price`, and `quantity_available` directly. Nothing existing was removed or renamed — this is purely new fields on a response you already consume, so nothing breaks if you don't touch anything. Not pushed yet.

## What you need to do

1. **Nothing is required** — this is backwards-compatible. Existing frontend code that ignores unknown response fields keeps working exactly as before.
2. **If your admin dashboard's product list/edit views were built expecting price/sku/stock straight from `GET /products`** (per your original design), you can now read them directly off `ProductRead` instead of making a separate `GET /variants?product_id=<id>` call per row. Drop that extra call wherever it exists purely to show price/sku/stock in a list or detail view.
3. **`quantity_available` only comes back as a real number when the request is staff-authenticated** (same rule `GET /variants` already follows) — send the admin's bearer token on these calls the way you already do elsewhere in the admin dashboard. A public/logged-out request gets `quantity_available: null`, always — that's expected, not a bug.

## New fields — `ProductRead` (`GET /products`, `GET /products/{id}`)

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
- `quantity_available` — `null` unless the caller sent a valid staff bearer token; a real integer otherwise (`0` if the product has never been stocked at all).

## Where these values come from

Every product created since the 2026-08-20 collapsed-create change (see `Nyson_Changelog_2026-08-20.md`) has exactly one variant, so these three fields just mirror that variant's own `sku`/`price`/stock. If a product ever grows a **second** variant (a genuine separately-stocked color/size, via `POST /products/{id}/variants`), these fields keep showing its **first** variant only — they're a convenience for the common single-variant case, not a replacement for `GET /variants?product_id=<id>`, which is still the only way to see every variant a product has.

`sku`/`price`/`quantity_available` are `null` only in the rare edge case of a product whose one-and-only variant has since been soft-deleted (`DELETE /variants/{id}`) with nothing replacing it.

## Verified

New test (`tests/test_products.py::test_product_read_exposes_default_variant_price_sku_and_gates_quantity`) covering: a public GET sees `sku`/`price` but `quantity_available: null`; a staff GET on the same product/list sees the real quantity; both `GET /products/{id}` and `GET /products` (list) behave the same way. Full existing suite still green.

## Deploy status

**Not pushed yet.** Since this is additive and shouldn't require any frontend change to avoid breaking anything, happy to push as soon as you've confirmed — let me know.
