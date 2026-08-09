"""
Shared pytest fixtures for the test suite.

Provides a test-database session (db_session) and an async HTTP client
(client) wired to the FastAPI app with get_db overridden to use the test
database instead of the real one — every request made through `client`
during a test hits test_property_db, never property_db.

Also provides two authentication fixtures — staff_headers (a regular user)
and admin_headers (a superuser) — each returning a ready-to-use
Authorization header dict for tests that exercise permission-gated routes.

The test database URL is resolved from the DATABASE_URL environment variable
when it is set (CI) and falls back to local development credentials when it
is not — see SECTION 2 for the full explanation.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================

import os

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

# =============================================================================
# SECTION 2 — Test database URL
# =============================================================================
# Points at the test database, never property_db — resolved per environment.
#
# In CI, ci.yml's `env:` block sets DATABASE_URL to point at CI's own
# disposable PostgreSQL service container, whose testuser/testpassword
# credentials are created fresh for that single CI run and thrown away with
# the container afterwards.
#
# Locally, DATABASE_URL is NOT set as an environment variable, so os.getenv
# falls through to the second argument — the real damal/damal123 credentials
# already used by the app's development database.
#
# The same test code therefore reaches the correct database in both
# environments with no manual setup: a developer never needs to create extra
# PostgreSQL roles on their own machine. testuser exists ONLY inside CI's
# disposable container and is never something a local machine should have to
# configure.
TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://damal:damal123@localhost:5432/test_property_db",
)


# =============================================================================
# SECTION 3 — db_session fixture
# =============================================================================
@pytest_asyncio.fixture
async def db_session():
    """
    Yield an AsyncSession bound to a freshly created test-database schema.

    create_all builds every table from Base.metadata before the test runs;
    drop_all tears the schema back down after — every test starts from a
    clean, empty database and leaves no state behind for the next one.
    """
    engine = create_async_engine(TEST_DATABASE_URL)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)

    await engine.dispose()


# =============================================================================
# SECTION 4 — client fixture
# =============================================================================
# Depends on db_session (named as a parameter) — pytest runs db_session
# first, then this fixture, so every request the client makes shares the
# same test-database session.
@pytest_asyncio.fixture
async def client(db_session: AsyncSession):
    """
    Yield an httpx.AsyncClient wired to the FastAPI app in-memory.

    Overrides get_db so every route handler receives db_session instead of
    a real request-scoped session — swaps the real DB for the test DB
    across the whole app for the duration of the test.
    """

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    # ASGITransport sends requests directly into the app in memory —
    # no network socket, no running server required.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as test_client:
        yield test_client

    app.dependency_overrides.clear()


# =============================================================================
# SECTION 5 — staff_headers fixture
# =============================================================================
@pytest_asyncio.fixture
async def staff_headers(client: AsyncClient):
    """
    Register and log in a regular (non-admin) user, returning an auth header.

    Goes through the real /register and /login endpoints — nothing is faked —
    so the token is genuine and fully verifiable. Used by tests that need an
    authenticated but NON-privileged caller: confirming admin-only endpoints
    correctly reject ordinary staff with 403, and that staff-readable
    endpoints correctly let them through.

    Returns:
        A dict ready to pass as headers=... on any client request.
    """
    credentials = {
        "email": "test-staff@example.com",
        "password": "StaffTestPass123!",
        "full_name": "Test Staff",
    }
    await client.post("/api/v1/auth/register", json=credentials)

    # OAuth2PasswordRequestForm is form data, not JSON — the field is
    # "username" even though we log in with an email.
    login_response = await client.post(
        "/api/v1/auth/login",
        data={"username": credentials["email"], "password": credentials["password"]},
    )
    token = login_response.json()["access_token"]

    return {"Authorization": f"Bearer {token}"}


# =============================================================================
# SECTION 6 — admin_headers fixture
# =============================================================================
@pytest_asyncio.fixture
async def admin_headers(client: AsyncClient, db_session: AsyncSession):
    """
    Create a superuser directly via UserRepository, then log in for a token.

    The admin row is inserted through the repository, bypassing the API
    entirely — mirroring exactly how scripts/create_admin.py works. This is
    necessary, not a shortcut: the public /register endpoint can never
    produce an admin, because UserService.create_user hardcodes
    is_superuser=False.

    Only the account creation is bypassed. The token itself is obtained
    through the real /login endpoint, so it is a genuine, fully-verified JWT.
    Used by tests confirming admin-only endpoints work when the caller IS an
    admin.

    Returns:
        A dict ready to pass as headers=... on any client request.
    """
    from app.core.security import hash_password
    from app.repositories.user_repository import UserRepository

    email = "test-admin@example.com"
    password = "AdminTestPass123!"

    repo = UserRepository(db_session)
    await repo.create(
        email=email,
        hashed_password=hash_password(password),
        full_name="Test Admin",
        is_superuser=True,
    )

    login_response = await client.post(
        "/api/v1/auth/login",
        data={"username": email, "password": password},
    )
    token = login_response.json()["access_token"]

    return {"Authorization": f"Bearer {token}"}
