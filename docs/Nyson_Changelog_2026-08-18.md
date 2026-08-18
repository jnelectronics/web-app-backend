# Backend changes — 2026-08-18

Response to your three 2026-08-16/17 docs (customer account manager, staff profile settings, staff role model + first-login reset), plus two items flagged separately (dashboard revenue, repeated login). All done, tested (85/85 automated tests passing), and pushed — Render is auto-deploying now.

## 1. Saved addresses (Address Book) — built

Full CRUD under `/customers/me/addresses`, matching your doc's shape exactly.

| Method | Path | Notes |
|---|---|---|
| GET | `/api/v1/customers/me/addresses` | Returns an array (empty if none), default address first, then oldest-first |
| POST | `/api/v1/customers/me/addresses` | `{label, recipient_name, phone_number, address_line, is_default?}` — `is_default` defaults to `false` |
| PATCH | `/api/v1/customers/me/addresses/{address_id}` | All fields optional (partial update) |
| DELETE | `/api/v1/customers/me/addresses/{address_id}` | Real delete (`204`), not soft — matches "Remove a saved address" |

Business rules, all enforced server-side:
- Registered customers only (a guest has no JWT to call these with at all).
- **Max 3 addresses per customer** — a 4th `POST` returns `409`.
- **At most one `is_default: true`** — setting a new one automatically un-defaults every other address for that customer, on both create and update.
- Every endpoint is ownership-scoped — trying to `PATCH`/`DELETE` another customer's address (or one that doesn't exist) returns **`404`**, not `403`, matching how the rest of this API hides existence of things you don't own.

Response shape is exactly `CustomerAddressRead` as you specified it (`id, label, recipient_name, phone_number, address_line, is_default, created_at, updated_at`).

## 2. Self-service account closure — built

Went with your **Option A**.

```
PATCH /api/v1/customers/me/status
{ "status": "inactive" }
```

- Only ever accepts `"inactive"` — sending `"active"` (or anything else) is rejected with `403`. Reactivation stays staff-only, via the existing `PATCH /customers/{id}/status`.
- Closing an already-inactive account returns `409`, not a silent no-op.
- On success: `200` with the updated `CustomerRead`, and **every active refresh token for that customer is revoked** (same "log out everywhere" logic `/auth/logout` uses). One nuance worth knowing: the *access token* used to make this call is a stateless JWT and stays cryptographically valid until its own ~60-minute expiry — revocation only kills refresh tokens. Please still clear stored tokens client-side and redirect immediately after a successful close, rather than relying on the token to stop working on its own.

## 3. Staff role model — built (breaking change for the frontend, please read)

`StaffRole` is now `owner` / `system_administrator` / `sales_attendant` — `inventory_manager` no longer exists as a value. This was a **rename**, not a delete-and-recreate: every existing staff account that had `role: "inventory_manager"` now has `role: "owner"`, same account, same id, same login credentials. Nothing needs to be recreated, but **any frontend code checking `role === "inventory_manager"` needs to check `"owner"` instead**, or it'll silently stop matching.

Alongside the rename, **Sales Attendant gained access to every admin section except Staff management and Audit Logs** (your requested matrix). Concretely, Sales Attendant can now do everything Owner can on: categories, category groups, products, variants, branches, homepage sections, store settings, promotions (banners + product discounts), inventory (create + adjust, not just view), the customer directory, and the dashboard's low-inventory/sales-summary widgets. `POST/GET/PATCH /staff/*` and `GET /audit-logs` remain Owner + System Administrator only — Sales Attendant still gets `403` there.

One thing that did **not** change: `total_revenue` on `GET /admin/dashboard/summary` is still hidden from Sales Attendant specifically (`null` in the response) — a separate, narrower rule (FR-ADMIN-003) that's independent of the RBAC widening above.

## 4. Mandatory first-login password reset — built

`StaffRead` (from `GET /staff/me`, `PATCH /staff/me`, and everywhere else a staff record is returned) now includes:

```json
{ "must_change_password": true }
```

- `true` on `POST /staff` (new account) and `POST /staff/{staff_id}/reset-password` (admin-issued temporary password).
- Cleared to `false` by `PATCH /staff/me/password` — only on a successful password change (a wrong `current_password` doesn't touch it).
- `POST /staff` now also sends the welcome email you asked for — full name, login email, the temporary password, role, and a login link — asynchronously, so it doesn't slow down the create request. If Resend isn't configured in an environment, it logs what it would have sent instead of failing the request.

## 5. Self-service staff profile update — built

```
PATCH /api/v1/staff/me
{ "full_name": "Jane Okello", "phone_number": "+256700000000" }
```

- Any staff role, including Sales Attendant.
- Both fields optional (partial update).
- **`role`, `email`, `is_active` are hard-rejected with `422`** if present in the body at all — not silently dropped — matching your "reject (403 or 422)" requirement precisely. (This was actually a gap in my first pass — caught it while writing this doc up and fixed it before pushing; see the second commit below.)
- `phone_number` can be explicitly cleared: **omit** the field to leave it untouched, send it as **`null`** to clear it. `full_name` can't be cleared this way (it's a required field on the account).
- Returns the updated `StaffRead` under `data`, same envelope as `GET /staff/me`.

## 6. Dashboard revenue — bug fixed

`total_revenue` (on both `GET /admin/dashboard/summary` and `GET /admin/dashboard/sales-summary`) was counting every non-cancelled order regardless of payment status — an order sitting unpaid in `pending` was still counted as revenue. It now only counts orders that have at least one `PAID` payment. If your dashboard was showing a higher number than expected, it'll be lower now, correctly.

## 7. Repeated login — not a backend bug

Checked the token setup on this end: access tokens last 60 minutes, refresh tokens last 30 days, and `POST /auth/refresh` correctly issues a fresh access token from a still-valid refresh token — that part is working as designed. If users are being asked to log in more often than that, it's almost certainly one of:

- the frontend isn't calling `POST /auth/refresh` when a request comes back `401` / the access token expires, or
- the refresh token isn't being persisted somewhere that survives a reload or new tab (e.g. it's only being kept in memory/component state instead of `localStorage` or a persisted cookie).

Happy to dig further from this side once you confirm which one it looks like — nothing to fix here in the API itself.

## Verified

- **85/85 automated tests passing** (`pytest tests/ -v`), including new coverage added for every endpoint above (address CRUD + isolation between customers, account closure + refresh-token revocation, `must_change_password` lifecycle end-to-end including a real login with the temporary password, the `PATCH /staff/me` validation rejection).
- Role-rename migration (`ALTER TYPE staff_role RENAME VALUE 'inventory_manager' TO 'owner'`) applied directly to the shared dev/prod DB — existing accounts flipped in place, nothing lost or duplicated.

## Deploy status

Pushed to `main` in two commits — `85d457f` (the full batch above) and a follow-up `0d990d0` (the `PATCH /staff/me` validation fix in §5). Render auto-deploys on push; will confirm once live.
