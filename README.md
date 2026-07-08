# Property Management API

![CI](https://github.com/NouhJama/property-management-api/actions/workflows/ci.yml/badge.svg)
![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)
![Python 3.13](https://img.shields.io/badge/python-3.13-blue.svg)

An async, layered REST API for managing properties, units, tenants, employees,
rent, and renovations — built with **FastAPI** and **SQLAlchemy 2.0 (async)**.

> **Status:** Early stage. The **User** slice (model → schema → repository →
> service → security → dependency wiring) and the **Auth** router
> (`register` / `login` / `me`) are complete end to end. Property, Unit,
> Tenant, Lease, and Payment resources are scaffolded but not yet implemented.

---

## Table of Contents

- [Tech Stack](#tech-stack)
- [Architecture](#architecture)
- [How It Works](#how-it-works)
- [Project Structure](#project-structure)
- [Current Feature Status](#current-feature-status)
- [API Endpoints](#api-endpoints)
- [Getting Started](#getting-started)
- [Database & Migrations](#database--migrations)
- [Testing, Linting & Formatting](#testing-linting--formatting)
- [Coding Standards](#coding-standards)
- [Git Workflow](#git-workflow)
- [Custom Slash Commands](#custom-slash-commands)
- [Roadmap](#roadmap)
- [License](#license)

---

## Tech Stack

| Concern | Choice |
|---|---|
| Language / runtime | Python 3.13 |
| Package manager | [uv](https://docs.astral.sh/uv/) |
| Web framework | [FastAPI](https://fastapi.tiangolo.com/) |
| ORM | SQLAlchemy 2.0 — **async** (`create_async_engine`, `async_sessionmaker`, `AsyncSession`) |
| Database driver | asyncpg |
| Database | PostgreSQL 17 (via Docker) |
| Migrations | Alembic (async `env.py`) |
| Validation / settings | Pydantic v2 (`pydantic[email]`) + `pydantic-settings` |
| Authentication | JWT (OAuth2 password flow) via **PyJWT** |
| Password hashing | **pwdlib[bcrypt]** (`PasswordHash((BcryptHasher(),))`) |
| Testing | pytest + pytest-asyncio + httpx |
| Lint / format (CI-enforced) | ruff (`ruff check`, `ruff format --check`) |
| Lint / format (also available) | flake8, black |
| CI | GitHub Actions, PostgreSQL service container |

**Deliberately not used:** `python-jose` (unmaintained, replaced by PyJWT) and
`passlib` (incompatible with bcrypt ≥ 4.1, replaced by `pwdlib`). No sync
SQLAlchemy engine anywhere — the asyncpg driver requires the async engine
end to end.

---

## Architecture

The project follows a strict **four-layer architecture**. Each layer only
talks to the one directly below it:

```
HTTP request
     │
     ▼
routers/        FastAPI path operations — request/response only, no logic
     │  Depends(get_..._service)
     ▼
services/       Business rules, orchestration, password hashing calls
     │  self.repo.<query>()
     ▼
repositories/   Async SQLAlchemy queries — no business logic, no HTTP
     │  AsyncSession
     ▼
models/         SQLAlchemy ORM classes — table schema only
     │
     ▼
PostgreSQL (via asyncpg)
```

`schemas/` (Pydantic v2) sits at the HTTP boundary alongside `routers/` —
it validates incoming JSON and shapes outgoing responses, but is not part of
the call chain above.

`core/` is the cross-cutting layer every other layer depends on:

| File | Responsibility |
|---|---|
| `core/config.py` | `Settings` — loads and validates every environment variable via `pydantic-settings` |
| `core/security.py` | The **only** module allowed to import `pwdlib` or `jwt`. Exposes exactly four helpers: `hash_password`, `verify_password`, `create_access_token`, `verify_token` |
| `core/dependencies.py` | Composition layer: wires `get_db → get_user_repository → get_user_service`, and owns authentication (`oauth2_scheme`, `get_current_user`, `get_current_active_superuser`) |

`database.py` is the async database foundation: the `engine`
(`create_async_engine`, `pool_pre_ping=True`), the `AsyncSessionLocal`
session factory, the `Base` declarative class every model inherits from, and
the `get_db()` FastAPI dependency. No queries or business logic live there.

---

## How It Works

**A typical request**, e.g. `GET /api/v1/auth/me` with a Bearer token:

1. `app/main.py` receives the request and routes it to `routers/auth.py`.
2. FastAPI resolves `Depends(get_current_user)` from `core/dependencies.py`:
   - `oauth2_scheme` extracts the raw JWT from the `Authorization` header
     (401 automatically if missing).
   - `verify_token()` (in `core/security.py`) checks the signature and
     expiry, and returns the decoded payload — any failure is a uniform
     401 so clients never learn *why* a token was rejected.
   - The `sub` claim (a string) is parsed back into an integer user id and
     loaded via `UserService.get_user_by_id`.
   - A deactivated account (`is_active=False`) short-circuits with 400.
3. The router hands the resolved `User` straight back — no business logic
   lives in the router itself.
4. The response is filtered through `UserResponse` (`schemas/user.py`),
   which deliberately has no `hashed_password` field, so secrets can never
   leak through a response model.

**Login** (`POST /api/v1/auth/login`) works the other direction: the router
calls `UserService.authenticate_user()`, which looks up the user by email
and verifies the password via `verify_password()` — using the *same* error
message for "no such user" and "wrong password" to prevent email
enumeration. Only once identity is confirmed does the **router** (not the
service) call `create_access_token({"sub": str(user.id)})` and return a
`Token`.

**Registration** (`POST /api/v1/auth/register`) hashes the password with
`hash_password()` before it ever reaches the repository, and hardcodes
`is_superuser=False` in the service layer regardless of what a client sends.

---

## Project Structure

```
.
├── app/
│   ├── main.py                  # FastAPI app, lifespan, CORS, router mounting
│   ├── database.py               # Async engine, session factory, Base, get_db()
│   ├── core/
│   │   ├── config.py              # Settings (env-driven)
│   │   ├── security.py            # Password hashing + JWT (only module touching pwdlib/jwt)
│   │   └── dependencies.py        # DI wiring + auth dependencies
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── user.py                 # Implemented
│   │   └── properties.py, units.py, tenants.py, leases.py, payments.py  # Empty — not yet scaffolded
│   ├── schemas/
│   │   └── user.py                 # UserBase/Create/Update/Response/InDB + Token
│   ├── repositories/
│   │   ├── user_repository.py      # Implemented
│   │   └── tenant_repository.py, unit_repository.py  # Docstring stubs only
│   ├── services/
│   │   └── user_service.py         # Implemented — create/authenticate/get/update/delete/list
│   └── routers/
│       └── auth.py                 # Implemented — register, login, me
├── migrations/
│   ├── env.py                     # Async Alembic environment
│   └── versions/                  # f166860421d8_create_users_table.py
├── tests/
│   ├── conftest.py
│   └── test_smoke.py              # Config-loading smoke tests
├── .github/workflows/ci.yml       # Lint + format + test on every push/PR to main
├── docker-compose.yml             # Local PostgreSQL 17 container
├── alembic.ini
├── pyproject.toml
└── CLAUDE.md                      # Full engineering conventions for AI-assisted work
```

---

## Current Feature Status

| Resource | Model | Schema | Repository | Service | Router |
|---|:---:|:---:|:---:|:---:|:---:|
| **User / Auth** | ✅ | ✅ | ✅ | ✅ | ✅ (`/api/v1/auth/*`) |
| Property | — | — | — | — | — |
| Unit | — | — | — | — | — (repository docstring stub only) |
| Tenant | — | — | — | — | — (repository docstring stub only) |
| Lease | — | — | — | — | — |
| Payment | — | — | — | — | — |

---

## API Endpoints

| Method | Path | Auth required | Description |
|---|---|:---:|---|
| GET | `/` | No | Welcome message + doc links |
| GET | `/health` | No | Liveness + live DB connectivity check |
| POST | `/api/v1/auth/register` | No | Create a new user account → `201 UserResponse` |
| POST | `/api/v1/auth/login` | No | OAuth2 password flow login → `200 Token` (JWT) |
| GET | `/api/v1/auth/me` | **Yes** (Bearer) | Return the authenticated user's profile |

Interactive docs: **`/docs`** (Swagger UI, with an **Authorize** button wired
to the OAuth2 password flow) and **`/redoc`**.

---

## Getting Started

### Prerequisites

- Python 3.13
- [uv](https://docs.astral.sh/uv/)
- Docker + Docker Compose

### 1. Clone and configure environment

```bash
git clone git@github.com:NouhJama/property-management-api.git
cd property-management-api
cp .env.example .env
```

Fill in `.env` — `Settings` (`app/core/config.py`) requires:

| Variable | Purpose |
|---|---|
| `APP_NAME`, `APP_VERSION` | Shown in `/`, `/health`, and the OpenAPI docs |
| `DEBUG` | Enables SQL echo logging and FastAPI debug mode |
| `DATABASE_URL` | Must be `postgresql+asyncpg://...` — asyncpg driver only |
| `SECRET_KEY`, `ALGORITHM`, `ACCESS_TOKEN_EXPIRE_MINUTES` | JWT signing config |
| `ALLOWED_ORIGINS` | CORS allow-list |

Never hard-code credentials elsewhere — everything reads through `Settings`.

### 2. Start the database

```bash
docker compose up -d
docker compose ps   # confirm property_db is healthy
```

### 3. Install dependencies

```bash
uv sync
```

### 4. Apply migrations

```bash
uv run alembic upgrade head
```

### 5. Run the API

```bash
uv run uvicorn app.main:app --reload
```

Then open **http://localhost:8000/docs**.

---

## Database & Migrations

Every new SQLAlchemy model requires **two** manual steps before Alembic can
see it — this is the most common Alembic mistake in this project:

1. Import the model class in `migrations/env.py` under the model-imports block.
2. Generate the migration:

```bash
# Generate a migration after changing or adding a model
uv run alembic revision --autogenerate -m "describe the change"

# Review the generated file in migrations/versions/, then apply it
uv run alembic upgrade head

# Roll back the last migration
uv run alembic downgrade -1

# Show the current migration
uv run alembic current

# Verify models and DB are in sync
uv run alembic check
```

---

## Testing, Linting & Formatting

```bash
# Run the test suite
uv run pytest tests/ -v

# Lint (enforced in CI)
uv run ruff check .

# Format check (enforced in CI)
uv run ruff format --check .
uv run ruff format .        # auto-fix

# Also available per CLAUDE.md conventions
uv run black app/ tests/
uv run flake8 app/ tests/
```

CI (`.github/workflows/ci.yml`) runs on every push and PR to `main`: it spins
up a PostgreSQL 17 service container, then runs `ruff check`,
`ruff format --check`, and the full `pytest` suite.

---

## Coding Standards

- PEP 8, **max line length 100**.
- Descriptive names; `Annotated[...]`-style type hints on every function
  signature and variable annotation.
- Docstrings on every function and class.
- **Async end to end** — no sync `create_engine()`/`sessionmaker()` calls.
- FastAPI: `Annotated` dependencies, `lifespan` context manager (not the
  deprecated `@app.on_event`), Pydantic v2 response models.
- Pydantic v2 only: `model_validator`, `field_validator`, `ConfigDict` —
  never v1's `@validator` / `class Config`.
- Strict layering: repositories only query, services only orchestrate,
  routers only wire HTTP to services. No layer reaches past the one below it.
- All routes are prefixed `api/v1/...`.
- Before writing or editing code that touches FastAPI, SQLAlchemy, Pydantic,
  or Alembic, fetch current docs via the **Context7 MCP server** rather than
  relying on training data — these libraries move fast.

---

## Git Workflow

**GitHub Flow** — one protected branch (`main`); every feature merges via PR.

### Before starting any task

```bash
git branch --show-current
# If on main:
git checkout main && git pull origin main
git checkout -b feature/describe-the-task
```

### Branch naming

| Prefix | Use for |
|---|---|
| `feature/short-description` | New features |
| `fix/what-you-are-fixing` | Bug fixes |
| `chore/what-you-are-doing` | Config, tooling |
| `test/what-you-are-testing` | Tests only |
| `docs/what-you-are-writing` | Documentation |

### Standard flow

```bash
git checkout main && git pull origin main
git checkout -b feature/task-name
# do the work
git add . && git commit -m "type: description"
git push origin feature/task-name
# open PR on GitHub → review → merge → delete branch
```

### Never

- Never commit directly to `main`.
- Never force-push to `main`.
- Never skip the PR — even for a one-line change.

---

## Custom Slash Commands

Project-specific Claude Code commands live in `.claude/commands/`:

| Command | Purpose |
|---|---|
| `/new-endpoint <resource>` | Scaffolds a new resource (schema, model, repository, service, router, test) as docstring-only stubs across all layers — no real logic unless explicitly requested |
| `/sync-main <branch>` | Post-merge cleanup: switch to `main`, pull, safe-delete the merged local branch, prune stale remote refs |

---

## Roadmap

- [ ] Implement `Property`, `Unit`, `Tenant`, `Lease`, and `Payment` models
- [ ] Implement their repositories, services, and routers (currently stubs
      or entirely empty)
- [ ] Wire remaining routers into `app/main.py`
- [ ] Expand `tests/` beyond config smoke tests — unit + integration
      coverage per resource
- [ ] Rent tracking and renovation tracking business logic

---

## License

MIT — see [LICENSE](LICENSE).
