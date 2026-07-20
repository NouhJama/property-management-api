# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A property management API that manages properties, units, tenants, and employees, and tracks rent and renovations. Early stage: the User slice exists end to end (model, schemas, repository, service, security helpers, dependency wiring); routers and the remaining resources are not built yet.

## Toolchain

- This project uses **uv** for Python package management (Python 3.13).
- Database management is handled with **SQLAlchemy 2.0 (async)** and **Alembic** for migrations. The API will be built using **FastAPI**(Always use the official documentation).
- Database: **PostgreSQL**, driver: **asyncpg**.
- All database access is **asynchronous, end to end**: `create_async_engine`, `async_sessionmaker`, `AsyncSession`, and `DeclarativeBase` (SQLAlchemy 2.0 style). Do not introduce sync `create_engine()`/`sessionmaker()` code — the asyncpg driver only works with the async engine.
- Validation: **Pydantic V2 models** for data validation and settings management.
  Installed as `pydantic[email]` so `EmailStr` fields work (requires `email-validator`).

## Documentation & Library Accuracy

This project depends on libraries that evolve quickly. Always fetch current official documentation
via the **Context7 MCP server** before writing or modifying code that touches the libraries below.
Do not rely on training-data knowledge alone — it may reflect outdated APIs or deprecated patterns.

| Library | When to fetch docs | Key patterns to follow |
|---|---|---|
| **FastAPI** | Before writing any route, dependency, middleware, or lifespan handler | `Annotated` dependencies, `lifespan` context-manager startup/shutdown (not `@app.on_event`), Pydantic v2 response models |
| **SQLAlchemy (async 2.0)** | Before writing models, queries, or session management | `create_async_engine`, `async_sessionmaker`, `AsyncSession`, `select()` 2.0-style queries — no legacy `Query` API |
| **Pydantic v2** | Before writing schemas or validators | `model_validator`, `field_validator`, `model_config`, `ConfigDict` — not v1 `@validator` / `class Config` |
| **Alembic** | Before writing or editing migration scripts or `env.py` | Async engine setup in `env.py`, `run_async_migrations()` pattern |

### How to fetch docs with Context7

```
# 1. Resolve the library ID
mcp__context7__resolve-library-id  →  { libraryName: "fastapi" }

# 2. Fetch relevant docs
mcp__context7__query-docs  →  { context7CompatibleLibraryID: "<id>", query: "<topic>" }
```

Fetch docs **before** writing code, not after a review catches a stale pattern.

## Authentication and security
- Authentication: **JWT** (OAuth2 password flow), implemented with **PyJWT** (`import jwt`).
  Do not use `python-jose` — it was removed from this project (unmaintained).
- Password hashing: **pwdlib[bcrypt]** (`PasswordHash((BcryptHasher(),))`).
  Do not use `passlib` — it was removed (passlib 1.7.4 is incompatible with bcrypt >= 4.1),
  and do not call the `bcrypt` library directly.
- All hashing and JWT logic lives in `app/core/security.py`, which exposes exactly four helpers:
  `hash_password`, `verify_password`, `create_access_token`, `verify_token`.
  No other module may import `pwdlib` or `jwt` directly. `verify_token` raises a 401
  HTTPException ("Could not validate credentials", `WWW-Authenticate: Bearer`) on any
  invalid token — expired, tampered, or malformed all get the same error.
- JWT settings come from `.env` via `app/core/config.py`: `secret_key`, `algorithm`,
  `access_token_expire_minutes`.
- CORS: Configure CORS middleware to allow requests from trusted origins.

```bash
# Install dependencies
uv sync

# Run the app
uv run python main.py

# Add a dependency
uv add <package>

# Run a script/tool in the venv
uv run <command>
```

## Testing, Linting, and Formatting
- Testing: **pytest** for unit and integration tests.
- Linting: **flake8** for code style and quality checks.
- Formatting: **black** for consistent code formatting.

