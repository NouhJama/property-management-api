# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Purpose

A property management API that manages properties, units, tenants, and employees, and tracks rent and renovations. Currently in early scaffolding stage — no application code beyond the entry point exists yet.

## Toolchain

- This project uses **uv** for Python package management (Python 3.13).
- Database management is handled with **SQLAlchemy 2.0 (async)** and **Alembic** for migrations. The API will be built using **FastAPI**(Always use the official documentation).
- Database: **PostgreSQL**, driver: **asyncpg**.
- All database access is **asynchronous, end to end**: `create_async_engine`, `async_sessionmaker`, `AsyncSession`, and `DeclarativeBase` (SQLAlchemy 2.0 style). Do not introduce sync `create_engine()`/`sessionmaker()` code — the asyncpg driver only works with the async engine.
- Validation: **Pydantic V2 models** for data validation and settings management.

## Authentication and security
- Authentication: **JWT**(OAuth2 password flow).
- Password hashing: **bcrypt** for secure password storage.
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
│   │   └── __init__.py
│   ├── routers/
│   │   └── __init__.py
│   ├── repositories/
│   │   ├── __init__.py
│   │   ├── tenant_repository.py
│   │   ├── unit_repository.py
│   │   └── user_repository.py
│   ├── schemas/
│   │   └── __init__.py
│   └── services/
│       └── __init__.py
├── migrations/
│   └── versions/
├── pyproject.toml
├── README.md
└── tests/
    ├── __init__.py
    └── conftest.py