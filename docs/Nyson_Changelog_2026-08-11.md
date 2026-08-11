# Backend changes — 2026-08-11

Response to your "Backend needs (frontend handoff)" doc for homepage sections. Short version: **everything you asked for already existed** — it shipped in the same commit as category groups/promotions/store settings, before your doc arrived. But reading your doc against the actual code (then proving it live, not just by reading) turned up 4 real gaps between what's built and what you specified. All 4 are now fixed and pushed to `main`. Details below, plus one thing on your side worth double-checking.

## Endpoints — nothing missing, nothing unused

All 6 endpoints exist at exactly the paths you listed:

```
GET    /api/v1/homepage-sections
GET    /api/v1/admin/homepage-sections
POST   /api/v1/admin/homepage-sections
PUT    /api/v1/admin/homepage-sections/{id}
DELETE /api/v1/admin/homepage-sections/{id}
PATCH  /api/v1/admin/homepage-sections/reorder
```

`HomepageSectionRead` matches your field table exactly, plus one extra field you didn't ask for: `view_all_href` (string, nullable) — an optional override for a section's "view all" link. You said the frontend derives this from `section_type` for now, which is fine; the field's there if you ever want the backend to be able to override it per-section.

**One optimization you listed as "not blocking, maybe later" already exists today**: `GET /homepage-sections?include_products=true` (default `true` on the public endpoint) embeds each section's resolved products directly — you don't need to build the N+1 workaround.

## 4 real gaps, found and fixed

I read the code against your doc, then actually ran the requests to confirm — reading alone would've missed these, since they're all "accepts something it shouldn't" bugs, not missing features.

**1. Title/description over the DB length limit crashed with a 500.**
Same bug class as the product-description issue from two days ago. `title` (150 chars) and `description` (500 chars) had no validation matching the database column limits — an admin typing a slightly-too-long title in the new Settings UI would hit a plain-text `Internal Server Error`, not a clean error. Fixed: both now return a normal `422` over the limit.

**2. `by_category` accepted an inactive category.**
Your doc says "any *active* category can be a section." The code only checked the category existed, not that it was active — I created a category, deactivated it, then successfully pointed a section at it (`200 OK`). Fixed: now returns `404` for an inactive category, same as a nonexistent one.

**3. Reorder silently ignored bad input instead of rejecting it.**
Your error table wants unknown/missing IDs in a reorder to `422`. The old code just skipped IDs it didn't recognize and left any *omitted* section's rank untouched — I proved this could produce two sections both at `display_order = 0` simultaneously by sending a reorder that only included some sections. Fixed: `PATCH /admin/homepage-sections/reorder` now requires the **complete, exact set** of every existing section's ID, no duplicates, no unknowns — anything else is a `422`. This also guarantees the "contiguous ranks" behavior you asked for, since a partial list can no longer produce gaps or collisions.

⚠️ **This means your reorder call must always send every section's ID**, not just the ones that moved. If your UI already does a full drag-and-drop reorder (sending the whole list back), you're covered — just flagging it in case there's a "move one item" code path that only sends a partial diff.

**4. Seeded default sections didn't match your spec.**
The DB had 3 seeded sections from before your doc existed: order was *Featured → On Sale → New Arrivals* with `max_products: null` (a backend guess that the frontend default was 8). Your doc wants *On Sale → Featured → New Arrivals* with `max_products: 12`. Fixed via a data migration — the 3 existing rows (same IDs, so nothing breaks if you've already cached them) now read:

```json
[
  { "title": "On Sale",           "display_order": 0, "max_products": 12 },
  { "title": "Featured Products", "display_order": 1, "max_products": 12 },
  { "title": "New Arrivals",      "display_order": 2, "max_products": 12 }
]
```

## Something to double-check on your side (not a backend bug)

Your doc's product-filter examples use `page_size`:
```
GET /api/v1/products?featured=true&page_size=12
```
The real param is **`limit`**, not `page_size` (`page`/`page_size` was replaced by `skip`/`limit` in the Aug 6 changelog). `page_size` isn't a recognized query param, so it's silently ignored rather than erroring — you won't see a failure, you'll just quietly get the default of **10** products back instead of 12. Worth grepping your homepage-sections/product-filter code for `page_size` before this goes live, since it's the kind of mismatch that's easy to miss in testing and only shows up as "why is this row short one product."

## Open question for you, not something I changed

`GET/POST/PUT/DELETE /admin/homepage-sections` and the reorder endpoint all require the **Inventory Manager** staff role (System Administrator also passes, as a superset — see the standing role rule in our other docs). Your doc just says "Staff" for all of these. If your admin Settings → Homepage sections page is meant to be reachable by every staff role (e.g. Sales Attendant), they'll get a `403` today. Let us know if that's intended to be broader — this is a scope call, not something I want to guess at and change unilaterally.

## Deploy status

- **Data migration (seed order/max_products fix) is already applied to the shared DB** — takes effect immediately, no deploy needed for that part.
- **Code changes (the 3 validation fixes) are pushed to `main`**, not yet confirmed live on Render. Will auto-deploy same as always; confirm with us once it's live before you point your integration tests at it.
- Full test suite run clean (82/82, once a transient DB connection drop during the long run was ruled out by re-running those 4 in isolation).