## Code/Project Organization
- `app/main.py`: Entry point for the application.
- `app/database.py`: The async database foundation layer. Defines:
  - `engine` — `create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)`.
  - `AsyncSessionLocal` — `async_sessionmaker` bound to `engine` (`expire_on_commit=False`, `autoflush=False`).
  - `Base` — `class Base(DeclarativeBase): pass`; all models inherit from this.
  - `get_db()` — async generator dependency (`async with AsyncSessionLocal() as session: yield session`) used via FastAPI's `Depends(get_db)`. No model, repository, or business logic belongs in this file.
- `models/`: SQLAlchemy ORM models for database tables. Inherit from `Base` in `app/database.py`.
- `schemas/`: Pydantic models for request and response validation.
- `routers/`: FastAPI routers for different API endpoints.
- `services/`: Business logic and orchestration — calls repositories, never touches the DB directly.
- `repositories/`: Async SQLAlchemy query functions, one file per model (e.g. `tenant_repository.py`).
  - **Naming convention**: `<model>_repository.py`
  - **Rules**: only DB queries here — no business logic, no password hashing, no HTTP concerns. Every function receives an `AsyncSession` (via `Depends(get_db)`) and uses `await`.
  - **Architecture flow**: `routers → services → repositories → database (SQLAlchemy async / asyncpg)`
- `core/`: Core utilities, such as database session management, security helpers, and config.
  - `app/core/dependencies.py`: The dependency-wiring (composition) layer. Two jobs:
    1. **Assembly** — factory chain `get_db → get_user_repository → get_user_service`, so routes
       declare `Annotated[UserService, Depends(get_user_service)]` instead of building the chain
       by hand. New resources follow the same pattern: `get_<model>_repository` / `get_<model>_service`.
    2. **Authentication** — `oauth2_scheme` (`OAuth2PasswordBearer`, `tokenUrl="api/v1/auth/login"`),
       `get_current_user` (JWT → verified, active `User`; any failure — bad token, unknown or
       deleted user — returns the same generic 401; deactivated account returns 400), and
       `get_current_active_superuser` (chains on `get_current_user`, raises 403 for non-admins).
    No business logic, queries, or hashing/JWT internals here — it only wires existing pieces.
- `migrations/`: Alembic migration scripts for database schema changes.
- `tests/`: Unit and integration tests for the application.

## Development Workflow
1. **Branching**: Use feature branches for new features and bug fixes. Merge into `main` only after code review and testing.
2. **Code Reviews**: As I am now a solo developer, I will perform code reviews myself, so I don't need to worry about getting reviews from others.
3. **Testing**: Write tests for new features and bug fixes. Run all tests before pushing changes.
4. **Documentation**: Update documentation as needed when adding new features or changing existing functionality.

## Workflow - before any code is written
1. Understand the feature fully, including the requirements and any edge cases, ask clear questions if anything is unclear.
2. Plan the implementation, including the necessary database models, API endpoints, and any business logic.
3. Write the code following the code standards outlined below.
4. Understand which layers of the application the code belongs to (models, schemas, routers, services, core, etc.) and place it accordingly.
5. Write tests for the new code, ensuring good coverage and testing edge cases.
6. Run all tests to ensure everything is working correctly.
7. Update documentation if necessary, including docstrings and any relevant README sections.

## Code standards
- Follow PEP 8 for Python code style, max line of 100 characters.
- Use descriptive variable and function names.
- Write docstrings for all functions and classes.
- Use type hints for function signatures and variable annotations.
- Avoid global state and side effects where possible.
- Handle exceptions gracefully and provide meaningful error messages.
- Keep functions and methods small and focused on a single responsibility.
- All routes use 'api/v1' prefix, e.g., `api/v1/properties`, `api/v1/units`, etc.

## Custom Slash Commands

Project-specific Claude Code slash commands live in `.claude/commands/`. Each `.md` file in that directory becomes a `/filename` command available in Claude Code sessions.

### `/new-endpoint <resource>`
Scaffolds a new resource across all architecture layers. For example, `/new-endpoint property` creates:

