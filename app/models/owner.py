"""
Owner model — anyone who can legally own a Unit.

This is the data layer (Layer 4 of 4) in the four-layer architecture:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB schema)

The Owner model maps to the `owners` table in PostgreSQL. An owner is either
a real individual who purchased a unit, or a single special row representing
the property developer itself (Damal Heights), used as the default owner for
units that haven't been sold yet. Every Unit.owner_id must point to a row
here — never null.

This file only defines the schema — no business logic, no HTTP concerns
live here.
"""

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class OwnerType(str, enum.Enum):
    """Whether this owner is a real individual or the developer company row.

    Inherits from both str and Enum so each member is simultaneously a real
    string — same reasoning as UnitType/UnitStatus — which makes JSON
    serialization (via Pydantic later) and equality comparisons work
    naturally without extra conversion code in schemas.
    """

    INDIVIDUAL = "individual"
    COMPANY = "company"


class Owner(Base):
    """SQLAlchemy ORM model for the `owners` table.

    Represents anyone who can legally own a Unit — either a real individual
    who purchased it, or a single special row representing the property
    developer itself (Damal Heights), used as the default owner for units
    that haven't been sold yet. Every Unit.owner_id must point to a row
    here — never null.
    """

    __tablename__ = "owners"

    # -------------------------------------------------------------------------
    # id
    # Auto-incremented integer primary key. SQLAlchemy sets this on INSERT;
    # we never assign it manually. Every row in the table has a unique id.
    # Mapped[int] tells SQLAlchemy that this column is an integer, and
    # mapped_column(primary_key=True) marks it as the primary key.
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(primary_key=True)

    # -------------------------------------------------------------------------
    # name
    # The owner's full legal name (individual) or company name (Damal Heights).
    # String(255) matches the length convention used for names elsewhere.
    # -------------------------------------------------------------------------
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # -------------------------------------------------------------------------
    # phone
    # Optional contact phone number. String(20) covers international formats
    # with country code and separators.
    # -------------------------------------------------------------------------
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # -------------------------------------------------------------------------
    # email
    # Optional contact email address. String(255) matches the practical
    # maximum length for a valid email address per RFC 5321.
    # -------------------------------------------------------------------------
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # -------------------------------------------------------------------------
    # national_id
    # Government-issued identification number for individual owners.
    # Nullable because the company placeholder row (type="company") has no
    # national ID.
    # -------------------------------------------------------------------------
    national_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # -------------------------------------------------------------------------
    # type
    # Distinguishes real individual owners from the single company row
    # representing Damal Heights itself. SQLEnum(OwnerType) maps to a native
    # PostgreSQL enum — invalid values are rejected at the database level,
    # not just in Python.
    # -------------------------------------------------------------------------
    type: Mapped[OwnerType] = mapped_column(SQLEnum(OwnerType), nullable=False)

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
        return f"<Owner id={self.id} name={self.name} type={self.type}>"
