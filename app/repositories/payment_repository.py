"""
Payment repository — the single source of all SQLAlchemy queries for the Payment model.

This file is the ONLY place in the application that writes queries against
the `payments` table. No other layer (routers, services, schemas) may import
SQLAlchemy and query Payment directly.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Execute async SQLAlchemy 2.0 queries (select/insert/update).
  - Return Payment ORM instances — never Pydantic schemas.

Out of scope for this file:
  - Business logic (the amount-must-equal-the-charge rule and the 24-hour
    self-void window both belong to the service, which can load the Charge
    and see the authenticated user; neither is expressible here).
  - Raising HTTP exceptions (the service layer raises those).
  - Creating its own database sessions (the session is always injected).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.payment import Payment, PaymentMethod
from app.schemas.payment import PaymentCancel


# =============================================================================
# SECTION 2 — PaymentRepository class
# =============================================================================
class PaymentRepository:
    """
    Data-access layer for the Payment model.

    This is the ONLY place in the app that writes SQLAlchemy queries for
    the Payment model. All other layers talk to this class; none of them
    import or use SQLAlchemy directly.

    Contract:
      - Receives an AsyncSession injected from get_db() — never creates one.
      - Returns Payment model instances — never Pydantic schemas or plain
        dicts.
      - Never contains business logic. In particular, the rule that a
        payment's amount must EXACTLY EQUAL its charge's amount lives in
        PaymentService, which can load the Charge and compare; and the rule
        that a non-admin may only void their own payment within 24 hours
        lives there too, since it needs the authenticated user. This layer
        can see neither.
      - Never raises HTTP exceptions — only database-level errors propagate.

    NO delete() method — by design, not by omission:
      Same rule as ChargeRepository, and for the same reason. Every other
      repository in this project (User, Owner, Unit, Tenant) exposes
      delete(). This one deliberately does NOT, and no delete method should
      ever be added here. Financial records are never destroyed, only VOIDED
      via cancel(), which flips is_cancelled to True and leaves the row in
      place. That is what keeps the audit trail intact: money recorded in
      error stays visible as a mistaken record rather than vanishing as if it
      had never been entered. If you came here looking for delete(), cancel()
      is the method you want.

    Why get_active_by_charge_id exists:
      The model's partial unique index (ix_payments_one_active_per_charge)
      is the real guarantee that a charge has at most one ACTIVE payment. But
      a constraint violation surfaces as a bare IntegrityError, which cannot
      tell the service WHICH constraint failed — charge_id's foreign key and
      the partial unique index both arrive as the same exception type. So
      get_active_by_charge_id lets PaymentService check the
      one-active-payment-per-charge rule BEFORE attempting the insert, and
      return a specific, actionable 409 naming the payment that is blocking,
      instead of a vague error guessed at from an exception it cannot
      classify. The pre-check improves the message; the index still enforces
      the rule.

    No update() method either:
      Unlike ChargeRepository, there is nothing here backing a general
      PATCH. With the amount forced to equal the charge's amount and
      method/reference being incidental detail, there is nothing meaningful
      to edit — void the mistaken payment and record a corrected one. There
      is no PaymentUpdate schema for the same reason.
    """

    def __init__(self, db: AsyncSession) -> None:
        """
        Store the injected session.

        Args:
            db: An AsyncSession produced by the get_db() dependency and
                injected by FastAPI's dependency injection system.
        """
        # Session is injected — never created here.
        self.db = db

    # =========================================================================
    # SECTION 3 — get_by_id
    # =========================================================================
    async def get_by_id(self, payment_id: int) -> Optional[Payment]:
        """
        Fetch a single Payment row by its primary key.

        Args:
            payment_id: The integer primary key of the target payment.

        Returns:
            The matching Payment ORM instance, or None if no row exists.
        """
        # Used for GET /payments/{id} and the fetch-then-act step of
        # PATCH /payments/{id}/cancel — answers "does THIS payment exist by
        # its real id." The service also reads created_by and created_at off
        # the returned row to decide whether the caller may void it.
        result = await self.db.execute(select(Payment).where(Payment.id == payment_id))
        # scalar_one_or_none() returns the Payment object or None.
        # If multiple rows somehow match (impossible with a primary-key
        # constraint, but guarded against here), it raises MultipleResultsFound
        # — a data-integrity protection that should never fire in practice.
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 4 — get_active_by_charge_id
    # =========================================================================
    async def get_active_by_charge_id(self, charge_id: int) -> Optional[Payment]:
        """
        Fetch the single ACTIVE (non-cancelled) payment for one charge.

        Args:
            charge_id: The integer primary key of the charge being settled.

        Returns:
            The active Payment ORM instance, or None if the charge has no
            live payment — either because none was ever recorded, or because
            every payment recorded against it has since been voided.
        """
        # Answers "is this charge already paid, and by which row" — the
        # question PaymentService asks before allowing a new payment to be
        # recorded.
        #
        # scalar_one_or_none() is correct here rather than scalars().all(),
        # even though charge_id is not a primary key: the partial unique
        # index on the model guarantees AT MOST ONE non-cancelled payment per
        # charge, so this query can structurally never match two rows. If it
        # ever did raise MultipleResultsFound, that would mean the index was
        # never created or has been dropped — a silent corruption of the
        # one-payment-per-charge rule, and exactly the kind of thing worth
        # failing loudly on rather than papering over by quietly returning
        # the first row.
        #
        # .is_(False) is the correct SQLAlchemy idiom for a boolean column.
        # It generates proper SQL (`is_cancelled IS false`), whereas the
        # Python-natural `Payment.is_cancelled == False` trips flake8's E712
        # and `not Payment.is_cancelled` is silently WRONG — Python would
        # evaluate the truthiness of the column object itself and collapse it
        # to a constant before SQLAlchemy ever saw it. Note this WHERE clause
        # mirrors the index's own postgresql_where condition exactly, which
        # is what makes the pre-check agree with the constraint.
        #
        # This method exists so PaymentService can raise a specific 409
        # naming the blocking payment, instead of catching a bare
        # IntegrityError that cannot distinguish which constraint failed. The
        # partial unique index remains the ACTUAL guarantee — a concurrent
        # insert can still slip between this check and the commit, and the
        # database will reject it. This only improves the error message in
        # the overwhelmingly common non-racing case.
        result = await self.db.execute(
            select(Payment)
            .where(Payment.charge_id == charge_id)
            .where(Payment.is_cancelled.is_(False))
        )
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 5 — get_by_charge_id
    # =========================================================================
    async def get_by_charge_id(self, charge_id: int) -> list[Payment]:
        """
        Fetch the FULL payment history for one charge, cancelled rows included.

        Args:
            charge_id: The integer primary key of the charge.

        Returns:
            A list of Payment ORM instances ordered newest-recorded first.
            Empty list if nothing has ever been recorded against the charge.
        """
        # Deliberately distinct from get_active_by_charge_id in Section 4.
        # That method answers "what is the live payment"; this one answers
        # "what has happened to this bill" — the complete audit trail, which
        # may contain SEVERAL voided attempts plus at most one active
        # payment. Because nothing is ever deleted here, that history is
        # permanent, and a charge corrected twice genuinely has three rows.
        #
        # Hence the list return type, in contrast to Section 4's
        # Optional[Payment]: the one-per-charge index constrains only
        # NON-CANCELLED rows, so there is no upper bound at all on how many
        # rows this can return.
        #
        # Ordered by created_at descending — when the record was ENTERED, not
        # paid_at when the money arrived. For an audit trail the sequence of
        # bookkeeping actions is the meaningful order: the correction that
        # replaced a mistake comes first, and a backdated paid_at cannot
        # scramble the sequence.
        result = await self.db.execute(
            select(Payment)
            .where(Payment.charge_id == charge_id)
            .order_by(Payment.created_at.desc())
        )
        return list(result.scalars().all())

    # =========================================================================
    # SECTION 6 — create
    # =========================================================================
    async def create(
        self,
        charge_id: int,
        amount: Decimal,
        paid_at: date,
        method: Optional[PaymentMethod] = None,
        reference: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> Payment:
        """
        Insert a new Payment row into the database.

        Args:
            charge_id:  The charge this money settles. REQUIRED — money
                        received against nothing has no meaning here.
            amount:     The money received, as an exact Decimal. Whether it
                        EQUALS the charge's amount is checked by the service,
                        which can load the Charge; this layer cannot.
            paid_at:    The date the money actually arrived — distinct from
                        created_at, which the model sets to now.
            method:     How the money arrived. Optional by design: a payment
                        known to have been received is still recordable when
                        the channel was not captured.
            reference:  External identifier (M-Pesa code, bank slip, receipt
                        number). Optional, and INDEPENDENTLY so — a reference
                        with no method is deliberately allowed.
            created_by: Optional id of the User (staff member) who recorded
                        this payment. Optional HERE so this method stays
                        general purpose for non-HTTP callers (scripts,
                        reconciliation runs). The SERVICE is responsible for
                        always passing the authenticated user's id when
                        called from the real recording flow — and it matters
                        more here than elsewhere, because the service later
                        reads this field back to decide who may void the row.

        Returns:
            The newly created Payment instance, fully populated from the DB
            (id and created_at are present after refresh).

        Note:
            is_cancelled is NOT a parameter. A payment is always born live;
            voiding is a later, deliberate act via cancel(). The model's
            default=False handles it. Allowing a row to be created
            already-cancelled would let a payment exist that was never
            actually received — and, because the partial unique index ignores
            cancelled rows, it would not even occupy its charge's slot.
        """
        payment = Payment(
            charge_id=charge_id,
            amount=amount,
            paid_at=paid_at,
            method=method,
            reference=reference,
            created_by=created_by,
        )

        # add() — stages the object in the session's identity map.
        # The row does NOT exist in PostgreSQL yet at this point.
        self.db.add(payment)

        # commit() — opens a transaction, flushes the INSERT to PostgreSQL,
        # and commits. After this call the row exists and PostgreSQL has
        # assigned id and created_at.
        #
        # Rollback handling is essential here, and TWO distinct constraints
        # can fire:
        #   - charge_id is a real FOREIGN KEY (and NO ACTION / RESTRICT), so
        #     pointing it at a non-existent charge fails the commit. Same for
        #     created_by against users.id.
        #   - the partial unique index ix_payments_one_active_per_charge can
        #     also reject here, even though the service pre-checks it with
        #     get_active_by_charge_id: two concurrent requests can both pass
        #     that check and only one can win the insert. The database is the
        #     real arbiter of that race, which is exactly why the index
        #     exists rather than the check alone.
        # Without the rollback, the session's transaction would stay in a
        # broken state for the rest of the request — every later query on it
        # would fail too.
        #
        # Bare re-raise — the repository never raises HTTPException, so it
        # stays usable outside an HTTP context. Translating this into a
        # client-facing error is the SERVICE layer's job.
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise

        # refresh() — issues a SELECT to reload the row from the DB back onto
        # the Python object. Without this, payment.id, payment.created_at and
        # the is_cancelled default would still be unset on the Python side.
        await self.db.refresh(payment)

        return payment

    # =========================================================================
    # SECTION 7 — cancel
    # =========================================================================
    async def cancel(self, payment: Payment, payload: PaymentCancel) -> Payment:
        """
        Void a payment by flipping its is_cancelled flag — the ONLY way a
        payment is ever taken out of circulation.

        This method exists INSTEAD OF delete(), not alongside it. See the
        class docstring: financial records are never destroyed. The row stays
        in the table permanently; only its is_cancelled flag changes, so both
        facts — that the payment was recorded, and that it was later voided —
        remain recoverable.

        Voiding also FREES the charge's single active-payment slot. The
        partial unique index counts only non-cancelled rows, so once this
        flag flips, the mistaken record stops occupying the slot and a
        corrected payment can be recorded against the same charge. That WHERE
        clause on the index is the entire reason recovering from a mistake is
        possible at all: a plain unique index would leave the voided row
        holding the slot forever, making the charge permanently unpayable.

        Args:
            payment: The existing Payment ORM instance to be voided.
            payload: A PaymentCancel Pydantic model carrying the flag. That
                     schema contains ONLY is_cancelled — no other field
                     exists on it to send — and defaults to True.

        Returns:
            The updated Payment instance, reloaded from the database.

        Note:
            WHO is allowed to cancel, and WHEN, is a business rule and
            belongs to the service layer: an admin may void any payment, a
            non-admin only their own and only within 24 hours of recording
            it. This method just writes the flag it is given.
        """
        # Assigned directly rather than via model_dump/setattr: is_cancelled
        # is the only field this schema carries, so there is no
        # partial-update ambiguity to resolve here. This is also the only
        # write path on this class besides create() — there is no update().
        payment.is_cancelled = payload.is_cancelled

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        # The model's onupdate lambda sets updated_at automatically, which is
        # what timestamps the voiding for the audit trail.
        self.db.add(payment)
        await self.db.commit()
        await self.db.refresh(payment)

        return payment

    # =========================================================================
    # SECTION 8 — get_all
    # =========================================================================
    async def get_all(self) -> list[Payment]:
        """
        Fetch all Payment rows ordered by payment date, newest first.

        Returns:
            A list of Payment ORM instances ordered by paid_at descending,
            with id descending as a tiebreak. Returns an empty list if no
            payments exist.
        """
        # Cancelled payments ARE included — this is the unfiltered admin
        # view, and a voided payment is still a record.
        #
        # Ordered by paid_at (when the money arrived) rather than created_at
        # (when the row was entered), the opposite choice from
        # get_by_charge_id in Section 5. That is deliberate: this is the
        # money-received ledger, where the banking day is what a reader is
        # scanning for, whereas Section 5 is one bill's audit trail, where
        # the order of bookkeeping actions is what matters.
        #
        # id DESC is a necessary tiebreak, not decoration. paid_at is a plain
        # Date, so every payment received on the same day ties on the sort
        # key, and PostgreSQL is free to return tied rows in ANY order —
        # which can differ between two runs of the identical query. Without
        # the tiebreak, paginating this list could show the same row twice or
        # skip one entirely. id DESC is unique and monotonic, so it settles
        # every tie and puts the most recently entered row first within a day.
        result = await self.db.execute(
            select(Payment).order_by(Payment.paid_at.desc(), Payment.id.desc())
        )
        # scalars().all() unpacks the result rows and returns a plain Python
        # list of Payment objects. Returns [] if the table is empty — never
        # None.
        return list(result.scalars().all())
