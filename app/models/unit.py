"""
Unit model — a physical unit in the building.

This is the data layer (Layer 4 of 4) in the four-layer architecture:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB schema)

The Unit model maps to the `units` table in PostgreSQL. Every unit type
(shop, office, restaurant, lodge, apartment) uses this one table,
distinguished by the `unit_type` column. Tenants will later reference
units for occupancy, and rent/renovation records will link back here.

This file only defines the schema — no business logic, no HTTP concerns
live here.
"""

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class UnitType(str, enum.Enum):
    """The kind of unit — determines which columns are meaningful (e.g. bedrooms).

    Inherits from both str and Enum so each member is simultaneously a real
    string, which makes JSON serialization (via Pydantic later) and equality
    comparisons work naturally without extra conversion code.
    """

    SHOP = "shop"
    OFFICE = "office"
    RESTAURANT = "restaurant"
    LODGE = "lodge"
    APARTMENT = "apartment"


class UnitStatus(str, enum.Enum):
    """The current occupancy state of a unit.

    AVAILABLE means "nobody currently assigned" — deliberately does not
    distinguish "never occupied" from "previously occupied, now empty",
    since that history is instead derivable later from querying Tenant
    records linked to this unit (count/exist queries), not stored
    redundantly here.
    """

    AVAILABLE = "available"
    OWNER_OCCUPIED = "owner_occupied"
    TENANT_OCCUPIED = "tenant_occupied"
    UNDER_MAINTENANCE = "under_maintenance"


class Unit(Base):
    """SQLAlchemy ORM model for the `units` table."""

    __tablename__ = "units"

    # -------------------------------------------------------------------------
    # id
    # Auto-incremented integer primary key. SQLAlchemy sets this on INSERT;
    # we never assign it manually. Every row in the table has a unique id.
    # Mapped[int] tells SQLAlchemy that this column is an integer, and
    # mapped_column(primary_key=True) marks it as the primary key.
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(primary_key=True)

    # -------------------------------------------------------------------------
    # unit_number
    # Human-readable identifier for the unit — unique across the ENTIRE
    # building, not just per floor (e.g. "B-01", "G-03", "5-2A").
    # index=True adds a B-tree index so lookups by unit number are fast.
    # -------------------------------------------------------------------------
    unit_number: Mapped[str] = mapped_column(String(50), unique=True, index=True, nullable=False)

    # -------------------------------------------------------------------------
    # floor
    # Which floor the unit is on: basement=-1, ground=0, 1st=1, up to 21.
    # Signed integer to allow the basement level.
    # -------------------------------------------------------------------------
    floor: Mapped[int] = mapped_column(Integer, nullable=False)

    # -------------------------------------------------------------------------
    # unit_type
    # What kind of unit this is (shop, office, restaurant, lodge, apartment).
    # SQLEnum(UnitType) maps to a native PostgreSQL enum — invalid values
    # are rejected at the database level, not just in Python.
    # -------------------------------------------------------------------------
    unit_type: Mapped[UnitType] = mapped_column(SQLEnum(UnitType), nullable=False)

    # -------------------------------------------------------------------------
    # bedrooms
    # Number of bedrooms: null = concept doesn't apply (shop/office/restaurant),
    # 0 = studio-like (lodge), 2 or 3 = apartments. null and 0 are deliberately
    # different — null means "not applicable", 0 means "applicable, and the
    # answer is zero".
    # -------------------------------------------------------------------------
    bedrooms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # -------------------------------------------------------------------------
    # size
    # Square footage of the unit, optional.
    # -------------------------------------------------------------------------
    size: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # -------------------------------------------------------------------------
    # status
    # Current occupancy state. New units default to available until assigned.
    # SQLEnum(UnitStatus) is a native PostgreSQL enum, same as unit_type.
    # -------------------------------------------------------------------------
    status: Mapped[UnitStatus] = mapped_column(
        SQLEnum(UnitStatus),
        default=UnitStatus.AVAILABLE,
        nullable=False,
    )

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
        return f"<Unit id={self.id} unit_number={self.unit_number} type={self.unit_type}>"
