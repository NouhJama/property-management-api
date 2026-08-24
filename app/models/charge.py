"""
Charge model — one monthly bill (an invoice) raised against a Unit.

This is the data layer (Layer 4 of 4) in the four-layer architecture:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB schema)

The Charge model maps to the `charges` table in PostgreSQL. A row here is a
single billing event for a single billing period — NOT a standing agreement.
A new row is created each period, which is what preserves financial history:
water charges in particular vary month to month with the actual meter
reading, so a single mutable "standing" row would overwrite the record of
what was actually billed.

Two parties can be billed, never both on the same row:
  rent, water, move_out  → owed by the TENANT
  service_charge         → owed by the OWNER

Deliberately absent from this domain: electricity and garbage. Electricity is
never billed by the company — KPLC prepaid token meters mean the occupant
tops up directly with KPLC. Garbage is not a line item either; it is one of
the things the owner's service charge FUNDS, alongside common-area cleaning,
security guards, the backup generator, management staff salaries, KRA tax and
the KPLC meter tax.

This file only defines the schema — no business logic, no HTTP concerns
live here.
"""

import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Numeric
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ChargeCategory(str, enum.Enum):
    """What this bill is for — and, by implication, WHO owes it.

    Inherits from both str and Enum so each member is simultaneously a real
    string — same reasoning as UnitType/UnitStatus/OwnerType — which makes
    JSON serialization (via Pydantic) and equality comparisons work
    naturally without extra conversion code in schemas.

    Which party owes each category:
      RENT           → owed by the TENANT (fixed monthly amount)
      WATER          → owed by the TENANT (variable, from a meter reading)
      MOVE_OUT       → owed by the TENANT (one-off on exit — painting,
                       cleaning, repairs)
      SERVICE_CHARGE → owed by the OWNER (a percentage of the agreed rent)

    This pairing is what determines whether owner_id or tenant_id is
    populated on a given row.
    """

    RENT = "rent"
    WATER = "water"
    SERVICE_CHARGE = "service_charge"
    MOVE_OUT = "move_out"


