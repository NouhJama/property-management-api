"""
User model — the identity and authentication foundation of the application.

This is the data layer (Layer 4 of 4) in the four-layer architecture:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB schema)

The User model maps to the `users` table in PostgreSQL. Every other resource
in this system (properties, tenants, leases, payments) will reference User
for ownership, audit trails, and access control.

This file only defines the schema — no business logic, no password hashing,
no HTTP concerns live here.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Boolean, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class User(Base):
    """SQLAlchemy ORM model for the `users` table."""

    __tablename__ = "users"

    # -------------------------------------------------------------------------
    # id
    # Auto-incremented integer primary key. SQLAlchemy sets this on INSERT;
    # we never assign it manually. Every row in the table has a unique id.
    # Mapped[int] tells SQLAlchemy that this column is an integer, and
    # mapped_column(primary_key=True) marks it as the primary key.
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(primary_key=True)

    # -------------------------------------------------------------------------
    # email
    # The user's login identifier — must be unique across the entire table.
    # index=True adds a B-tree index so lookups by email (e.g. during login)
    # are O(log n) instead of a full table scan. String(255) matches the
    # practical maximum length for a valid email address per RFC 5321.
    # -------------------------------------------------------------------------
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)

    # -------------------------------------------------------------------------
    # hashed_password
    # Stores only the bcrypt hash of the user's password — NEVER the plain text.
    # Hashing is performed in the service layer before this value is written.
    # String(255) is sufficient for a bcrypt hash (typically 60 characters).
    # -------------------------------------------------------------------------
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    # -------------------------------------------------------------------------
    # full_name
    # Optional display name shown in the UI and on documents.
    # Not required at registration — users can set it later.
    # -------------------------------------------------------------------------
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # -------------------------------------------------------------------------
    # is_active
    # Controls whether this account is enabled.
    # False disables the account without deleting the row — useful for
    # suspending users while preserving their data and relationships.
    # Defaults to True so new sign-ups are immediately usable.
    # -------------------------------------------------------------------------
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # -------------------------------------------------------------------------
    # is_superuser
    # Grants full administrative access when True.
    # Defaults to False for all regular users — superuser status is only
    # assigned manually to admin accounts, never via the public API.
    # -------------------------------------------------------------------------
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # -------------------------------------------------------------------------
    # created_at
    # The exact UTC timestamp when this row was first inserted.
    # Set automatically on INSERT via the lambda default — never updated after
    # that. Using datetime.now(timezone.utc) instead of the deprecated
    # datetime.utcnow() which was removed in Python 3.12+.
    # DateTime(timezone=True) tells PostgreSQL to use TIMESTAMPTZ so the
    # timezone offset is preserved in the database.
    # Timezone is East African Time (EAT) for this application, but we store UTC in the DB
    # for consistency.
    # -------------------------------------------------------------------------
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # updated_at
    # The UTC timestamp of the most recent UPDATE to this row.
    # Starts as None (NULL) — intentional: a NULL here means the row has never
    # been updated since creation, which is meaningful information.
    # The onupdate lambda fires automatically on every UPDATE statement,
    # keeping this column current without any manual bookkeeping.
    # -------------------------------------------------------------------------
    updated_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        default=None,
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=True,
    )

    def __repr__(self) -> str:
        """Return a readable string representation for debugging and logs."""
        return f"<User id={self.id} email={self.email}>"
