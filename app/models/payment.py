"""
Payment model — money actually received against a single Charge.

This is the data layer (Layer 4 of 4) in the four-layer architecture:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB schema)

The Payment model maps to the `payments` table in PostgreSQL. A row here is a
record of money that ACTUALLY ARRIVED — not an expectation of money, which is
what Charge is. The two are deliberately separate tables: a Charge exists the
moment a bill is raised, while a Payment exists only once the cash, M-Pesa
transfer or bank deposit has genuinely landed.

This separation is what lets Charge avoid storing a paid/unpaid status column
at all. Payment status is DERIVED by looking for an active Payment row against
a Charge, so it can never drift out of sync with the money actually received —
see the class docstring on Charge, which explains the same decision from the
other side.

This file only defines the schema — no business logic, no HTTP concerns
live here.
"""

import enum
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PaymentMethod(str, enum.Enum):
    """How the money physically reached the company.

    Inherits from both str and Enum so each member is simultaneously a real
    string — same reasoning as UnitType/UnitStatus/OwnerType/ChargeCategory —
    which makes JSON serialization (via Pydantic) and equality comparisons
    work naturally without extra conversion code in schemas.

    These three reflect how Damal Heights actually receives money: a mix of
    M-Pesa, bank transfer, and cash handed over at the office. They are not a
    generic payment-processor list — anything not on it is not a channel this
    company accepts.
    """

    MPESA = "mpesa"
    BANK_TRANSFER = "bank_transfer"
    CASH = "cash"


