"""
Unit service — the business logic layer for all unit operations.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Enforce business rules (hardcoding the initial status on create, guarding
    against a duplicate unit_number).
  - Translate "not found", "already exists" and constraint-violation conditions
    into HTTPExceptions with semantically correct status codes for the router.

Out of scope for this file:
  - SQL / SQLAlchemy queries (the repository's job).
  - HTTP request/response handling (the router's job).
  - Creating its own repository or database session (injected in).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

# Unit is imported as a type annotation only.
# The service never instantiates Unit() or writes queries — that is the
# repository's job. UnitStatus, by contrast, is used as a VALUE here (to
# hardcode the initial status on create).
from app.models.unit import Unit, UnitStatus
from app.repositories.unit_repository import UnitRepository
from app.schemas.unit import UnitCreate, UnitStatusUpdate, UnitUpdate


# =============================================================================
# SECTION 2 — UnitService class
# =============================================================================
class UnitService:
    """
    Business logic layer for all unit operations.

    Sits between the router (HTTP) and the repository (database). Enforces
    all rules that determine whether an operation should be allowed and in
    what form:
      - Never writes SQL — always delegates to the repository.
      - Never handles HTTP request/response objects directly.
      - Raises HTTPException for the router to handle.
      - Receives its repository via the constructor — never creates its own
        (dependency injection pattern).
    """

    def __init__(self, repository: UnitRepository) -> None:
        """
        Store the injected repository.

        Args:
            repository: The UnitRepository this service delegates all
                database access to.
        """
        # Injected by dependencies.py — never instantiated directly
        # inside this class.
        self.repo = repository

    # =========================================================================
    # SECTION 3 — create_unit
    # =========================================================================
    async def create_unit(self, payload: UnitCreate, created_by: int) -> Unit:
        """
        Create a new unit.

        Checks that unit_number is not already taken before inserting, so a
        clean 400 is returned instead of a raw database IntegrityError from the
        unique constraint. The status is ALWAYS hardcoded to
        UnitStatus.AVAILABLE regardless of any input — UnitCreate has no status
        field at all, and this is the second layer of that same defensive
        pattern.

        Args:
            payload: The validated unit-creation data from the client.
            created_by: The id of the authenticated admin creating this unit,
                passed down by the router from get_current_active_superuser.
                A required parameter here — every unit created through the API
                is attributed to the admin who made the request, and it is
                never read from the client payload (UnitCreate has no such
                field).

        Returns:
            The newly created Unit instance.

        Raises:
            HTTPException: 400 if a unit with this unit_number already exists.
        """
        # Step 1 — unit_number is unique building-wide, so reject a duplicate
        # here with a clear 400 rather than letting the database's unique
        # constraint surface as an unhandled 500.
        existing = await self.repo.get_by_unit_number(payload.unit_number)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"A unit with number {payload.unit_number} already exists",
            )

        # Step 2 — status hardcoded UnitStatus.AVAILABLE — never derived from
        # payload. UnitCreate has no status field for the client to send in the
        # first place; a brand-new unit is always available until it is
        # explicitly moved on via the status endpoint.
        return await self.repo.create(
            unit_number=payload.unit_number,
            floor=payload.floor,
            unit_type=payload.unit_type,
            owner_id=payload.owner_id,
            bedrooms=payload.bedrooms,
            size=payload.size,
            status=UnitStatus.AVAILABLE,
            created_by=created_by,
        )

    # =========================================================================
    # SECTION 4 — get_unit_by_id
    # =========================================================================
    async def get_unit_by_id(self, unit_id: int) -> Unit:
        """
        Fetch a unit by primary key.

        Args:
            unit_id: The integer primary key of the target unit.

        Returns:
            The matching Unit instance.

        Raises:
            HTTPException: 404 if no unit with this id exists.
        """
        # Fetch by primary key; translate "not found" into a 404 for the router.
        unit = await self.repo.get_by_id(unit_id)
        if not unit:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unit with id {unit_id} not found",
            )
        return unit

    # =========================================================================
    # SECTION 5 — update_unit
    # =========================================================================
    async def update_unit(self, unit: Unit, payload: UnitUpdate) -> Unit:
        """
        Update a unit's STRUCTURAL fields.

        Covers unit_number, floor, unit_type, bedrooms, size and owner_id.
        Admin only — enforced at the router level via
        Depends(get_current_active_superuser), not here. Status is deliberately
        NOT updatable through this method; it has its own endpoint and its own
        looser permission (see update_unit_status).

        The router fetches the unit first via get_unit_by_id and passes the
        Unit object here — fetch-then-act pattern, same as
        OwnerService.update_owner.

        Args:
            unit: The existing Unit instance to update.
            payload: The validated partial-update data from the client.

        Returns:
            The updated Unit instance.

        Raises:
            HTTPException: 400 if unit_number is being changed to one that
                another unit already uses.
        """
        # Step 1 — only check for a duplicate if unit_number is ACTUALLY being
        # changed to a different value. Two conditions must BOTH be true: the
        # client sent a unit_number at all (not None, e.g. they may only be
        # updating floor), AND it differs from the current value (not a no-op
        # resubmit of the same number). The database check itself
        # (get_by_unit_number) only ever runs zero or one times per call — this
        # condition is a cheap in-memory gate deciding whether that one real
        # check is needed, not a duplicate of it.
        if payload.unit_number and payload.unit_number != unit.unit_number:
            existing = await self.repo.get_by_unit_number(payload.unit_number)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"A unit with number {payload.unit_number} already exists",
                )

        # Step 2 — delegate to the repository — the repository uses
        # exclude_unset=True so only fields the client actually sent change.
        return await self.repo.update(unit, payload)

    # =========================================================================
    # SECTION 6 — update_unit_status
    # =========================================================================
    async def update_unit_status(self, unit: Unit, payload: UnitStatusUpdate) -> Unit:
        """
        Update ONLY a unit's occupancy status.

        Open to ANY logged-in staff member, not just admins — enforced at the
        router level via Depends(get_current_user). This is the day-to-day
        operational path, kept separate from the admin-only structural update.

        No transition rules are enforced — any status can move to any other
        status freely. This is a deliberate decision: no business requirement
        currently demands otherwise, and once Charge/Payment exist, status may
        end up derived automatically from real events rather than manually set
        at all — premature transition rules could conflict with that future
        design.

        The router fetches the unit first via get_unit_by_id and passes the
        Unit object here — fetch-then-act pattern, same as update_unit.

        Args:
            unit: The existing Unit instance to update.
            payload: The validated new status from the client.

        Returns:
            The updated Unit instance.
        """
        # No rules to apply — delegate straight to the repository, which only
        # ever touches the status column.
        return await self.repo.update_status(unit, payload)

    # =========================================================================
    # SECTION 7 — delete_unit
    # =========================================================================
    async def delete_unit(self, unit: Unit) -> None:
        """
        Delete a unit.

        The router fetches the unit first via get_unit_by_id — fetch-then-act.
        If any other record still references this unit, PostgreSQL rejects the
        delete with an IntegrityError; this method catches it and translates it
        into a clean 409 Conflict for the client, rather than letting a raw
        database error propagate as an unhandled 500.

        unit.id is captured into a plain variable BEFORE the delete is
        attempted, because rollback() (triggered inside the repository on
        failure) expires the unit object — reading unit.id directly inside the
        except block would trigger SQLAlchemy's synchronous lazy-load path,
        which raises MissingGreenlet since there's no active async bridge at
        that point. This is exactly the bug found and fixed in
        OwnerService.delete_owner.

        Args:
            unit: The Unit instance to delete.
        """
        # Capture the unit id for the IntegrityError message, since-
        # the unit object will be expired after a failed delete attempt on-
        # the try block.
        unit_id = unit.id
        try:
            await self.repo.delete(unit)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot delete unit with id {unit_id}. "
                    "It may still be referenced by other records."
                ),
            )

    # =========================================================================
    # SECTION 8 — get_all_units
    # =========================================================================
    async def get_all_units(self) -> list[Unit]:
        """
        Return all units.

        Returns:
            A list of all Unit instances, newest first.
        """
        # No business rules to apply — delegate straight to the repository.
        return await self.repo.get_all()