class Charge(Base):
    """SQLAlchemy ORM model for the `charges` table.

    One row = ONE MONTHLY BILL for one unit, not a standing agreement. New
    rows are created each billing period rather than updating an existing
    row, so the amount actually billed in any past month stays recoverable.

    Exactly ONE of owner_id/tenant_id is populated per row, determined by
    `category` (see ChargeCategory). That rule is enforced at the Pydantic
    schema layer (schemas/charge.py's model_validator), NOT as a database
    constraint — the same approach already used for Unit's
    bedrooms/unit_type rule, keeping conditional business rules in one
    reviewable place instead of split across Python and DDL.

    Payment status (paid/partially_paid/unpaid) is deliberately NOT stored
    here. It will be DERIVED from Payment records once Payment exists, so it
    can never drift out of sync with the money actually received — a stored
    status column would need updating on every payment and would silently
    lie the moment one update was missed. Only `is_cancelled` is stored,
    because cancelling is a deliberate human decision that no calculation
    over Payment rows could ever infer.
    """

    __tablename__ = "charges"

    # -------------------------------------------------------------------------
    # id
    # Auto-incremented integer primary key. SQLAlchemy sets this on INSERT;
    # we never assign it manually. Every row in the table has a unique id.
    # Mapped[int] tells SQLAlchemy that this column is an integer, and
    # mapped_column(primary_key=True) marks it as the primary key.
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(primary_key=True)

    # -------------------------------------------------------------------------
    # unit_id
    # REQUIRED — every charge is raised against a specific unit; a bill that
    # belongs to no unit is meaningless.
    # No ondelete specified, so PostgreSQL falls back to NO ACTION
    # (effectively RESTRICT): it will BLOCK deleting a Unit that still has
    # billing history attached. Financial records are essential data, not
    # audit trivia — they must never be silently discarded or orphaned. This
    # is deliberately stricter than created_by's SET NULL below, and matches
    # the same reasoning already applied to Unit.owner_id.
    # -------------------------------------------------------------------------
    unit_id: Mapped[int] = mapped_column(
        ForeignKey("units.id"),
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # owner_id
    # Populated ONLY for category=SERVICE_CHARGE — the one category an owner
    # owes.
    # Nullable at the database level because rent/water/move_out charges
    # legitimately have no owner attached; the category/party pairing is
    # enforced in the schema layer, not here.
    # No ondelete → RESTRICT, same reasoning as unit_id: an Owner with
    # billing history cannot be deleted out from under it.
    # -------------------------------------------------------------------------
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("owners.id"),
        nullable=True,
    )

    # -------------------------------------------------------------------------
    # tenant_id
    # Populated ONLY for RENT/WATER/MOVE_OUT — the categories a tenant owes.
    # Same nullable + RESTRICT reasoning as owner_id directly above: nullable
    # because service_charge rows have no tenant, and RESTRICT because a
    # Tenant with billing history must not be deletable.
    # -------------------------------------------------------------------------
    tenant_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tenants.id"),
        nullable=True,
    )

    # -------------------------------------------------------------------------
    # category
    # What this bill is for, and therefore which party owes it.
    # SQLEnum(ChargeCategory) maps to a native PostgreSQL enum — invalid
    # values are rejected at the database level, not just in Python.
    # -------------------------------------------------------------------------
    category: Mapped[ChargeCategory] = mapped_column(
        SQLEnum(ChargeCategory),
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # amount
    # The money owed, in Numeric — NOT Float. Binary floats cannot represent
    # decimal fractions exactly (0.1 + 0.2 != 0.3), and those tiny errors
    # accumulate across many rows into real accounting discrepancies.
    # Numeric(12, 2) stores exact decimal values up to 9,999,999,999.99 —
    # well beyond any realistic charge — with exactly two decimal places.
    # The amount is frozen at creation and never recalculated, so historical
    # records stay accurate even if rates change later.
    # -------------------------------------------------------------------------
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # percentage
    # Set ONLY for SERVICE_CHARGE, recording the RULE that produced the
    # amount (e.g. 10.00 for 10% of the agreed rent).
    # Stored ALONGSIDE the computed amount rather than instead of it: the
    # amount stays frozen for historical accuracy, while the percentage
    # documents how it was derived, for auditing. Recomputing from the
    # percentage on read would reintroduce exactly the drift that freezing
    # the amount avoids.
    # Numeric(5, 2) handles up to 999.99.
    # -------------------------------------------------------------------------
    percentage: Mapped[Optional[Decimal]] = mapped_column(
        Numeric(5, 2),
        nullable=True,
    )

    # -------------------------------------------------------------------------
    # period
    # The first day of the billing month this charge covers — e.g.
    # 2026-03-01 for March 2026. Always normalized to the first of the month;
    # the day component carries no meaning of its own.
    # A plain Date rather than separate year/month integers: simpler to sort,
    # filter, and range-query (BETWEEN two dates) without composing two
    # columns in every WHERE clause.
    # -------------------------------------------------------------------------
    period: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # is_cancelled
    # The ONLY stored status field. A charge can be voided — a billing error,
    # a waived fee — regardless of what payments were received against it.
    # That is a genuine human decision no calculation could derive, which is
    # exactly why it is stored.
    # Paid/partially_paid/unpaid are NOT stored here; they are computed from
    # Payment records so they can never drift out of sync with the actual
    # money received.
    # -------------------------------------------------------------------------
    is_cancelled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # created_by
    # Nullable audit trail ONLY — tracks which staff member (User) raised
    # this Charge. Identical pattern to Owner/Unit/Tenant.created_by.
    # NOT an ownership or ORM relationship() — just a plain foreign key
    # column. Querying the actual User requires a separate lookup via
    # UserRepository, not automatic loading.
    # ondelete="SET NULL": if the referencing User account is ever deleted,
    # this field clears to NULL rather than blocking the User deletion or
    # cascading to delete the Charge. Note the contrast with unit_id /
    # owner_id / tenant_id above, which are essential financial data and
    # therefore RESTRICT instead.
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
        return (
            f"<Charge id={self.id} unit_id={self.unit_id} "
            f"category={self.category} amount={self.amount} period={self.period}>"
        )
