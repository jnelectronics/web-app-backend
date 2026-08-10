# Backend changes — 2026-08-10

Response to your product-creation CORS report. You were right that it wasn't actually a CORS problem — found the real cause, fixed it, verified it against both a local instance and the live Render API directly. This doc is just about that one issue.

## What was actually happening

You correctly diagnosed the shape of it: the browser was reporting a CORS error, but the real response underneath was a `500`. Here's the specific trigger.

`POST /products` (and `PUT /products/{id}`, same schema) accepts a `description` field with no length limit on our side — but the `products.description` column in the database is capped at 2000 characters. A description over that limit passed our validation just fine, then got rejected by Postgres itself once the insert actually ran. That kind of database-level error isn't one we catch and turn into a clean JSON error — it fell through to FastAPI's raw default handler, which returns **plain text, not our usual `{success, message, data}` envelope**. And because that particular response never passes through `CORSMiddleware`, it has no `Access-Control-Allow-Origin` header — which is exactly why your browser showed it as a CORS failure instead of a 500.

Confirmed precisely, at the exact boundary, against the live production API:

| `description` length | Result before fix | Result after fix |
|---|---|---|
| 2000 chars | `200 OK` | `200 OK` (unchanged) |
| 2001 chars | `500`, plain text, no CORS header | `422`, normal JSON error |

## What changed

`ProductCreate.description` now has `max_length=2000`, matching the database column. An over-length description now fails validation on our side and comes back as a normal `422` through the same error envelope every other bad request already uses:

```json
{
  "success": false,
  "message": "Validation failed.",
  "error_code": "VALIDATION_ERROR",
  "errors": [
    {
      "type": "string_too_long",
      "loc": ["body", "description"],
      "msg": "String should have at most 2000 characters",
      "ctx": { "max_length": 2000 }
    }
  ]
}
```

## What you need to do

**Nothing required.** Any description ≤ 2000 characters works exactly as before — no contract change, nothing to update in your request shape.

**Optional, worth considering:** if the product-creation form's description field is a plain textarea or rich text box with no character limit, a real 2000-char cap will now surface as a validation error instead of a silent crash — better than before, but still not a great UX moment to hit for the first time. A client-side character counter/limit on that field would catch it earlier and more clearly. Not blocking, just flagging it since this is the field that actually triggered your report.

## Deploy status

Fixed and verified locally and directly against the deployed Render API (the DB is shared, so the verification requests hit real production data — all cleaned up afterward). **Not yet committed/pushed as of this doc** — will auto-deploy to Render on push to `main`, same as always. Confirm with us once it's live before re-testing your original repro case.

## Other things checked and ruled out while investigating

In case they crossed your mind too — all confirmed working correctly against the live API during this investigation, not related to this bug:
- CORS preflight (`OPTIONS`) for `POST /products` — correct headers, correct origin.
- `is_on_sale: true` without an applied promotion — returns a clean `422`, not a crash.
- The `variants` array shown in `JN_API_Specification.md`'s `POST /products` example — that example is stale (variants are created separately via `POST /products/{id}/variants`, per the note just above it in the same doc). Sending `variants` inline is silently ignored, not an error, so it wasn't causing your 500 either — but don't rely on it doing anything if you are sending it.