class Payment(Base):
    """SQLAlchemy ORM model for the `payments` table.

    One row = money actually received against ONE Charge.

    Exactly ONE ACTIVE payment may exist per charge, enforced at the database
    level by a partial unique index (see __table_args__ at the bottom of this
    class). Cancelling a payment frees that slot so a corrected record can be
    entered, while the mistaken row survives permanently in the audit trail.
    Without the partial condition the two goals would be irreconcilable: a
    plain unique index would let the voided row keep occupying the slot
    forever, permanently blocking its charge from ever being paid.

    The amount must EQUAL the charge's amount — partial payments are not
    permitted. That rule is not expressible here: this model can see its own
    charge_id but not the charge's amount, so the check lives in the service
    layer, which loads the Charge and compares. Same division of labour as
    Charge's category/party pairing rule, which lives in the schema layer
    rather than in DDL.

    There is no delete path anywhere — at any layer. Financial records are
    voided via is_cancelled, never destroyed, exactly as with Charge, so the
    history of what was recorded (including what was recorded in error) is
    always recoverable.
    """

    __tablename__ = "payments"

    # -------------------------------------------------------------------------
    # id
    # Auto-incremented integer primary key. SQLAlchemy sets this on INSERT;
    # we never assign it manually. Every row in the table has a unique id.
    # Mapped[int] tells SQLAlchemy that this column is an integer, and
    # mapped_column(primary_key=True) marks it as the primary key.
    # -------------------------------------------------------------------------
    id: Mapped[int] = mapped_column(primary_key=True)

    # -------------------------------------------------------------------------
    # charge_id
    # REQUIRED — every payment settles a specific charge; money received
    # against nothing has no meaning in this domain.
    # No ondelete specified, so PostgreSQL falls back to NO ACTION
    # (effectively RESTRICT): it will BLOCK deleting a Charge that already has
    # a payment recorded against it. Same reasoning as Charge.unit_id —
    # financial history is essential data and must never be silently
    # discarded or orphaned.
    # -------------------------------------------------------------------------
    charge_id: Mapped[int] = mapped_column(
        ForeignKey("charges.id"),
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # amount
    # The money received, in Numeric — NOT Float. Binary floats cannot
    # represent decimal fractions exactly (0.1 + 0.2 != 0.3), and those tiny
    # errors accumulate across many rows into real accounting discrepancies.
    # Numeric(12, 2) matches Charge.amount exactly, which matters: this value
    # must EQUAL the charge's amount, since partial payments are not
    # permitted. That equality is enforced in the service layer, which can
    # load the Charge and compare — this column can only guarantee the
    # precision, not the match.
    # -------------------------------------------------------------------------
    amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # paid_at
    # The date the money was ACTUALLY RECEIVED — deliberately distinct from
    # created_at below, which is when a staff member typed the record into
    # the system. These genuinely differ in practice: cash handed over at the
    # office on a Friday is often only entered the following Monday, and
    # backdating paid_at is the correct response, not a workaround.
    # A plain Date, not a DateTime: the banking day is the meaningful unit
    # here, and no one records the minute a payment arrived.
    # -------------------------------------------------------------------------
    paid_at: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # method
    # How the money arrived. OPTIONAL BY DESIGN — a payment recorded without
    # a method is still a completely valid payment. Money that is known to
    # have arrived should never be blocked from being recorded just because
    # the channel wasn't captured at the time.
    # SQLEnum(PaymentMethod) maps to a native PostgreSQL enum — invalid
    # values are rejected at the database level, not just in Python.
    # -------------------------------------------------------------------------
    method: Mapped[Optional[PaymentMethod]] = mapped_column(
        SQLEnum(PaymentMethod),
        nullable=True,
    )

    # -------------------------------------------------------------------------
    # reference
    # The external identifier for this payment: the M-Pesa transaction code,
    # the bank slip number, or the office receipt number — whichever applies
    # to the method used.
    # Optional for the same reason as method directly above: a payment that
    # genuinely arrived must be recordable even when the reference wasn't
    # captured. String(100) comfortably covers all three formats.
    # -------------------------------------------------------------------------
    reference: Mapped[Optional[str]] = mapped_column(
        String(100),
        nullable=True,
    )

    # -------------------------------------------------------------------------
    # is_cancelled
    # Voiding replaces deletion entirely — there is no delete path for a
    # Payment at any layer, exactly as with Charge.
    # This column also PARTICIPATES IN the partial unique index defined in
    # __table_args__ below: a cancelled payment no longer occupies its
    # charge's single active slot, which is precisely what allows a mistaken
    # payment to be voided and a corrected one recorded in its place.
    # -------------------------------------------------------------------------
    is_cancelled: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    # -------------------------------------------------------------------------
    # created_by
    # Nullable audit trail — tracks which staff member (User) recorded this
    # Payment. Identical pattern to Owner/Unit/Tenant/Charge.created_by, and
    # likewise NOT an ORM relationship(): querying the actual User requires a
    # separate lookup via UserRepository.
    # ondelete="SET NULL": if the referencing User account is ever deleted,
    # this field clears to NULL rather than blocking that deletion or
    # cascading to destroy the Payment. Note the contrast with charge_id
    # above, which is essential financial data and therefore RESTRICT.
    #
    # This field carries EXTRA WEIGHT here compared with the other entities:
    # it is not merely informational. The service layer reads it to enforce
    # that a non-admin staff member may only void payments THEY recorded, and
    # only within 24 hours — so a NULL here (a deleted user) means no
    # non-admin can ever self-void that row, which is the correct outcome.
    # -------------------------------------------------------------------------
    created_by: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    # -------------------------------------------------------------------------
    # created_at
    # The exact UTC timestamp when this row was first inserted — when the
    # payment was ENTERED, as opposed to paid_at above, which is when the
    # money arrived.
    # Set automatically on INSERT via the lambda default — never updated after
    # that. Using datetime.now(timezone.utc) instead of the deprecated
    # datetime.utcnow() which was removed in Python 3.12+.
    # DateTime(timezone=True) tells PostgreSQL to use TIMESTAMPTZ so the
    # timezone offset is preserved in the database.
    # Timezone is East African Time (EAT) for this application, but we store UTC in the DB
    # for consistency.
    #
    # Also LOAD-BEARING, not just informational: this is the timestamp the
    # service layer measures the 24-hour self-void window against, so it is
    # what decides whether a non-admin may still cancel their own payment.
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

    # -------------------------------------------------------------------------
    # __table_args__ — ix_payments_one_active_per_charge
    #
    # A PARTIAL unique index enforcing "one ACTIVE payment per charge" at the
    # DATABASE level, not merely in the service layer.
    #
    # The WHERE clause is the entire point. `postgresql_where` restricts which
    # rows the index covers, so cancelled payments are INVISIBLE to the
    # uniqueness check — they simply are not indexed. That is what makes the
    # two competing requirements compatible: voiding a mistaken payment frees
    # the charge's single active slot for a correct one, while the mistaken
    # row survives permanently in the audit trail.
    #
    # Without the partial condition — i.e. a plain unique index on charge_id
    # alone — a cancelled payment would keep occupying its charge's slot
    # forever. The charge would become permanently UNPAYABLE, with no way to
    # record the corrected payment short of destroying the erroneous row,
    # which is exactly what this table's no-delete rule forbids.
    #
    # Same mechanism as Owner's ix_owners_single_company. The syntax differs
    # in one respect worth noting: Owner writes the condition as a SQLAlchemy
    # expression (`type == OwnerType.COMPANY`), which works there because it
    # compares against a native PG enum whose DB-side labels SQLEnum knows how
    # to render. Here the condition is a plain boolean column, so it is
    # written with text() — the same form the generated migration uses for
    # Owner's index (see d56bfe32591a, `postgresql_where=sa.text(...)`).
    # -------------------------------------------------------------------------
    __table_args__ = (
        Index(
            "ix_payments_one_active_per_charge",
            "charge_id",
            unique=True,
            postgresql_where=text("is_cancelled = false"),
        ),
    )

    def __repr__(self) -> str:
        """Return a readable string representation for debugging and logs."""
        return (
            f"<Payment id={self.id} charge_id={self.charge_id} "
            f"amount={self.amount} paid_at={self.paid_at}>"
        )
