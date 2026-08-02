# Frontend Integration Contract

Practical integration guidance for the frontend team, covering things not fully spelled out in `JN_API_Specification.md` (the full endpoint-by-endpoint reference — check there for request/response shapes not covered here) and everything added after that spec was written: Google Sign-In, real Cloudinary image hosting, and the exact page-level behavior expected around password reset and payment confirmation.

## Base URL & response shape

- Production: `https://web-app-backend-2vca.onrender.com/api/v1`
- Every successful response is wrapped: `{"success": true, "message": "...", "data": {...}}` — the actual payload is under `data`.
- Every error response: `{"success": false, "message": "...", "error_code": "..."}`.

## Authentication — email/password

- `POST /auth/register` — `{full_name, email, phone_number?, password}` → `{customer, access_token, refresh_token}`. Logs the customer in immediately, no separate login call needed.
- `POST /auth/login` — `{email, password}` → `{access_token, refresh_token}`.
- `POST /auth/refresh` — `{refresh_token}` → a new `access_token` (same `refresh_token` back, it isn't rotated).
- `POST /auth/logout` — needs `Authorization: Bearer <access_token>`, no body. Revokes every active refresh token for that account (there's no "log out this one device only").
- Attach `Authorization: Bearer <access_token>` on every authenticated request.

## Authentication — Google Sign-In

Not in the original spec — added afterward. Client ID (same value on both sides):

```
253011211945-9o2bt73brv6i93hcui62tpg4npiomha3.apps.googleusercontent.com
```

1. Use Google's Identity Services JavaScript library on the frontend, initialized with the Client ID above, to render the "Sign in with Google" button/prompt.
2. On success, Google's library hands you an **ID token** (a signed string) — don't try to read or use it yourself, just forward it.
3. `POST /auth/google` — `{id_token}` → same `{access_token, refresh_token}` shape as a normal login.
4. `401` → the token was invalid, expired, or the Google account's email isn't verified. `503` (`error_code: GOOGLE_SIGNIN_UNAVAILABLE`) → Google sign-in is temporarily disabled server-side.

If the email matches an existing password-based account, that account is logged into directly (auto-linked) — no separate "connect your account" step needed on the frontend.

## Password reset

1. `POST /auth/password/forgot` — `{email}`. Always returns the same generic success message regardless of whether the email exists (prevents leaking which emails are registered) — a real reset link is emailed only if the account exists.
2. Build a `/reset-password` page:
   - Read `token` from the URL's query string.
   - Show a new-password form (+ confirm field).
   - Submit → `POST /auth/password/reset` — `{token, new_password}`.
   - `200` → success, redirect to login. `401` → "this link has expired, please request a new one" (send them back to step 1, a brand new token gets generated — the old one can't be reused or refreshed).

## Catalog (products/categories/branches)

All `GET` endpoints here are public, no auth needed for browsing. Product image URLs in the response (`image_url`) are already real, permanent Cloudinary-hosted URLs — just display them directly, nothing special needed on your end.

## Cart & checkout

- Registered customers: `Authorization: Bearer <access_token>` header on cart requests.
- Guests: an `X-Guest-Token` header — **the frontend generates and persists this itself** (e.g. a random UUID kept in `localStorage`); there's no endpoint that issues one.
- `POST /orders` — `{guest_full_name, guest_phone_number, guest_email?, delivery_address}` → the created order. `guest_email` is optional but **should be collected whenever possible** — without it, the customer won't receive an order confirmation email, and won't receive a payment confirmation email either later.

## Payments

1. `POST /orders/{order_id}/payments` — `{provider, amount}` → `{id, redirect_url, status: "awaiting_payment", ...}`.
2. Send the customer's browser to `redirect_url` — **this is PesaPal's own hosted checkout page.** Don't build a card/mobile-money form yourselves.
3. Possible errors on this call: `409` (`error_code: PAYMENT_IN_PROGRESS`) — a payment attempt for this order is already active; don't let the customer start a second one, tell them to wait or check status instead. `409` (`error_code: DUPLICATE_PAYMENT`) — the order's already paid. `503` (`error_code: PAYMENTS_UNAVAILABLE`) — payments temporarily disabled server-side.
4. Build an `/order-confirmation` page:
   - The customer lands here after PesaPal redirects back — **this alone is not proof the payment succeeded.**
   - You'll need the `payment_id` (from step 1's response) carried through to this page yourselves — e.g. as a URL param you set when initiating payment. PesaPal's own redirect doesn't include it.
   - Poll `GET /payments/{payment_id}` every couple of seconds until `status` is `paid` or `failed`, then show the matching message. If it's still unresolved after ~30 seconds, show something like "still processing — we'll email you once it's confirmed" rather than polling forever.

## CORS

Every real frontend origin (scheme + host + port, no path) must be explicitly whitelisted server-side or the browser will block the request entirely — this can't be worked around from the frontend side. Currently allowed: `localhost:3000`/`5173`/`4321`, `https://jnelectronics.vercel.app`, `https://www.jnelectronics.ug`, `https://test.jnelectronics.ug`. **Tell us before deploying to any new domain or port** so it can be added.

## Error codes worth handling specially

Not exhaustive — see `JN_API_Specification.md` §6 for the full table.

| `error_code` | Status | What it means for the UI |
|---|---|---|
| `PAYMENTS_UNAVAILABLE` | 503 | Payments temporarily disabled server-side — show a "coming soon" message, not a generic error |
| `GOOGLE_SIGNIN_UNAVAILABLE` | 503 | Google Sign-In temporarily disabled server-side |
| `IMAGE_UPLOAD_UNAVAILABLE` | 503 | Cloudinary temporarily disabled server-side (staff/admin uploads only) |
| `PAYMENT_IN_PROGRESS` | 409 | A payment attempt for this order is already active |
| `DUPLICATE_PAYMENT` | 409 | This order is already paid |
| `VALIDATION_ERROR` | 422 | Show field-level messages from the response body, not a generic error |
