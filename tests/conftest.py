"""
Shared pytest fixtures for the test suite.

Provides a test-database session (db_session) and an async HTTP client
(client) wired to the FastAPI app with get_db overridden to use the test
database instead of the real one — every request made through `client`
during a test hits test_property_db, never property_db.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.main import app

# =============================================================================
# SECTION 2 — Test database URL
# =============================================================================
# Points at the local test database, never property_db.
TEST_DATABASE_URL = "postgresql+asyncpg://damal:damal123@localhost:5432/test_property_db"


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
