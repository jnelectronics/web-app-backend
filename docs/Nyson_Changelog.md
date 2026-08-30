# Backend changes — action items for the frontend

All changes below are already pushed to `main` (Render auto-deploys on push).

## Delivery zones, post-delivery rating, and product lifecycle (2026-08-30)

### What you need to do

**Delivery zones:**

1. `GET /delivery-zones` — public, active zones only, sorted by `sort_order` then `name`. Fields: `id`, `name`, `fee` (integer UGX), `is_active`, `sort_order`, `created_at`, `updated_at`.
2. Admin CRUD lives at `GET/POST /admin/delivery-zones`, `PATCH /admin/delivery-zones/{id}`, `PATCH /admin/delivery-zones/{id}/status` — **gate these to Owner and System Administrator only**, Sales Attendant gets `403`.
3. `POST /orders` now accepts `delivery_zone_id` (uuid, omit for a pickup order) and `delivery_fee` (integer). We re-validate the fee server-side and reject with `422` if it doesn't match the zone's current fee — send it, but don't treat your own copy as authoritative. `district` comes back overwritten with the zone's own name whenever a zone is selected.
4. `GET /orders/{id}` (and everywhere else `OrderRead` appears) now includes `delivery_zone_id` and `delivery_fee`. `total` is `subtotal + delivery_fee`.
5. Duplicate zone names are rejected case-insensitively (`409`).

**Post-delivery experience rating:**

6. `GET /public/order-ratings/{token}` — prefill, always `200`. `rating_status` is `eligible` / `already_rated` / `expired` / `invalid` — render your error states off this field, not an HTTP status code. Also returns `order_number`, `delivered_at`, `item_count`, and (only when `already_rated`) `score`/`comment`/`submitted_at`.
7. `POST /public/order-ratings/{token}` — submit, body `{"score": 1-5, "comment": "optional, max 500 chars"}`. `201` on success, `404` if the token doesn't resolve to a delivered order, `409` if already rated, `410` if expired.
8. The Order Delivered email now sends this link automatically — nothing to trigger from your side.

**Product lifecycle:**

9. `ProductRead` now includes `deactivated_at` (nullable datetime) — matches your `PRODUCT_PERMANENT_DELETE_AFTER_DAYS = 30` constant.
10. `PATCH /products/{id}/reactivate` now also clears `deactivated_at` back to `null`.
11. `DELETE /products/{id}/permanent` is new: `204` on success, `409` if the product was never deactivated, `409` if it's not yet 30 days past `deactivated_at`, `409` if it still has real order history. **Gate this button to Owner only** — Sales Attendant gets `403`.

**RBAC narrowing (client UAT request — affects your admin nav/guards):**

12. Sales Attendant now gets `403` on the whole Dashboard module (`/admin/dashboard/*`), `PATCH /payments/{id}/mark-paid`, and `PATCH /customers/{id}/status` (deactivate). Reading the customer directory is unaffected. Update your client-side role guards for these three spots to match.

### Deploy status

**Pushed to `main` (`4820f62`).** Render auto-deployed and it's confirmed live.
