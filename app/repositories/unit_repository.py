"""
Unit repository — the single source of all SQLAlchemy queries for the Unit model.

This file is the ONLY place in the application that writes queries against
the `units` table. No other layer (routers, services, schemas) may import
SQLAlchemy and query Unit directly.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Execute async SQLAlchemy 2.0 queries (select/insert/update/delete).
  - Return Unit ORM instances — never Pydantic schemas.

Out of scope for this file:
  - Business logic (e.g. the bedrooms/unit_type combination rules — those live
    in the Pydantic schema layer's validate_bedrooms_for_type, and any
    status-transition rules belong to the service).
  - Raising HTTP exceptions (the service layer raises those).
  - Creating its own database sessions (the session is always injected).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.unit import Unit, UnitStatus, UnitType
from app.schemas.unit import UnitStatusUpdate, UnitUpdate


# =============================================================================
# SECTION 2 — UnitRepository class
# =============================================================================
class UnitRepository:
    """
    Data-access layer for the Unit model.

    This is the ONLY place in the app that writes SQLAlchemy queries for
    the Unit model. All other layers talk to this class; none of them
    import or use SQLAlchemy directly.

    Contract:
      - Receives an AsyncSession injected from get_db() — never creates one.
      - Returns Unit model instances — never Pydantic schemas or plain dicts.
      - Never contains business logic (no bedrooms/unit_type validation here —
        that belongs to the Pydantic schema layer, exactly like password
        hashing belongs in the service, not the repository; no
        status-transition rules here either — that is the service's job if any
        such rules ever exist).
      - Never raises HTTP exceptions — only database-level errors propagate.
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
    async def get_by_id(self, unit_id: int) -> Optional[Unit]:
        """
        Fetch a single Unit row by its primary key.

        Args:
            unit_id: The integer primary key of the target unit.

        Returns:
            The matching Unit ORM instance, or None if no row exists.
        """
        # Used for GET /units/{id} and the fetch-then-act step of every
        # PATCH/DELETE endpoint — answers "does THIS unit exist by its real
        # id."
        result = await self.db.execute(select(Unit).where(Unit.id == unit_id))
        # scalar_one_or_none() returns the Unit object or None.
        # If multiple rows somehow match (impossible with a primary-key
        # constraint, but guarded against here), it raises MultipleResultsFound
        # — a data-integrity protection that should never fire in practice.
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 4 — get_by_unit_number
    # =========================================================================
    async def get_by_unit_number(self, unit_number: str) -> Optional[Unit]:
        """
        Fetch a single Unit row by its human-readable unit number.

        Args:
            unit_number: The building-wide unique identifier (e.g. "B-01",
                "G-03", "5-2A").

        Returns:
            The matching Unit ORM instance, or None if the number is unused.
        """
        # Used ONLY by the service's create_unit(), BEFORE attempting an
        # insert — checks whether this unit_number is already taken, so a
        # clean 400 can be raised instead of letting the database's unique
        # constraint produce a raw IntegrityError.
        #
        # unit_number is unique building-wide (NOT per-floor), matching the
        # model's unique=True constraint — so at most one row can ever match.
        result = await self.db.execute(select(Unit).where(Unit.unit_number == unit_number))
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 5 — get_by_owner
    # =========================================================================
    async def get_by_owner(self, owner_id: int) -> list[Unit]:
        """
        Fetch every Unit row belonging to a given owner.

        Args:
            owner_id: The integer primary key of the owning Owner.

        Returns:
            A list of Unit ORM instances owned by that owner. Empty list if
            the owner currently owns none.
        """
        # Returns every unit a given owner currently owns — useful both for
        # general queries and for OwnerService.delete_owner's 409 conflict
        # scenario, where confirming WHICH units block a deletion may be
        # useful context to surface later.
        result = await self.db.execute(select(Unit).where(Unit.owner_id == owner_id))
        return list(result.scalars().all())

    # =========================================================================
    # SECTION 6 — create
    # =========================================================================
    async def create(
        self,
        unit_number: str,
        floor: int,
        unit_type: UnitType,
        owner_id: int,
        bedrooms: Optional[int] = None,
        size: Optional[float] = None,
        status: UnitStatus = UnitStatus.AVAILABLE,
        created_by: Optional[int] = None,
    ) -> Unit:
        """
        Insert a new Unit row into the database.

        Args:
            unit_number: The building-wide unique identifier for the unit.
            floor:       Which floor the unit is on (basement=-1, ground=0,
                         upwards).
            unit_type:   The UnitType for this row (shop, office, restaurant,
                         lodge, apartment).
            owner_id:    The owner this unit belongs to. REQUIRED with no
                         default — every unit must state its owner explicitly,
                         whether the real buyer's id or the company's id for
                         unsold units. Ownership never defaults silently.
            bedrooms:    Optional bedroom count. None means the concept does
                         not apply to this unit type. The valid combinations
                         of unit_type and bedrooms are enforced by the schema
                         layer, not here.
            size:        Optional square footage.
            status:      The initial occupancy state. Defaults to
                         UnitStatus.AVAILABLE at the repository level too,
                         matching the model's own default — belt-and-braces,
                         so the service never has to remember to pass it
                         explicitly for a normal creation.
            created_by:  Optional id of the User (staff member) who created
                         this row — a pure audit trail. Optional HERE so this
                         method stays general purpose for non-HTTP callers
                         (scripts, data migrations). The SERVICE is
                         responsible for always passing the authenticated
                         admin's id when called from the real creation flow.

        Returns:
            The newly created Unit instance, fully populated from the DB
            (id and created_at are present after refresh).
        """
        unit = Unit(
            unit_number=unit_number,
            floor=floor,
            unit_type=unit_type,
            owner_id=owner_id,
            bedrooms=bedrooms,
            size=size,
            status=status,
            created_by=created_by,
        )

        # add() — stages the object in the session's identity map.
        # The row does NOT exist in PostgreSQL yet at this point.
        self.db.add(unit)

        # commit() — opens a transaction, flushes the INSERT to PostgreSQL,
        # and commits. After this call the row exists in the DB and PostgreSQL
        # has assigned id and created_at.
        #
        # Unlike OwnerRepository.create(), this commit needs rollback handling:
        # owner_id is a real FOREIGN KEY, so an owner_id pointing at no existing
        # owner makes commit() fail with an IntegrityError. Without the
        # rollback, the session's transaction would stay in a broken state for
        # the rest of the request — every later query on it would fail too.
        #
        # Bare re-raise, same as delete() — the repository never raises
        # HTTPException, so it stays usable outside an HTTP context. Translating
        # this into a client-facing error is the SERVICE layer's job.
        try:
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise

        # refresh() — issues a SELECT to reload the row from the DB back onto
        # the Python object. Without this, unit.id and unit.created_at would
        # still be None on the Python side (they were None before commit).
        await self.db.refresh(unit)

        return unit

    # =========================================================================
    # SECTION 7 — update
    # =========================================================================
    async def update(self, unit: Unit, payload: UnitUpdate) -> Unit:
        """
        Apply a partial update to an existing Unit row.

        Handles STRUCTURAL fields only (unit_number, floor, unit_type,
        bedrooms, size, owner_id) — status changes go through update_status()
        instead, matching the two-tier permission split already decided:
        structural edits are admin only, status changes are open to any staff
        member.

        The caller fetches the Unit first and passes it here together with a
        UnitUpdate payload. Only fields the client actually sent are written —
        fields not included in the request body are left unchanged.

        Args:
            unit:    The existing Unit ORM instance to be modified.
            payload: A UnitUpdate Pydantic model containing the fields to
                     change. Fields not supplied by the client are absent from
                     the model's __fields_set__ and are therefore skipped.

        Returns:
            The updated Unit instance, reloaded from the database.
        """
        # exclude_unset=True is the correct partial-update pattern.
        #
        # Without it: model_dump() would include every field — even those the
        # client never sent — serialised to their default (usually None):
        #   {"unit_number": None, "floor": None, "size": 450.0, ...}
        # That would overwrite unit_number and floor with None even though the
        # client only intended to update size.
        #
        # With it: only fields the client explicitly included in the request
        # body appear in the dict:
        #   {"size": 450.0}
        # So only size is updated — the other fields are untouched.
        update_data = payload.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            # setattr(unit, "floor", 3) is exactly equivalent to
            # unit.floor = 3, but works when the field name is a variable
            # at runtime (as it is here, iterating over a dict).
            setattr(unit, field, value)

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        self.db.add(unit)
        await self.db.commit()
        await self.db.refresh(unit)

        return unit

    # =========================================================================
    # SECTION 8 — update_status
    # =========================================================================
    async def update_status(self, unit: Unit, payload: UnitStatusUpdate) -> Unit:
        """
        Change only a unit's occupancy status.

        Deliberately SEPARATE from update() — this is the method backing
        PATCH /units/{id}/status, which is open to ANY logged-in staff member,
        not just admins. It only ever touches the status column, never any
        structural field, so routine status bookkeeping can never become a
        backdoor for rewriting a unit's physical description or reassigning
        its ownership.

        Args:
            unit:    The existing Unit ORM instance to be modified.
            payload: A UnitStatusUpdate Pydantic model carrying the new
                     status. That schema contains ONLY status — no other
                     field exists on it to send.

        Returns:
            The updated Unit instance, reloaded from the database.
        """
        # Assigned directly rather than via model_dump/setattr: status is the
        # only field this schema carries and it is REQUIRED, so there is no
        # partial-update ambiguity to resolve here.
        unit.status = payload.status

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        self.db.add(unit)
        await self.db.commit()
        await self.db.refresh(unit)

        return unit

    # =========================================================================
    # SECTION 9 — delete
    # =========================================================================
    async def delete(self, unit: Unit) -> None:
        """
        Delete an existing Unit row from the database.

        The service layer is responsible for fetching the unit first and
        confirming it exists before calling this method. The repository does
        not re-fetch inside delete — single responsibility: just delete what
        it receives.

        On an IntegrityError this method rolls back the broken transaction and
        re-raises the SAME error, unmodified — it never raises HTTPException
        itself, so it stays usable outside an HTTP context. Translating the
        error into a client-facing message is the SERVICE layer's job.

        Args:
            unit: The Unit ORM instance to be deleted. Must already be
                loaded by the session (e.g. retrieved via get_by_id).
        """
        # Bare re-raise, same pattern as OwnerRepository.delete.
        #
        # No RESTRICT relationships currently point AT Unit, so this except
        # branch may never actually fire today. The pattern is kept consistent
        # with Owner deliberately, for when Charge/Payment eventually
        # reference Unit directly and a delete can genuinely be blocked.
        try:
            await self.db.delete(unit)
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise

    # =========================================================================
    # SECTION 10 — get_all
    # =========================================================================
    async def get_all(self) -> list[Unit]:
        """
        Fetch all Unit rows ordered by creation date, newest first.

        Returns:
            A list of Unit ORM instances ordered by created_at descending.
            Returns an empty list if no units exist.
        """
        result = await self.db.execute(select(Unit).order_by(Unit.created_at.desc()))
        # scalars().all() unpacks the result rows and returns a plain Python
        # list of Unit objects. Returns [] if the table is empty — never None.
        # order_by created_at desc — newest units appear first in the list.
        return list(result.scalars().all())
