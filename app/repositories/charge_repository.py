"""
Charge repository — the single source of all SQLAlchemy queries for the Charge model.

This file is the ONLY place in the application that writes queries against
the `charges` table. No other layer (routers, services, schemas) may import
SQLAlchemy and query Charge directly.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Execute async SQLAlchemy 2.0 queries (select/insert/update).
  - Return Charge ORM instances — never Pydantic schemas.

Out of scope for this file:
  - Business logic (e.g. the category/party pairing rule — that lives in the
    Pydantic schema layer's validate_party_for_category; deciding WHEN a
    charge may be cancelled belongs to the service).
  - Raising HTTP exceptions (the service layer raises those).
  - Creating its own database sessions (the session is always injected).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import date
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.charge import Charge, ChargeCategory
from app.schemas.charge import ChargeCancel, ChargeUpdate


# =============================================================================
# SECTION 2 — ChargeRepository class
# =============================================================================
class ChargeRepository:
    """
    Data-access layer for the Charge model.

    This is the ONLY place in the app that writes SQLAlchemy queries for
    the Charge model. All other layers talk to this class; none of them
    import or use SQLAlchemy directly.

    Contract:
      - Receives an AsyncSession injected from get_db() — never creates one.
      - Returns Charge model instances — never Pydantic schemas or plain
        dicts. The one exception is get_total_by_unit_and_period_range(),
        which returns a Decimal because a SUM is a single number, not a row.
      - Never contains business logic (no category/party validation here —
        that belongs to the Pydantic schema layer, exactly like password
        hashing belongs in the service, not the repository).
      - Never raises HTTP exceptions — only database-level errors propagate.

    NO delete() method — by design, not by omission:
      Every other repository in this project (User, Owner, Unit, Tenant)
      exposes delete(). This one deliberately does NOT, and no delete method
      should ever be added here. Financial records are never destroyed, only
      VOIDED via cancel(), which flips is_cancelled to True and leaves the
      row in place. That is what keeps the audit trail intact: what was
      billed, and the fact that it was later cancelled, both stay
      recoverable forever. A hard DELETE would erase the evidence that a bill
      was ever raised at all — indistinguishable from it never having
      existed. If you came here looking for delete(), cancel() is the method
      you want.

    Aggregate and range queries — also by design:
      This repository carries methods no other repository in the project
      needs: date-range filters (get_by_unit_and_period_range) and a
      database-side SUM (get_total_by_unit_and_period_range). That is not
      scope creep; it follows from the domain. Billing questions are
      inherently about TIME SPANS and TOTALS — "what did this unit accrue
      between March and June", "what is the outstanding balance" — rather
      than the single-row "fetch this entity by id" lookups that Owner, Unit
      and Tenant are built around. The queries live here for the same reason
      every other query does: the repository is the only layer allowed to
      speak SQL.
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
    async def get_by_id(self, charge_id: int) -> Optional[Charge]:
        """
        Fetch a single Charge row by its primary key.

        Args:
            charge_id: The integer primary key of the target charge.

        Returns:
            The matching Charge ORM instance, or None if no row exists.
        """
        # Used for GET /charges/{id} and the fetch-then-act step of the
        # PATCH and PATCH /cancel endpoints — answers "does THIS charge exist
        # by its real id."
        result = await self.db.execute(select(Charge).where(Charge.id == charge_id))
        # scalar_one_or_none() returns the Charge object or None.
        # If multiple rows somehow match (impossible with a primary-key
        # constraint, but guarded against here), it raises MultipleResultsFound
        # — a data-integrity protection that should never fire in practice.
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 4 — get_by_unit_and_period_range
    # =========================================================================
    async def get_by_unit_and_period_range(
        self, unit_id: int, start: date, end: date
    ) -> list[Charge]:
        """
        Fetch every Charge raised against one unit within a billing window.

        The window is INCLUSIVE at both ends — a charge dated exactly `start`
        or exactly `end` is included.

        Args:
            unit_id: The integer primary key of the unit being billed.
            start:   First day of the earliest billing period to include.
            end:     First day of the latest billing period to include.

        Returns:
            A list of Charge ORM instances ordered oldest period first.
            Empty list if the unit has no charges in that window.
        """
        # Answers "show me this unit's billing history for a stretch of
        # months" — the statement view, e.g. every charge from January
        # through June.
        #
        # Chained .where() calls are combined with AND — this is exactly
        # equivalent to a single .where(and_(...)) but reads as one condition
        # per line, which matters when there are three of them.
        #
        # period is a real Date column, so >= and <= are natural date
        # comparisons handled by PostgreSQL itself. THIS is why the model
        # chose Date over separate year/month integers: with two integer
        # columns, "March 2026 through February 2027" would need an awkward
        # composed condition in every single WHERE clause
        # ((year > 2026) OR (year = 2026 AND month >= 3)) AND ... — instead
        # of the two plain comparisons below.
        #
        # Cancelled charges are NOT filtered out here: this is the history
        # view, and a voided bill is part of the history. Callers that want
        # only live money should use the total method in Section 7.
        result = await self.db.execute(
            select(Charge)
            .where(Charge.unit_id == unit_id)
            .where(Charge.period >= start)
            .where(Charge.period <= end)
            .order_by(Charge.period)
        )
        return list(result.scalars().all())

    # =========================================================================
    # SECTION 5 — get_by_tenant_and_period
    # =========================================================================
    async def get_by_tenant_and_period(self, tenant_id: int, period: date) -> list[Charge]:
        """
        Fetch every Charge owed by one tenant for one specific billing month.

        Args:
            tenant_id: The integer primary key of the tenant.
            period:    The first day of the billing month (e.g. 2026-03-01).

        Returns:
            A list of Charge ORM instances ordered by category. Empty list if
            nothing was billed to that tenant for that month.

        Note:
            Returns a LIST, not a single row. That is deliberate — see the
            comment below.
        """
        # Answers "what does this tenant owe for this specific month".
        #
        # This TYPICALLY returns MULTIPLE rows, which is why the return type
        # is a list rather than Optional[Charge]: a tenant normally owes rent
        # AND water for the same period, and may additionally owe a one-off
        # move_out charge in their final month. There is no unique constraint
        # on (tenant_id, period) and there must not be one — each category is
        # its own bill.
        #
        # order_by(category) groups the month's bills consistently (the enum
        # orders by its stored string value), so the same tenant's March
        # statement always lists its lines in the same order rather than in
        # whatever order PostgreSQL happens to return them.
        result = await self.db.execute(
            select(Charge)
            .where(Charge.tenant_id == tenant_id)
            .where(Charge.period == period)
            .order_by(Charge.category)
        )
        return list(result.scalars().all())

    # =========================================================================
    # SECTION 6 — get_by_owner_and_period
    # =========================================================================
    async def get_by_owner_and_period(self, owner_id: int, period: date) -> list[Charge]:
        """
        Fetch every Charge owed by one owner for one specific billing month.

        Args:
            owner_id: The integer primary key of the owner.
            period:   The first day of the billing month (e.g. 2026-03-01).

        Returns:
            A list of Charge ORM instances ordered by category. Empty list if
            nothing was billed to that owner for that month.
        """
        # Answers "what does this owner owe for this month" — the mirror
        # image of Section 5, for the other billable party.
        #
        # Also typically multi-row, but for a different reason. A tenant's
        # several rows are several CATEGORIES against one unit; an owner's
        # several rows are usually the SAME category (service_charge) across
        # SEVERAL UNITS they own. One owner, one month, five units → five
        # service_charge rows, one per unit.
        #
        # Same order_by(category) as Section 5 for a stable, predictable
        # statement ordering.
        result = await self.db.execute(
            select(Charge)
            .where(Charge.owner_id == owner_id)
            .where(Charge.period == period)
            .order_by(Charge.category)
        )
        return list(result.scalars().all())

    # =========================================================================
    # SECTION 7 — get_total_by_unit_and_period_range
    # =========================================================================
    async def get_total_by_unit_and_period_range(
        self, unit_id: int, start: date, end: date
    ) -> Decimal:
        """
        Sum the LIVE (non-cancelled) charges for one unit across a window.

        The window is INCLUSIVE at both ends, matching
        get_by_unit_and_period_range().

        Args:
            unit_id: The integer primary key of the unit being billed.
            start:   First day of the earliest billing period to include.
            end:     First day of the latest billing period to include.

        Returns:
            The total amount as a Decimal. Returns Decimal("0.00") — never
            None — when no matching rows exist.
        """
        # func.sum() pushes the addition down into PostgreSQL: the database
        # adds the amount column up and returns ONE number over the wire.
        # The alternative — SELECTing every matching row, building a Charge
        # object for each, and summing in a Python loop — produces the same
        # answer but transfers and instantiates every row to do it. With a
        # handful of rows the difference is invisible; across years of
        # billing history for a whole building it is not. Charges only ever
        # accumulate (nothing is deleted here, by design), so this table is
        # the one in the project guaranteed to keep growing.
        #
        # .is_(False) is the correct SQLAlchemy idiom for a boolean column.
        # It generates proper SQL (`is_cancelled IS false`), whereas the
        # Python-natural `Charge.is_cancelled == False` trips flake8's E712
        # and `not Charge.is_cancelled` is silently WRONG — Python would
        # evaluate the truthiness of the column object itself and collapse it
        # to a constant before SQLAlchemy ever saw it.
        #
        # Cancelled charges are excluded because a voided bill is not money
        # owed. The row survives for the audit trail (see the class
        # docstring's note on why there is no delete()), but including it in
        # a balance would overstate what the unit actually owes. Note the
        # deliberate contrast with Section 4, which does NOT filter: that
        # method answers "what happened", this one answers "what is owed".
        result = await self.db.execute(
            select(func.sum(Charge.amount))
            .where(Charge.unit_id == unit_id)
            .where(Charge.period >= start)
            .where(Charge.period <= end)
            .where(Charge.is_cancelled.is_(False))
        )
        # scalar_one() — an aggregate with no GROUP BY always returns exactly
        # one row, so there is no "or none" case at the ROW level. The VALUE
        # in that row is NULL when nothing matched, which is why the None
        # check below is still required.
        total = result.scalar_one()

        # SQL SUM over zero rows returns NULL, not 0. Normalising to
        # Decimal("0.00") here means callers never have to handle None, and
        # the return type stays honestly Decimal rather than
        # Optional[Decimal]. Decimal (not float) throughout, matching the
        # model's Numeric(12, 2) — exact decimal arithmetic, no binary
        # float drift.
        return total if total is not None else Decimal("0.00")

    # =========================================================================
    # SECTION 8 — create
    # =========================================================================
    async def create(
        self,
        unit_id: int,
        category: ChargeCategory,
        amount: Decimal,
        period: date,
        owner_id: Optional[int] = None,
        tenant_id: Optional[int] = None,
        percentage: Optional[Decimal] = None,
        created_by: Optional[int] = None,
    ) -> Charge:
        """
        Insert a new Charge row into the database.

        Args:
            unit_id:    The unit this bill is raised against. REQUIRED — a
                        charge that belongs to no unit is meaningless.
            category:   What the bill is for, and by implication which party
                        owes it (see ChargeCategory).
            amount:     The money owed, as an exact Decimal.
            period:     First day of the billing month this charge covers.
            owner_id:   Set ONLY for SERVICE_CHARGE. The category/party
                        pairing is enforced by the schema layer, not here.
            tenant_id:  Set ONLY for RENT/WATER/MOVE_OUT. Same note as above.
            percentage: Set ONLY for SERVICE_CHARGE — records the RULE that
                        produced the amount (e.g. 10.00 for 10%), for audit.
            created_by: Optional id of the User (staff member) who raised
                        this charge — a pure audit trail. Optional HERE so
                        this method stays general purpose for non-HTTP
                        callers (scripts, monthly billing runs). The SERVICE
                        is responsible for always passing the authenticated
                        user's id when called from the real creation flow.

        Returns:
            The newly created Charge instance, fully populated from the DB
            (id and created_at are present after refresh).

        Note:
            is_cancelled is NOT a parameter. A charge is always born live;
            voiding is a later, deliberate act via cancel(). Allowing a row
            to be created already-cancelled would let a bill exist that was
            never actually raised.
        """
        charge = Charge(
            unit_id=unit_id,
            category=category,
            amount=amount,
            period=period,
            owner_id=owner_id,
            tenant_id=tenant_id,
            percentage=percentage,
            created_by=created_by,
        )

        # add() — stages the object in the session's identity map.
        # The row does NOT exist in PostgreSQL yet at this point.
        self.db.add(charge)

        # commit() — opens a transaction, flushes the INSERT to PostgreSQL,
        # and commits. After this call the row exists and PostgreSQL has
        # assigned id and created_at.
        #
        # Rollback handling is essential here: unit_id, owner_id and
        # tenant_id are all real FOREIGN KEYs, so any of them pointing at a
        # non-existent row makes commit() fail with an IntegrityError.
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
        # the Python object. Without this, charge.id, charge.created_at and
        # the server-side is_cancelled default would still be unset on the
        # Python side.
        await self.db.refresh(charge)

        return charge

    # =========================================================================
    # SECTION 9 — update
    # =========================================================================
    async def update(self, charge: Charge, payload: ChargeUpdate) -> Charge:
        """
        Apply a partial update to an existing Charge row.

        Handles CORRECTABLE fields only (amount, percentage, period).
        ChargeUpdate deliberately does not carry category, unit_id, owner_id
        or tenant_id — changing those would rewrite financial history rather
        than correct a typo in it. Nor does it carry is_cancelled; voiding
        goes through cancel() instead.

        The caller fetches the Charge first and passes it here together with a
        ChargeUpdate payload. Only fields the client actually sent are
        written — fields not included in the request body are left unchanged.

        Args:
            charge:  The existing Charge ORM instance to be modified.
            payload: A ChargeUpdate Pydantic model containing the fields to
                     change. Fields not supplied by the client are absent
                     from the model's __fields_set__ and are therefore
                     skipped.

        Returns:
            The updated Charge instance, reloaded from the database.
        """
        # exclude_unset=True is the correct partial-update pattern.
        #
        # Without it: model_dump() would include every field — even those the
        # client never sent — serialised to their default (None):
        #   {"amount": None, "percentage": None, "period": date(2026, 3, 1)}
        # That would null out amount on a row whose amount is NOT NULL,
        # failing at the database level, and would wipe a legitimate
        # percentage the client never intended to touch.
        #
        # With it: only fields the client explicitly included appear:
        #   {"amount": Decimal("15000.00")}
        # So only amount is updated — period and percentage are untouched.
        update_data = payload.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            # setattr(charge, "amount", x) is exactly equivalent to
            # charge.amount = x, but works when the field name is a variable
            # at runtime (as it is here, iterating over a dict).
            setattr(charge, field, value)

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        self.db.add(charge)
        await self.db.commit()
        await self.db.refresh(charge)

        return charge

    # =========================================================================
    # SECTION 10 — cancel
    # =========================================================================
    async def cancel(self, charge: Charge, payload: ChargeCancel) -> Charge:
        """
        Void a charge by flipping its is_cancelled flag — the ONLY way a
        charge is ever taken out of circulation.

        This method exists INSTEAD OF delete(), not alongside it. See the
        class docstring: financial records are never destroyed. The row stays
        in the table permanently; only its is_cancelled flag changes, so both
        facts — that the bill was raised, and that it was later voided —
        remain recoverable.

        Deliberately SEPARATE from update() — this is the method backing
        PATCH /charges/{id}/cancel. It only ever touches the is_cancelled
        column, never an amount or a period, so a routine correction can
        never accidentally void a bill and voiding can never quietly rewrite
        what was owed.

        Args:
            charge:  The existing Charge ORM instance to be voided.
            payload: A ChargeCancel Pydantic model carrying the flag. That
                     schema contains ONLY is_cancelled — no other field
                     exists on it to send — and defaults to True.

        Returns:
            The updated Charge instance, reloaded from the database.

        Note:
            Whether a charge is ALLOWED to be cancelled (already cancelled?
            already paid?) is a business rule and belongs to the service
            layer. This method just writes the flag it is given.
        """
        # Assigned directly rather than via model_dump/setattr: is_cancelled
        # is the only field this schema carries, so there is no
        # partial-update ambiguity to resolve here.
        charge.is_cancelled = payload.is_cancelled

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        # The model's onupdate lambda sets updated_at automatically, which is
        # what timestamps the voiding for the audit trail.
        self.db.add(charge)
        await self.db.commit()
        await self.db.refresh(charge)

        return charge

    # =========================================================================
    # SECTION 11 — get_all
    # =========================================================================
    async def get_all(self) -> list[Charge]:
        """
        Fetch all Charge rows ordered by creation date, newest first.

        Returns:
            A list of Charge ORM instances ordered by created_at descending.
            Returns an empty list if no charges exist.
        """
        result = await self.db.execute(select(Charge).order_by(Charge.created_at.desc()))
        # scalars().all() unpacks the result rows and returns a plain Python
        # list of Charge objects. Returns [] if the table is empty — never
        # None.
        #
        # Cancelled charges ARE included — this is the unfiltered admin view,
        # and a voided bill is still a record. Ordered by created_at (when
        # the row was raised), NOT by period (which month it bills), because
        # those genuinely differ: a correction raised today can carry a
        # period from three months ago.
        return list(result.scalars().all())
