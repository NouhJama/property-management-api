# =============================================================================
# Section 1 — Imports
#
# Standard library and third-party imports required for the application entry
# point. Kept minimal: only what is used directly in this file belongs here.
# Router includes are added per-feature in later sections of the project.
# =============================================================================
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import settings
from app.database import engine
from app.routers import auth


# =============================================================================
# Section 2 — Lifespan context manager
#
# Controls startup and shutdown logic for the FastAPI application.
# Code before `yield` runs once on startup; code after `yield` runs once on
# shutdown. Using lifespan (rather than deprecated @app.on_event) is the
# SQLAlchemy 2.0 + FastAPI recommended pattern.
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application startup and shutdown lifecycle events."""
    # -- STARTUP ---------------------------------------------------------------
    # Verify the database is reachable before accepting any traffic.
    # A failed SELECT 1 raises immediately, preventing the server from starting
    # in a broken state. The exception propagates intentionally — do not catch.
    async with engine.begin() as conn:
        await conn.execute(text("SELECT 1"))

    print(
        f"[{settings.app_name} v{settings.app_version}] "
        "Startup complete — database connection verified."
    )

    # -- LIVE ------------------------------------------------------------------
    # The server is now live and handling requests.
    yield

    # -- SHUTDOWN --------------------------------------------------------------
    # Gracefully close all connections in the async connection pool.
    await engine.dispose()
    print(
        f"[{settings.app_name} v{settings.app_version}]"
        " Shutdown complete — connection pool disposed."
    )


# =============================================================================
# Section 3 — FastAPI app instance
#
# The central application object. All middleware, routers, and lifecycle hooks
# attach to this instance. `lifespan` wires in the startup/shutdown logic
# defined above.
# =============================================================================
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "Property management system for Damal Heights — built with async FastAPI and SQLAlchemy."
    ),
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    debug=settings.debug,
    lifespan=lifespan,
)


# =============================================================================
# Section 4 — CORS middleware
#
# Allows the API to be called from browser-based clients.
# WARNING: allow_origins=["*"] is intentionally permissive for local
# development. Before deploying to production, replace "*" with the real
# frontend domain (e.g. ["https://app.damalheights.com"]) to prevent
# unauthorised cross-origin access.
# =============================================================================
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Section 5 — Routers
#
# Mounts the auth router under /api/v1, producing /api/v1/auth/register,
# /api/v1/auth/login, and /api/v1/auth/me — matching the tokenUrl declared
# in app/core/dependencies.py.
# =============================================================================
app.include_router(auth.router, prefix="/api/v1")


# =============================================================================
# Section 6 — System endpoints
#
# Lightweight routes for monitoring and discovery. These are intentionally
# kept separate from the business-domain routers added later. Both are tagged
# "System" so they appear in their own group in the Swagger UI.
# =============================================================================
@app.get("/health", tags=["System"])
async def health_check() -> dict:
    """
    Return the liveness and database reachability status of the service.

    Performs a live async SELECT 1 against the database on every call so that
    monitoring systems can distinguish between a healthy server, a running
    server with a broken DB connection, and a completely unreachable service.
    """
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        db_status = "connected"
        db_error = None
    except Exception as exc:
        db_status = "unreachable"
        db_error = str(exc)

    payload: dict = {
        "status": "ok",
        "app": settings.app_name,
        "version": settings.app_version,
        "db_status": db_status,
    }
    if db_error:
        payload["db_error"] = db_error

    return payload


@app.get("/", tags=["System"])
async def root() -> dict:
    """
    Welcome endpoint — confirms the API is running and provides navigation links.

    Useful as a quick sanity check and as an entry point for API consumers
    who want to discover the available documentation URLs.
    """
    return {
        "message": f"Welcome to {settings.app_name}",
        "version": settings.app_version,
        "links": {
            "docs": "/docs",
            "health": "/health",
        },
    }
