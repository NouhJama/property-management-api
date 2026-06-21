from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings

# ---------------------------------------------------------------------------
# ASYNC ENGINE
#
# The engine manages the connection pool to PostgreSQL via the asyncpg driver.
# echo mirrors settings.debug so SQL statements are only logged in debug mode.
# pool_pre_ping checks each connection before use so dropped/stale connections
# (e.g. after a DB restart) are transparently replaced instead of raising.
# ---------------------------------------------------------------------------
engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
)

# ---------------------------------------------------------------------------
# ASYNC SESSION FACTORY
#
# AsyncSessionLocal produces new AsyncSession instances bound to the engine.
# expire_on_commit=False keeps loaded attributes accessible after commit,
# which matters since FastAPI may serialize a response after the session's
# transaction has already been committed. autoflush=False keeps flushing
# under explicit/manual control rather than implicit, query-triggered flushes.
# ---------------------------------------------------------------------------
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    expire_on_commit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# BASE CLASS
#
# All ORM models inherit from this SQLAlchemy 2.0-style declarative base.
# ---------------------------------------------------------------------------
class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# get_db DEPENDENCY
#
# FastAPI dependency that yields a request-scoped AsyncSession. The async
# context manager closes the session automatically once the request handler
# (and the code after `yield`) finishes, including on exceptions.
# ---------------------------------------------------------------------------
async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
