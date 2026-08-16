# Backend changes — 2026-08-16

Response to your two 2026-08-13 docs — the payment-failure diagnosis and the homepage-sections handoff. Both are done. Details below.

## 1. Payment blocker — fixed and verified live

Your diagnosis was right: PesaPal's `SubmitOrderRequest` was rejecting the deployed API's IPN ID. Root cause turned out to be a stale/mismatched `PESAPAL_IPN_ID` on Render, not a code bug — confirmed by calling PesaPal's own `GetIpnList` directly with the live credentials, which showed the correct ID for `https://web-app-backend-2vca.onrender.com/api/v1/payments/webhook` was `3e674a89-8efd-44fd-90ff-da130c6fc097`, not what Render had configured. Swapped it in, and separately corrected `PESAPAL_CALLBACK_URL` (was pointing at `/order-confirmation`, should be `/confirm-payment`).

**Verified live against the deployed API** (not just locally): `mobile_money` and `card` both now return `201` with a real `redirect_url` pointing at PesaPal's hosted checkout (`https://pay.pesapal.com/iframe/...`), `cash_on_delivery` still returns `201`, and `GET /payments/{id}` resolves for a guest. The one item from your checklist we can't do from here is actually clicking through a real payment in a browser and confirming the `/confirm-payment` redirect — that needs you.

**New: the callback URL is now per-request, not fixed.** Your doc flagged three different needed callback URLs (prod/QA/local), but this backend serves `www.jnelectronics.ug`, `test.jnelectronics.ug`, and the Vercel preview from one deployment — a single `PESAPAL_CALLBACK_URL` env var literally can't be correct for all three. `POST /orders/{id}/payments` now reads the request's `Origin` header, checks it against the same `CORS_ALLOWED_ORIGINS` allowlist already governing CORS, and sends PesaPal `{that origin}/confirm-payment` when it's a known origin — falling back to the configured env var otherwise (e.g. a direct server-to-server call with no Origin header). Nothing changes on your end; this is transparent as long as the frontend's own `fetch`/`XHR` call naturally carries an `Origin` header, which it will.

**New error code**: PesaPal gateway failures (SubmitOrderRequest rejected, PesaPal unreachable, etc.) now return `error_code: "PAYMENT_GATEWAY_ERROR"` instead of the generic `INTERNAL_ERROR`, still at `502`. Same message format as before (`"Payment gateway error: ..."`), just a code you can branch on for more precise copy.

## 2. Homepage sections — curated collections built

Everything in your handoff doc that wasn't already live (category rows + legacy types shipped back on 2026-08-11) is now built:

- `curated` added to `section_type`
- New `slug` field on `HomepageSection` — required + unique when `section_type=curated`, `null` for every other type. Missing slug or a duplicate both return a clean `422`.
- New `product_homepage_sections` many-to-many join table
- `ProductRead` now includes `homepage_section_ids: string[]` — empty array when the product belongs to none
- **Membership sync endpoint — one path difference from your doc, worth knowing:** you specified `PUT /api/v1/admin/products/{product_id}/homepage-sections`, but this API has no `/admin/products` namespace anywhere (staff-only product routes are gated by role check, not a URL prefix — same as `POST/PUT/DELETE /products/{id}` today). The real path is:

  ```
  PUT /api/v1/products/{product_id}/homepage-sections
  ```

  Same request/response shape you specified (`{"homepage_section_ids": [...]}`, full replace semantics, returns the updated product). Rejects unknown section ids (`404`) and links to a non-curated or disabled section (`422`), matching your error table.
- `GET /api/v1/products?homepage_section={uuid}` filter, existing `featured`/`on_sale`/`new_arrival`/`category` filters untouched. `pagination.total_records` included, for your ≥4-products visibility rule.
- Deleting a curated section cascades the join rows first (this project doesn't use DB-level `ON DELETE CASCADE` or ORM relationships anywhere, so it's done explicitly in the same transaction) — confirmed no orphaned membership rows survive a delete.

**Verified with a live smoke test against the real API**: created a curated section, confirmed missing/duplicate slug both `422`, synced a product's membership, confirmed `homepage_section_ids` shows up on both the admin and public product read, confirmed the `?homepage_section=` filter returns it, confirmed linking a product to a non-curated section is rejected, deleted the section and confirmed the join row is gone. All rows cleaned up afterward — nothing left in the shared DB from this test.

Not touched (matches your doc — optional, not blocking): `product_count` on section read, bulk membership endpoint. `include_products=true` was already live before your doc even asked for it (see the 2026-08-11 changelog).

## Deploy status

All of the above is applied to the shared dev/prod database already (the migration is additive-only — new enum value, new nullable column, new table — nothing destructive). Code changes are local, not yet pushed/deployed to Render — will confirm once pushed and auto-deployed.
