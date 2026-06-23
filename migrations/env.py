# =============================================================================
# SECTION 1 — Standard imports
#
# asyncio        — needed to run the async migration function from a sync
#                  entry point (asyncio.run in run_migrations_online).
# fileConfig     — reads the [loggers]/[handlers]/[formatters] blocks from
#                  alembic.ini and configures Python's logging system.
# pool           — provides NullPool, which disables connection pooling for
#                  the one-off migration connection (different from the app pool).
# Connection     — type hint for the synchronous connection passed to
#                  do_run_migrations by run_sync().
# async_engine_from_config — async equivalent of engine_from_config; creates
#                  an async engine from the alembic config dict.
# context        — the Alembic runtime object; controls offline/online mode,
#                  metadata binding, and migration execution.
# =============================================================================
import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# =============================================================================
# SECTION 2 — Alembic config object
#
# context.config wraps alembic.ini so we can read settings from it in Python.
# fileConfig sets up Python logging exactly as defined in alembic.ini's
# [loggers], [handlers], and [formatters] sections — this is what makes
# Alembic print INFO-level progress messages during migrations.
# The guard (config_file_name is not None) allows env.py to be imported
# in tests or programmatically without a physical alembic.ini present.
# =============================================================================
config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# =============================================================================
# SECTION 3 — Import Base and settings
#
# These imports come after logging setup, which is why they appear below the
# standard library / third-party imports. The noqa: E402 comments suppress
# the "module level import not at top of file" warning from ruff/flake8.
#
# settings  — pydantic-settings object that reads from .env; provides
#             settings.database_url used in get_url() below.
# Base      — SQLAlchemy DeclarativeBase from app/database.py; its .metadata
#             attribute knows about every table defined on models that inherit
#             from it — but ONLY if those model files have been imported below.
# =============================================================================
from app.core.config import settings  # noqa: E402
from app.database import Base  # noqa: E402

# ── Model imports ──────────────────────────────────────────────────────────────
# IMPORTANT: import every model here as you create it.
# Without these imports Base.metadata cannot see the tables and Alembic will
# generate empty migrations even though your model files exist.
#
# Uncomment each line as you create the corresponding model file:
from app.models.user import User  # noqa: E402, F401

# from app.models.property import Property
# from app.models.tenant import Tenant
# from app.models.lease import Lease
# from app.models.payment import Payment
# ──────────────────────────────────────────────────────────────────────────────

# target_metadata tells Alembic which tables to compare against the live DB
# when autogenerating migrations. It must reflect all imported models.
target_metadata = Base.metadata


# =============================================================================
# SECTION 4 — get_url() helper
#
# Returns the database URL as a string from pydantic-settings (which reads it
# from .env). Centralising the URL here means it never needs to be duplicated
# in alembic.ini — the single source of truth is always the .env file.
# =============================================================================
def get_url() -> str:
    """Return the database URL from application settings."""
    return str(settings.database_url)


# =============================================================================
# SECTION 5 — run_migrations_offline()
#
# Offline mode generates raw SQL migration scripts without opening a live
# database connection. Useful when you want to review the exact SQL before
# applying it to a production database, or when the DB is not accessible from
# the machine running Alembic (e.g. generating scripts in CI to apply later).
# Triggered by: alembic upgrade head --sql
# =============================================================================
def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without a live DB."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


# =============================================================================
# SECTION 6 — do_run_migrations(connection)
#
# This function is always synchronous — even in async mode. Alembic's internal
# migration execution engine is fundamentally sync: it generates and runs SQL
# statements using a regular DBAPI connection. The async engine hands us a
# synchronous Connection object via run_sync(), which is the bridge between
# the async world (asyncpg) and the sync world (Alembic internals).
# =============================================================================
def do_run_migrations(connection: Connection) -> None:
    """Configure context with a live connection and run all pending migrations."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


# =============================================================================
# SECTION 7 — run_async_migrations()
#
# This is the async core of the migration runner.
#
# async_engine_from_config reads the sqlalchemy.* keys from the alembic config
# section (we inject sqlalchemy.url below) and creates an AsyncEngine.
#
# NullPool disables connection pooling entirely — migrations are a one-off
# operation and don't benefit from a persistent pool the way the app does.
# Using NullPool also avoids "event loop closed" errors that can occur when
# an engine with a pool outlives the asyncio event loop started by asyncio.run().
#
# connection.run_sync(do_run_migrations) is the key adapter: it takes a sync
# callable, opens a synchronous-view Connection from the async connection, and
# runs the callable inside it — allowing Alembic's sync internals to execute
# over an asyncpg connection transparently.
#
# connectable.dispose() closes all connections cleanly after the migration
# completes, preventing "connection was not closed" warnings.
# =============================================================================
async def run_async_migrations() -> None:
    """Create an async engine and run migrations through a sync adapter."""
    alembic_config_section = config.get_section(config.config_ini_section, {})
    alembic_config_section["sqlalchemy.url"] = get_url()

    connectable = async_engine_from_config(
        alembic_config_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


# =============================================================================
# SECTION 8 — run_migrations_online()
#
# Alembic calls this file as a regular Python script (not an async context),
# so it needs a synchronous entry point. asyncio.run() starts a fresh event
# loop, runs run_async_migrations() to completion, then closes the loop.
# This is the standard pattern for running async code from a sync entry point.
# =============================================================================
def run_migrations_online() -> None:
    """Sync entry point — starts the event loop for the async migration runner."""
    asyncio.run(run_async_migrations())


# =============================================================================
# SECTION 9 — Entry point
#
# Alembic sets context.is_offline_mode() based on whether --sql was passed.
# Online mode (default) connects to the live database and applies migrations.
# Offline mode generates SQL scripts for manual review or deferred application.
# =============================================================================
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