| File | Purpose |
|---|---|
| `app/schemas/property.py` | Pydantic V2 Create/Update/Response schema stubs |
| `app/models/property.py` | SQLAlchemy ORM model stub (skipped if exists) |
| `app/repositories/property_repository.py` | Async query function stubs |
| `app/services/property_service.py` | Business logic stubs |
| `app/routers/property.py` | FastAPI router stub (prefix: `/api/v1/property`) |
| `tests/test_property.py` | Placeholder test file |

The command only creates stubs with descriptive docstrings — no real implementation — unless explicitly asked. All files are staged for review but not committed.

## ⚠️ Git workflow — CRITICAL RULES

### Workflow: GitHub Flow
One protected branch: main
All features merge directly to main via PR

### BEFORE starting ANY task:
1. Check current branch:  git branch --show-current
2. If on main — STOP and create a feature branch first:
   git checkout main && git pull origin main
   git checkout -b feature/describe-the-task
3. Only start work after confirming you are NOT on main
4. Always make sure that the virtual environment is activated before running any commands, especially before installing new dependencies or running the application. 

### Branch naming
feature/short-description   →  new features
fix/what-you-are-fixing     →  bug fixes  
chore/what-you-are-doing    →  config, tooling
test/what-you-are-testing   →  tests only
docs/what-you-are-writing   →  documentation

### Every task follows this exact flow
git checkout main && git pull origin main
git checkout -b feature/task-name
# do the work
git add . && git commit -m "type: description"
git push origin feature/task-name
# open PR on GitHub → review → merge → delete branch

### NEVER
- Never commit directly to main
- Never force push to main
- Never skip the PR — even for a one-line change

## LOCAL DEVELOPMENT SETUP

### Before starting work each session
1. Start the database container: `docker compose up -d`
2. Activate the virtual environment: `uv sync`
3. Check the container is healthy: `docker compose ps`

## DATABASE CONNECTION

The database connection string is stored in `.env`. Do not hard-code credentials anywhere — always look up the connection string in `.env`.

## Alembic rules

Every time a new model file is created in `app/models/`, do TWO things immediately:
1. Import the model class in `migrations/env.py` under the "Model imports" comment block —
   uncomment the relevant line or add a new import line.
2. Run: `alembic revision --autogenerate -m "describe the change"`
3. Review the generated file in `migrations/versions/`
4. Run: `alembic upgrade head`

Without step 1, Alembic cannot see the table and will generate empty migrations.
This is the most common Alembic mistake.

After running `alembic revision --autogenerate` for ANY migration (schema or data), immediately
run, before reviewing the file's contents:
```bash
uv run ruff check migrations/ --fix
uv run ruff format migrations/
```
Alembic's autogenerated import order/spacing routinely fails ruff's I001 (import sorting) check
in CI. Fixing it immediately keeps the diff clean of formatting noise when reviewing the actual
migration logic, and avoids a CI failure on push.

Daily Alembic commands:
```bash
# Generate a migration after changing or adding a model
uv run alembic revision --autogenerate -m "description"

# Apply all pending migrations to the database
uv run alembic upgrade head

# Roll back the last applied migration
uv run alembic downgrade -1

# Show the current migration the database is on
uv run alembic current

# Verify models and DB are in sync (no pending changes)
uv run alembic check
```

## SKELETON STRUCTURE
.
├── .env
├── .env.example
├── .github/
│   ├── CODEOWNERS
│   ├── PULL_REQUEST_TEMPLATE.md
│   └── workflows/
│       └── ci.yml
├── .gitignore
├── CLAUDE.md
├── CONTRIBUTING.md
├── alembic.ini
├── app/
│   ├── __init__.py
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py
│   │   ├── dependencies.py
│   │   └── security.py
│   ├── database.py
│   ├── main.py
│   ├── models/
│   │   ├── __init__.py
│   │   └── user.py
│   ├── routers/
│   │   └── __init__.py
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── tenant_repository.py
│   │   ├── unit_repository.py
│   │   └── user_repository.py
│   ├── schemas/
│   │   ├── __init__.py
│   │   └── user.py
│   └── services/
│       ├── __init__.py
│       └── user_service.py
├── migrations/
│   └── versions/
├── pyproject.toml
├── README.md
└── tests/
    ├── __init__.py
    └── conftest.py