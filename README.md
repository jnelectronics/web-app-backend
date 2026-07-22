<p align="center">
  <img src="assets/images/logo-dark.png" alt="JN Electronics" width="280">
</p>

<h1 align="center">JN Electronics — Backend API</h1>

<p align="center">
  REST API powering the JN Electronics online shopping platform: catalogue, inventory, cart, checkout, payments, and staff administration.
</p>

---

## Overview

This service is the FastAPI backend for the JN Electronics Online Shopping Platform. It exposes a versioned REST API (`/api/v1/`) consumed by the storefront frontend and future admin/mobile clients.

Full requirements and design context live in [`docs/`](docs/):

| Document | Purpose |
|---|---|
| `DATABASE_SCHEMA.md` | **Current, actual** database schema — generated from the real models, kept up to date. Start here for table/column reference. |
| `JN_API_Specification.md` | Endpoint reference (original design — some endpoints/response shapes have evolved since; the live OpenAPI spec at `/docs` is authoritative for exact request/response shapes) |
| `JN_Database_Design_Document.md` | Original schema design — superseded by `DATABASE_SCHEMA.md` for anything that's since changed |
| `JN_SRS.docx` | Software Requirements Specification |
| `JN_SAD.docx` | System Architecture Document |
| `JN_Developer_Documentation.md` | Original setup/conventions doc — see this README and `CLAUDE.md` for the current setup |

`CLAUDE.md` (repo root) has the full architecture/conventions/gotchas reference for anyone (human or AI) working on this codebase.

## Tech Stack

| Layer | Choice |
|---|---|
| Framework | FastAPI |
| ORM / Migrations | SQLAlchemy 2.0 / Alembic |
| Database | PostgreSQL (Neon, hosted) |
| Background jobs | FastAPI `BackgroundTasks` (in-process) — Redis + RQ code exists (`redis_queue.py`/`worker.py`) but isn't currently wired in; see `CLAUDE.md` |
| Auth | JWT (access + refresh), Argon2 password hashing |
| Payments | PesaPal API 3.0 |
| Error monitoring / logging | Sentry |
| Media storage | Client-hosted URLs today — no Cloudinary integration yet |

## Repository Structure

Flat, not modular — everything lives at the repo root rather than under `app/`:

```
main.py              # FastAPI app, router wiring, exception handlers, CORS, Sentry/logging setup
database.py          # engine/session/Base/mixins
models.py            # every SQLAlchemy model
schemas.py           # every Pydantic schema
security.py          # password hashing, JWT, auth dependencies
envelope.py           # the {success,message,data} response envelope (route_class)
audit.py             # audit_logs writer
observability.py     # shared Sentry + logging setup (used by both main.py and worker.py)
pesapal_client.py    # PesaPal API 3.0 wrapper
jobs.py              # background job functions - called via FastAPI BackgroundTasks today
redis_queue.py       # Redis connection + RQ queue - NOT currently used (see CLAUDE.md)
worker.py            # RQ worker process entry point - NOT currently used (see CLAUDE.md)
routers/             # one file per domain - routes only
alembic/             # migrations
tests/                # pytest suite, one file per phase
seed_admin.py         # one-off script: creates the first System Administrator
register_pesapal_ipn.py  # one-off script: registers the PesaPal webhook URL
```

This was chosen deliberately over a layered `app/modules/<domain>/{router,service,repository,schemas,models}.py` structure — simpler to reason about at this project's size. See `CLAUDE.md` for the full rationale and conventions.

## Getting Started

### Prerequisites

| Tool | Notes |
|---|---|
| Python | 3.11+ (see `.python-version` — deployment pins this exactly) |
| Git | |
| Docker | Only needed if reviving the RQ/Redis worker path — not required for normal local dev right now |

No local Postgres needed — this project runs against a hosted Neon instance.

### 1. Clone and configure

```bash
git clone https://github.com/jnelectronics/web-app-backend.git
cd web-app-backend
cp .env.example .env
```

Fill in `.env` with real values — see `.env.example` for every variable this project reads (database, JWT secret, Redis, PesaPal, CORS, Sentry). **Never commit `.env`.**

### 2. Install dependencies

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

### 3. Run database migrations

```bash
alembic upgrade head
```

### 4. Seed the initial System Administrator

The platform ships with exactly one seeded System Administrator account, created via a one-off script (no account has permission to create one through the API):

```bash
python seed_admin.py
```

### 5. Run the API

```bash
uvicorn main:app --reload
```

That's it — background jobs (currently just password-reset "emails") run via FastAPI's `BackgroundTasks`, in this same process, no separate worker needed. (There's also an RQ + Redis + `worker.py` path in the codebase if this project ever needs real cross-process job queueing again — see `CLAUDE.md`'s "Background workers" section — but it isn't used right now.)

### 6. Verify it's running

| Service | URL |
|---|---|
| API | http://localhost:8000/api/v1 |
| Swagger docs | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |

### PesaPal webhook (local dev only)

PesaPal can't reach `localhost`, so testing real payment callbacks locally needs a public tunnel:

```bash
ngrok http 8000
python register_pesapal_ipn.py https://<your-ngrok-url>/api/v1/payments/webhook
```

Put the printed `ipn_id` into `.env` as `PESAPAL_IPN_ID`.

## Testing

```bash
pytest tests/ -v
```

Runs against the real (hosted) database — there's no separate test database or transactional rollback-per-test. External services PesaPal talks to are mocked at the test boundary (`tests/test_payments.py`'s `mock_pesapal` fixture); everything else is hit for real.

## API Conventions

- Base path: `/api/v1/`.
- All request/response bodies are JSON, `snake_case` fields, UUID identifiers.
- Every successful response is wrapped in `{"success": true, "message": ..., "data": ...}`; errors are `{"success": false, "message": ..., "error_code": ...}`.
- Access tokens: `Authorization: Bearer <token>`. Guest cart/checkout: `X-Guest-Token` header.
- The FastAPI-generated OpenAPI spec at `/docs` is the authoritative, always-current contract.

## Deployment

Hosted on [Render](https://render.com) (free tier, connected directly to this GitHub repo for auto-deploy on push to `main`). Required environment variables match `.env.example` — set them in Render's dashboard, not in a committed file. Python version is pinned via `.python-version` — Render's default is much newer than this project has ever been tested against.

## Contributing

- New/changed tables or columns: update `docs/DATABASE_SCHEMA.md` in the same change.
- New/changed endpoints: the live OpenAPI spec at `/docs` is authoritative; update `JN_API_Specification.md` if it materially diverges.
- Never commit secrets or `.env` files.
- Run `pytest tests/ -v` before pushing.
