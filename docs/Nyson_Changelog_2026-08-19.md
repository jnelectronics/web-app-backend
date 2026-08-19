# Backend changes — 2026-08-19

Response to your frontend audit notes (sent 8:01 PM, 8/18) — the "Noted Issues (Backend or Cross-Team)" list. Went through every row. Most needed no backend change (already correct, already a deliberate tradeoff, or not something this API is involved in at all). Three real, small gaps got fixed and tested (91/91 automated tests passing, including new coverage for all three). **Not pushed yet** — changes are local, will push once you've had a look, since one of them changes a status code your client already has a workaround for.

## Authentication and authorization

**401 vs 403 — real bug, fixed.** You were right, and I was wrong on my first pass at triaging this. `security.py`'s `HTTPBearer` was using its library default (`auto_error=True`), which silently returns **403 "Not authenticated"** the instant a request has *no* Authorization header at all — before any of our own code runs. But a request with a *present-but-invalid/expired* token was already going through our own check, which returns **401**. Two different codes for what your client should treat as the same case ("not logged in"), which is exactly why `src/api/client.ts` has that 403-treated-like-401 workaround.

Fixed: both cases now return **401**, consistently, on every protected endpoint (customer, staff, and the customer-or-admin routes). 403 is now reserved purely for "you're authenticated, but not allowed to do this" (wrong role, deactivated account, etc.) — you should be able to remove the workaround once this ships.

**JWT in localStorage → httpOnly cookies.** Real tradeoff (XSS exposure vs. CSRF handling + cross-origin cookie config for a separate frontend domain). Not a quick fix — it's a cross-cutting auth rework that changes the contract your client already integrates against. Parked for now; flag if you want to actually scope this out together.

**Refresh tokens not rotated.** Deliberate, documented tradeoff on this end (revoke-only, not rotate-on-use) — no change planned.

**Route guards are UX only.** Correct observation, but already backed by real enforcement — every `/api/v1` route requires a valid token server-side (`get_current_customer`/`require_staff_role`), independent of whatever the React route guards do. Nothing to fix.

## Data scale and pagination

**Hard `page_size: 100`/`500` caps — turns out this isn't a backend limit.** Checked every paginated router: `limit` has no server-side upper bound at all right now. The 100/500 figures are values the frontend is choosing to send, not something this API imposes — you can request a larger `limit` today with no backend change needed. (Worth knowing: there's also no *maximum* enforced, so this is on you to bound sensibly — I haven't added a cap, just confirmed there isn't a floor stopping you from asking for more.)

**SKU uniqueness check — new endpoint, built.**

```
GET /api/v1/variants/check-sku?sku=SKU-12345
```

Staff-only (same roles as variant create/update). Response:

```json
{ "sku": "SKU-12345", "exists": true }
```

Checks against every variant regardless of `is_active` (the underlying DB constraint is global, so a soft-deleted variant's old SKU still counts as taken). Should let `useSkuGenerator` drop the 500-product client-side load entirely — just call this once per candidate SKU instead.

**Inventory stock by variant, `page_size: 500`.** Same finding as above — no backend cap. If a branch's variant count ever exceeds whatever `page_size` you're requesting, raising the requested value is enough; nothing needs to change here.

## Abuse prevention and integrations

**Contact form spam/rate-limit, `replyTo` validation, Mailchimp JSONP.** None of these touch this backend at all — there's no `/contact` or newsletter endpoint anywhere in this FastAPI app. These are entirely your own Astro serverless functions' concern, not something I can act on from this repo.

## Payments and guest sessions

**Stuck payment status — fixed.** This was real. `GET /payments/{payment_id}` (the exact endpoint your integration contract has the frontend polling) only ever read the DB row — nothing re-checked PesaPal unless their IPN webhook actually reached us. If that webhook was ever dropped, a payment that genuinely succeeded would sit in `awaiting_payment` forever, and your 5-minute poll would give up on it for nothing.

Fixed: that same `GET /payments/{payment_id}` now lazily re-asks PesaPal directly whenever a payment is still `awaiting_payment`, and self-heals it if PesaPal already knows it succeeded (same confirmation email fires either way, whether the webhook or this recheck was what actually discovered it). **No frontend change needed** — same endpoint, same shape, it just resolves more reliably now.

One deliberate safety limit worth knowing: this recheck only ever *promotes* a payment to `paid`. It will never mark one `failed` on its own — PesaPal has no "still checking out" status of its own, so a poll landing while the customer is still mid-checkout could otherwise get misread as a decline. A genuine failure still only ever comes from the real webhook (or the existing 15-minute window before a stale attempt stops blocking a retry).

**Guest token merge/expiry.** Merging a guest cart into a customer's account on login/register was already built and working — no gap there. Token *expiry* (cleaning up abandoned guest carts that never convert) genuinely doesn't exist yet. Real gap, but it's a policy decision (what TTL? cleanup job vs. lazy expiry?) rather than a bug fix — parked for now, flag if you want to scope it.

## Deployment and secrets

Already resolved per the pilot-readiness checklist — Render has the real CORS origins and all required secrets configured. Nothing outstanding here.

## Product / plan alignment

Dark mode vs. `plan.md` — you already flagged this yourself as not a backend item. Agreed, no action from this end.

## Verified

**91/91 automated tests passing** (`pytest tests/ -v`), including:
- New tests for the payment self-heal (both the "resolves paid when webhook missed" case and the "never marks failed from a lazy poll" case).
- A new test for `GET /variants/check-sku`.
- Four existing tests that were quietly *pinning* the old 401/403 inconsistency (`assert status_code in (401, 403)`) — tightened to `== 401` now that it's fixed, so a regression back to inconsistent codes would get caught.

## Deploy status

**Not pushed yet.** Wanted you to see the 401 status-code change before it goes out, since your client currently has a workaround for the old (inconsistent) behavior — worth confirming removing it on your side lines up with this timing before it ships. Let me know and I'll push to `main`.
