"""
Bootstrap script — create the first admin (superuser) account.

The normal registration API deliberately hardcodes is_superuser=False:
UserCreate has no is_superuser field for a client to send, and the service
never sets it. There is therefore NO client-facing way to create an admin.
This script is the ONLY way an admin account is ever created — run once per
environment by someone with direct server or deployment access.

Credentials come from ADMIN_EMAIL and ADMIN_PASSWORD in .env (via
app.core.config). The password is hashed with the SAME hash_password()
helper the normal registration flow uses — there is no separate or weaker
hashing path for this script — and is never printed, logged, or echoed
anywhere, in plain or hashed form.

Safe to run multiple times: if a user with the configured admin email
already exists, the script reports that and exits without creating anything.

This is a SCRIPT, not a migration, so it is allowed to import live
application code (UserRepository, hash_password, settings) directly —
unlike migrations, which must stay frozen to their historical schema.

Usage:
    uv run python scripts/create_admin.py
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
import asyncio
import logging
import sys
from pathlib import Path

# Running a file inside scripts/ puts THAT directory on sys.path, not the
# project root — and this project has no [build-system], so `app` is not
# installed as an importable package. Without this, `import app.*` below
# raises ModuleNotFoundError. Adding the project root explicitly keeps the
# documented `uv run python scripts/create_admin.py` invocation working from
# anywhere. The app imports must therefore come after this line, hence the
# E402 suppressions.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.config import settings  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database import AsyncSessionLocal, engine  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

# =============================================================================
# SECTION 2 — Silence SQL echo
# =============================================================================
# The shared engine is built with echo=settings.debug, so with DEBUG=True in
# .env SQLAlchemy logs every statement WITH its bound parameters — which for
# the INSERT below includes the bcrypt hash of the admin password. This script
# must never emit the password in any form, so echo is forced off here
# regardless of the DEBUG setting.
#
# Setting the logger level is NOT sufficient: echo=True wraps the logger in an
# InstanceLogger whose echo flag short-circuits the level check, so log records
# are emitted even at WARNING. Clearing echo on the underlying sync engine is
# what actually turns the statement logging off. Belt and braces, the logger
# level is lowered too, in case DEBUG is off but the root logger is verbose.
#
# This mutates the engine only inside this short-lived script process — the
# running application imports its own fresh engine and is unaffected.
engine.sync_engine.echo = False
logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


# =============================================================================
# SECTION 3 — create_admin
# =============================================================================
async def create_admin() -> None:
    """
    Create the configured admin account, unless it already exists.

    Idempotent: a second run finds the existing user and returns without
    touching the database.
    """
    # Step 1 — Open a session directly.
    #
    # This script runs outside any HTTP request, so there is no get_db()
    # dependency chain to hang off — AsyncSessionLocal is used directly, the
    # same pattern as the standalone async verification scripts run elsewhere
    # in this project. The async context manager closes the session on exit,
    # including if an exception propagates.
    async with AsyncSessionLocal() as session:
        repo = UserRepository(session)

        # Step 2 — Check whether this specific account already exists.
        #
        # This checks for the CONFIGURED ADMIN EMAIL specifically, not
        # "does any superuser exist globally". That is a deliberate
        # simplification: UserRepository has no get_by_superuser_status
        # method, and checking by email is what makes re-running safe.
        # Consequence: changing ADMIN_EMAIL in .env and re-running will
        # create a SECOND admin rather than reporting one already exists.
        existing = await repo.get_by_email(settings.admin_email)

        # Step 3 — Already present: do nothing.
        if existing:
            print("A user with this email already exists. Skipping admin creation.")
            return

        # Step 4 — Create the admin.
        #
        # hash_password() is the same helper the registration flow uses, so
        # this account's hash is indistinguishable from any other user's.
        hashed = hash_password(settings.admin_password)

        # is_superuser=True is passed explicitly here — this is the ONE
        # legitimate place in the entire codebase where that happens outside
        # of a direct database/admin action.
        admin = await repo.create(
            email=settings.admin_email,
            hashed_password=hashed,
            full_name="System Administrator",
            is_superuser=True,
        )

        # Only non-sensitive identifiers are printed. The password — plain or
        # hashed — is never included in any output, including error messages.
        print(f"Admin created successfully: {admin.email} (id={admin.id})")


# =============================================================================
# SECTION 4 — Entry point
# =============================================================================
if __name__ == "__main__":
    asyncio.run(create_admin())
