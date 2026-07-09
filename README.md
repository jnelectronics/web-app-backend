<p align="center">
  <img src="assets/images/logo-dark.png" alt="JN Electronics" width="280">
</p>

<h1 align="center">JN Electronics — Backend API</h1>

<p align="center">
  REST API powering the JN Electronics online shopping platform: catalogue, inventory, cart, checkout, payments, and staff administration.
</p>

---

## Overview

This service is the FastAPI backend for the JN Electronics Online Shopping Platform. It exposes a versioned REST API (`/api/v1/`) consumed by the React storefront, the administrative dashboard, and future Flutter mobile apps.

Full requirements and design context live in [`docs/`](docs/):

| Document | Purpose |
|---|---|
| `JN_SRS.docx` | Software Requirements Specification |
| `JN_SAD.docx` | System Architecture Document |
| `JN_Database_Design_Document.md` | Schema, entities, relationships |
| `JN_API_Specification.md` | Full endpoint reference |
| `JN_Developer_Documentation.md` | Setup, conventions, workflow |

## Tech Stack

| Layer | Choice |
|---|---|
| Framework | FastAPI |
| ORM / Migrations | SQLAlchemy 2.0 / Alembic |
| Database | PostgreSQL (Neon) |
| Queue / Cache | Redis + RQ |
| Auth | JWT (access + refresh), Argon2 password hashing |
| Media storage | Cloudinary |

## Repository Structure

```
app/
├── core/            # config, security, logging, exceptions, shared deps
├── db/              # session, declarative base, enums, generic repository
├── modules/          # one folder per business domain
│   ├── auth/  users/  branches/  categories/  products/
│   ├── inventory/  cart/  orders/  payments/
│   └── promotions/  dashboard/  audit/
├── integrations/    # cloudinary, email, payment, storage clients
├── workers/         # email, payment, promotion, cleanup background jobs
├── middleware/
└── main.py

alembic/             # migrations
tests/
├── unit/  integration/  api/
scripts/             # one-off ops scripts (e.g. seed_admin)
requirements/         # base / dev / prod dependency sets
```

Each module owns its own `router.py`, `schemas.py`, `service.py`, `repository.py`, and `models.py`. Business logic lives in the service layer; repositories stay dumb; routers stay thin. See the Developer Documentation for the full conventions.

## Getting Started

### Prerequisites

| Tool | Version |
|---|---|
| Docker & Docker Compose | latest stable |
| Python | 3.11+ (if running outside Docker) |
| Git | latest stable |

### 1. Clone and configure

```bash
git clone https://github.com/jnelectronics/web-app-backend.git
cd web-app-backend
cp .env.example .env
```

Fill in `.env` with local values — never commit it.

### 2. Start the environment

```bash
docker compose up --build
```

This brings up the FastAPI API, an RQ worker, Redis, and a local PostgreSQL instance.

### 3. Run database migrations

```bash
docker compose exec api alembic upgrade head
```

### 4. Seed the initial System Administrator

Per FR-AUTH-011, the platform ships with exactly one seeded System Administrator account, created via a one-off script (no account yet has permission to create it through the API):

```bash
docker compose exec api python -m scripts.seed_admin
```

### 5. Verify it's running

| Service | URL |
|---|---|
| API | http://localhost:8000/api/v1 |
| Health check | http://localhost:8000/health |
| Swagger docs | http://localhost:8000/docs |
| ReDoc | http://localhost:8000/redoc |

## Running Without Docker

```bash
pip install -r requirements/dev.txt
uvicorn app.main:app --reload
```

## Testing

```bash
docker compose exec api pytest
docker compose exec api pytest --cov=app --cov-report=term-missing
```

- `tests/unit/` — service-layer business rules, no DB/network.
- `tests/integration/` — repository + DB, each test rolled back.
- `tests/api/` — endpoint tests via FastAPI's `TestClient`.

## API Conventions

- Base path: `/api/v1/`. Breaking changes go to `/api/v2/`.
- All request/response bodies are JSON, `snake_case` fields, UUID identifiers.
- Every response follows the standard envelope (`success`, `message`, `data`, optional `pagination`); errors carry an `error_code` — see `JN_API_Specification.md` §2–§6.
- Access tokens: `Authorization: Bearer <token>`. Guest cart/checkout: `X-Guest-Token` header.

The FastAPI-generated OpenAPI spec at `/docs` is the authoritative, always-current contract; `JN_API_Specification.md` is its human-readable companion.

## Contributing

- Branch naming: `feature/<desc>`, `fix/<desc>`, `chore/<desc>`.
- Open a Pull Request against `main`; at least one review + passing CI required.
- New/changed endpoints must be reflected in the API Specification document in the same PR.
- New/changed tables/columns must be reflected in the Database Design Document.
- Never commit secrets or `.env` files.

See `docs/JN_Developer_Documentation.md` for the full contribution checklist.
