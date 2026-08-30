# Backend changes — action items for the frontend

All changes below are already pushed to `main` (Render auto-deploys on push).

## Kampala delivery/pickup rework and storefront bundle endpoints (2026-08-30)

### What you need to do

**Delivery divisions, areas, and pickup stations (replaces `/delivery-zones` entirely):**

1. `GET /delivery-divisions` — public, active divisions only, sorted by `sort_order` then `name`. Fields: `id`, `name`, `is_active`, `sort_order`, `created_at`, `updated_at`.
2. `GET /delivery-areas?division_id={uuid}` — public, `division_id` is **required**. Returns active areas under that division only. Fields: `id`, `division_id`, `name`, `fee` (integer UGX), `is_active`, `sort_order`, `created_at`, `updated_at`.
3. `GET /regional-pickup-stations` — public, active stations only, sorted by `sort_order` then `major_town`. Fields: `id`, `major_town`, `address`, `fee` (integer UGX), `contact`, `is_active`, `sort_order`, `created_at`, `updated_at`.
4. Admin CRUD for all three lives under `/admin/delivery-divisions`, `/admin/delivery-areas` (admin list also returns `division_name` on each row), `/admin/regional-pickup-stations` — each supports `GET` (all, active + inactive), `POST` (create), `PATCH /{id}` (edit), `PATCH /{id}/status` (`{"is_active": boolean}`). **Gate all three to Owner and System Administrator only** — Sales Attendant gets `403`.
5. `POST /orders` now accepts exactly one of three combinations (matches your spec):
   - all three `null` → Kampala pickup from our shop, `delivery_fee` must be `0`
   - `delivery_division_id` + `delivery_area_id` together → Kampala door-to-door, `delivery_fee` must match the area's current fee
   - `regional_pickup_station_id` alone → outside-Kampala pickup, `delivery_fee` must match the station's current fee
   Any other combination (e.g. only one of division/area, or an area+station both set) is rejected with `422`. A stale `delivery_fee` is also rejected with `422` — refetch and retry.
6. **`district` and `delivery_address` are no longer overwritten server-side** — send whatever your checkout step already computes for each path; we no longer resolve/replace them from the selected division/area/station name.
7. `delivery_instructions` (string, optional, max 500 chars) is now accepted on `POST /orders` — send it whenever you wire up the "more information" field.
8. `GET /orders/{id}` (and everywhere else `OrderRead` appears) now includes: `delivery_division_id`, `delivery_area_id`, `regional_pickup_station_id`, `delivery_fee`, `district`, `delivery_address`, `delivery_instructions`, and the three name snapshots — `delivery_division_name`, `delivery_area_name` (Kampala delivery only), `pickup_town` (outside-Kampala pickup only, `= station.major_town`). All three snapshots are `null` for a Kampala pickup order. `total` is still `subtotal + delivery_fee`.
9. `delivery_zone_id` is gone. `GET /delivery-zones` and `/admin/delivery-zones` are gone (`404`) — update every reference.

**Storefront catalogue bundles (native replacement for your interim BFF):**

10. `GET /storefront/homepage-bundle` — public, returns `{categories, category_groups, store_settings, homepage_sections, catalogue_products, section_products}` in one response. `homepage_sections` is enabled-sections metadata only (no products nested); `section_products` is a separate list of `{section_id, products}` entries, **one per enabled section that has at least 4 matching products** — a section below that threshold is simply absent from `section_products` (it still appears in `homepage_sections`). `catalogue_products` is the first 100 active products, for your price-filter bounds.
11. `GET /storefront/catalogue-bundle` — public, returns `{categories, products}` (same `catalogue_products` shape, first 100 active products).
12. Both are cached (`Cache-Control: public, max-age=60, s-maxage=300`) and **not** wrapped in the usual `{success, message, data}` envelope — read the response body directly.
13. Once you switch over, `buildHomepageBundle()`/`buildCatalogueBundle()` and their fan-out calls can be retired.

### Deploy status

**Pushed to `main` (`a612b12`).** Render auto-deploys on push — not yet manually confirmed live on the deployed URL.
