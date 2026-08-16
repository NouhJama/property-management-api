"""
Unit router — the HTTP layer for unit CRUD.

Exposes the unit endpoints and connects incoming HTTP requests to the
UnitService. No business logic lives here — every handler only wires the
request to the service layer (via dependency injection for both assembly
and auth) and returns the result:

  POST   /api/v1/units                    — create a new unit.
  GET    /api/v1/units                    — list every unit, newest first.
  GET    /api/v1/units/{unit_id}          — fetch a single unit by id.
  PATCH  /api/v1/units/{unit_id}          — update a unit's structural fields.
  PATCH  /api/v1/units/{unit_id}/status   — update only a unit's status.
  DELETE /api/v1/units/{unit_id}          — delete a unit.

Permission split (the same shape as Owner's, with one addition):
  Create, delete, and STRUCTURAL update are ADMIN ONLY
  (get_current_active_superuser). Read and list are open to ANY
  authenticated staff member (get_current_user) — and so is the
  status-only update, which is the extra endpoint Owner has no equivalent
  of. Marking a unit under_maintenance or tenant_occupied is routine
  day-to-day bookkeeping that should not require an admin, while rewriting
  a unit's physical description or reassigning its ownership stays
  restricted.

Note on style: this file uses the Annotated[Type, Depends(...)] syntax
throughout — FastAPI's current recommendation, matching
get_unit_service in dependencies.py. routers/owners.py still uses the
older default-value form (service: OwnerService = Depends(...)); the two
are functionally identical, and migrating that file is a separate,
deliberate change not made here.

Architecture position:
  routers (HTTP, this file) → services (business logic) → repositories → models
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.core.dependencies import (
    get_current_active_superuser,
    get_current_user,
    get_unit_service,
)
from app.models.user import User
from app.schemas.unit import (
    UnitCreate,
    UnitResponse,
    UnitStatusUpdate,
    UnitUpdate,
)
from app.services.unit_service import UnitService

# =============================================================================
# SECTION 2 — Router object
# =============================================================================
# prefix "/units" combines with the "/api/v1" prefix added in main.py to
# produce /api/v1/units/... .
router = APIRouter(prefix="/units", tags=["Units"])


# =============================================================================
# SECTION 3 — POST /units
# =============================================================================
# 201 Created is the correct status for resource creation, matching the
# owner and register endpoints' convention.
@router.post(
    "",
    response_model=UnitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new unit",
)
async def create_unit(
    payload: UnitCreate,
    service: Annotated[UnitService, Depends(get_unit_service)],
    admin: Annotated[User, Depends(get_current_active_superuser)],
) -> UnitResponse:
    """
    Create a new unit — admin only.

    owner_id must be explicitly provided by the caller: the company's owner
    id for a unit that is not yet sold, or a real buyer's owner id if it
    has been. There is no default or automatic ownership anywhere in this
    flow — every unit states its owner plainly at creation time.

    status always starts as AVAILABLE regardless of input. UnitCreate has
    no status field for a client to send, and the service hardcodes it
    anyway as a second layer of defence.

    Args:
        payload: The validated unit-creation data from the client.
        service: The request-scoped UnitService.
        admin: The authenticated superuser — presence of this dependency is
            what enforces admin-only access (403 for non-admins). Its id is
            also recorded on the new row as created_by, the audit trail of
            which staff member created this unit. Never taken from the
            payload.

    Returns:
        The newly created unit, serialised through UnitResponse.

    Raises:
        HTTPException: 400 if a unit with this unit_number already exists
            (raised by the service).
    """
    return await service.create_unit(payload, created_by=admin.id)


# =============================================================================
# SECTION 4 — GET /units
# =============================================================================
@router.get(
    "",
    response_model=list[UnitResponse],
    summary="List all units",
)
async def list_units(
    service: Annotated[UnitService, Depends(get_unit_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[UnitResponse]:
    """
    Return every unit, newest first.

    Open to any logged-in staff member — deliberately NOT admin-only, since
    staff need to see unit records for day-to-day work.

    Args:
        service: The request-scoped UnitService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        All units ordered by created_at descending, serialised through
        UnitResponse. An empty list if no units exist.
    """
    return await service.get_all_units()


# =============================================================================
# SECTION 5 — GET /units/{unit_id}
# =============================================================================
@router.get(
    "/{unit_id}",
    response_model=UnitResponse,
    summary="Get a single unit by id",
)
async def get_unit(
    unit_id: int,
    service: Annotated[UnitService, Depends(get_unit_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UnitResponse:
    """
    Fetch a single unit by primary key.

    Open to any logged-in staff member, same reasoning as the list endpoint.

    Args:
        unit_id: The integer primary key of the target unit.
        service: The request-scoped UnitService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The matching unit, serialised through UnitResponse.

    Raises:
        HTTPException: 404 if no unit with this id exists (raised by the
            service).
    """
    return await service.get_unit_by_id(unit_id)


# =============================================================================
# SECTION 6 — PATCH /units/{unit_id}
# =============================================================================
@router.patch(
    "/{unit_id}",
    response_model=UnitResponse,
    summary="Update a unit's structural fields",
)
async def update_unit(
    unit_id: int,
    payload: UnitUpdate,
    service: Annotated[UnitService, Depends(get_unit_service)],
    admin: Annotated[User, Depends(get_current_active_superuser)],
) -> UnitResponse:
    """
    Update a unit's structural fields — admin only.

    Covers unit_number, floor, unit_type, bedrooms, size and owner_id.
    status is NOT changeable here: UnitUpdate has no status field at all —
    use the separate PATCH /units/{unit_id}/status endpoint, which is open
    to any staff member.

    FETCH-THEN-ACT pattern: this route fetches the unit first via
    get_unit_by_id (which raises 404 if it does not exist), THEN passes
    that fetched Unit object into update_unit. The service takes a Unit
    instance, not an id — existence is confirmed before any write happens.

    Args:
        unit_id: The integer primary key of the unit to update.
        payload: The validated partial-update data. Only fields the client
            actually sent are written (exclude_unset=True in the repository).
        service: The request-scoped UnitService.
        admin: The authenticated superuser — enforces admin-only access.

    Returns:
        The updated unit, serialised through UnitResponse.

    Raises:
        HTTPException: 404 if no unit with this id exists (raised during the
            fetch step); 400 if unit_number is being changed to one another
            unit already uses.
    """
    unit = await service.get_unit_by_id(unit_id)
    return await service.update_unit(unit, payload)


# =============================================================================
# SECTION 7 — PATCH /units/{unit_id}/status
# =============================================================================
@router.patch(
    "/{unit_id}/status",
    response_model=UnitResponse,
    summary="Update a unit's status",
)
async def update_unit_status(
    unit_id: int,
    payload: UnitStatusUpdate,
    service: Annotated[UnitService, Depends(get_unit_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> UnitResponse:
    """
    Update ONLY a unit's occupancy status.

    Open to ANY logged-in staff member, not just admins. This is the
    day-to-day operational endpoint — marking a unit under_maintenance,
    tenant_occupied, owner_occupied, or back to available. It is
    deliberately separate from the structural update endpoint above, which
    stays admin-only, so routine status bookkeeping can never become a
    backdoor for rewriting a unit's physical description or reassigning its
    ownership.

    No transition rules are enforced — any status can move to any other
    freely. UnitStatusUpdate carries status and nothing else, so no other
    field can be reached through this endpoint.

    Fetch-then-act, same as the structural update.

    Args:
        unit_id: The integer primary key of the unit to update.
        payload: The validated new status from the client.
        service: The request-scoped UnitService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The updated unit, serialised through UnitResponse.

    Raises:
        HTTPException: 404 if no unit with this id exists (raised by the
            service during the fetch step).
    """
    unit = await service.get_unit_by_id(unit_id)
    return await service.update_unit_status(unit, payload)


# =============================================================================
# SECTION 8 — DELETE /units/{unit_id}
# =============================================================================
# 204 No Content — the standard status for a successful DELETE that returns
# no response body.
@router.delete(
    "/{unit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a unit",
)
async def delete_unit(
    unit_id: int,
    service: Annotated[UnitService, Depends(get_unit_service)],
    admin: Annotated[User, Depends(get_current_active_superuser)],
) -> None:
    """
    Delete a unit — admin only.

    Fetch-then-act, same as update: the unit is loaded first (404 if
    missing), then handed to the service for deletion.

    If this unit is ever referenced by another record through a RESTRICT
    foreign key, PostgreSQL rejects the delete with an IntegrityError, which
    UnitService.delete_unit translates into a 409 Conflict. No existing
    table points at Unit that way today, so that branch cannot fire yet —
    the pattern is kept consistent with Owner deliberately, for when
    Charge/Payment eventually reference Unit directly.

    Args:
        unit_id: The integer primary key of the unit to delete.
        service: The request-scoped UnitService.
        admin: The authenticated superuser — enforces admin-only access.

    Raises:
        HTTPException: 404 if no unit with this id exists; 409 if the unit is
            still referenced by another record (both raised by the service).
    """
    unit = await service.get_unit_by_id(unit_id)
    await service.delete_unit(unit)
