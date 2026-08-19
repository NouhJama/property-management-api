"""
Tenant model — a real person renting a Unit through the company.

This is the data layer (Layer 4 of 4) in the four-layer architecture:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB schema)

The Tenant model maps to the `tenants` table in PostgreSQL. Unlike Owner,
there is no `type` field and no company-placeholder concept — every row
here is a genuine individual renter, never a stand-in for the developer.

There is also NO direct foreign key to Unit on this table. Which unit a
tenant currently rents is determined by an active rent-category Charge
(Charge.tenant_id + Charge.unit_id), not by a field here — this
deliberately avoids a relationship that could drift out of sync if it
were stored in two places. (Charge does not exist yet; the reasoning is
documented here so it isn't lost before that model is built.)

This file only defines the schema — no business logic, no HTTP concerns
live here.
"""

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Tenant(Base):
    """SQLAlchemy ORM model for the `tenants` table.

    Represents a real person renting a Unit through the company. Every row
    is a genuine individual renter — there is no company-placeholder row
    equivalent to Owner's type="company", and therefore no `type` column
    at all. The unit a tenant occupies is resolved through an active
    rent-category Charge rather than a column on this table.
    """

    __tablename__ = "tenants"

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
    # The tenant's full legal name. String(255) matches the length
    # convention used for names elsewhere.
    # -------------------------------------------------------------------------
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    # -------------------------------------------------------------------------
    # phone
    # Optional contact phone number. String(20) covers international formats
    # with country code and separators.
    # Stored as a plain str at the model/DB level, exactly like Owner.phone —
    # validation and E.164 normalization happen at the Pydantic schema layer
    # via the existing DamalPhoneNumber type (defined in app/schemas/owner.py),
    # which is reused rather than rebuilt.
    # -------------------------------------------------------------------------
    phone: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)

    # -------------------------------------------------------------------------
    # email
    # Optional contact email address. String(255) matches the practical
    # maximum length for a valid email address per RFC 5321.
    # Deliberately NOT unique — same reasoning as Owner.email: a family
    # renting together could plausibly share a single email address.
    # -------------------------------------------------------------------------
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # -------------------------------------------------------------------------
    # national_id
    # Government-issued identification number for the tenant. Nullable
    # because it isn't always collected at the time the record is created.
    # -------------------------------------------------------------------------
    national_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)

    # -------------------------------------------------------------------------
    # created_by
    # Nullable audit trail ONLY — tracks which staff member (User) created
    # this Tenant record. Identical pattern to Owner.created_by and
    # Unit.created_by.
    # NOT an ownership or ORM relationship() — just a plain foreign key
    # column. Querying the actual User requires a separate lookup via
    # UserRepository, not automatic loading.
    # ondelete="SET NULL": if the referencing User account is ever deleted,
    # this field clears to NULL rather than blocking the User deletion or
    # cascading to delete the Tenant.
    # -------------------------------------------------------------------------
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
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
        return f"<Tenant id={self.id} name={self.name}>"
