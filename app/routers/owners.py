"""
Owner router — the HTTP layer for owner CRUD.

Exposes the owner endpoints and connects incoming HTTP requests to the
OwnerService. No business logic lives here — every handler only wires the
request to the service layer (via dependency injection for both assembly
and auth) and returns the result:

  POST   /api/v1/owners             — create a new individual owner.
  GET    /api/v1/owners             — list every owner, newest first.
  GET    /api/v1/owners/{owner_id}  — fetch a single owner by id.
  PATCH  /api/v1/owners/{owner_id}  — update an owner's contact details.
  DELETE /api/v1/owners/{owner_id}  — delete an owner.

Permission split:
  Create, update, and delete are ADMIN ONLY
  (get_current_active_superuser). Read and list are open to ANY
  authenticated staff member (get_current_user) — owner contact and legal
  ownership data is sensitive, but staff need to see it for day-to-day
  collections and client work. Only mutating who legally owns what is
  restricted to admins.

Architecture position:
  routers (HTTP, this file) → services (business logic) → repositories → models
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from fastapi import APIRouter, Depends, status

from app.core.dependencies import (
    get_current_active_superuser,
    get_current_user,
    get_owner_service,
)
from app.models.user import User
from app.schemas.owner import OwnerCreate, OwnerResponse, OwnerUpdate
from app.services.owner_service import OwnerService

# =============================================================================
# SECTION 2 — Router object
# =============================================================================
# prefix "/owners" combines with the "/api/v1" prefix added in main.py to
# produce /api/v1/owners/... .
router = APIRouter(prefix="/owners", tags=["Owners"])


# =============================================================================
# SECTION 3 — POST /owners
# =============================================================================
# 201 Created is the correct status for resource creation, matching the
# register endpoint's convention.
@router.post(
    "",
    response_model=OwnerResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new owner",
)
async def create_owner(
    payload: OwnerCreate,
    service: OwnerService = Depends(get_owner_service),
    admin: User = Depends(get_current_active_superuser),
) -> OwnerResponse:
    """
    Create a new INDIVIDUAL owner — admin only.

    type is always hardcoded to individual by the service; there is no way
    for a client to create the company row through this endpoint.
    OwnerCreate has no type field for a client to send in the first place.

    Args:
        payload: The validated owner-creation data from the client.
        service: The request-scoped OwnerService.
        admin: The authenticated superuser — presence of this dependency is
            what enforces admin-only access (403 for non-admins).

    Returns:
        The newly created owner, serialised through OwnerResponse.
    """
    return await service.create_owner(payload)


# =============================================================================
# SECTION 4 — GET /owners
# =============================================================================
@router.get(
    "",
    response_model=list[OwnerResponse],
    summary="List all owners",
)
async def list_owners(
    service: OwnerService = Depends(get_owner_service),
    current_user: User = Depends(get_current_user),
) -> list[OwnerResponse]:
    """
    Return every owner, newest first.

    Open to any logged-in staff member — deliberately NOT admin-only, since
    staff need to see owner records for day-to-day work.

    Args:
        service: The request-scoped OwnerService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        All owners ordered by created_at descending, serialised through
        OwnerResponse. An empty list if no owners exist.
    """
    return await service.get_all_owners()


# =============================================================================
# SECTION 5 — GET /owners/{owner_id}
# =============================================================================
@router.get(
    "/{owner_id}",
    response_model=OwnerResponse,
    summary="Get a single owner by id",
)
async def get_owner(
    owner_id: int,
    service: OwnerService = Depends(get_owner_service),
    current_user: User = Depends(get_current_user),
) -> OwnerResponse:
    """
    Fetch a single owner by primary key.

    Open to any logged-in staff member, same reasoning as the list endpoint.

    Args:
        owner_id: The integer primary key of the target owner.
        service: The request-scoped OwnerService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The matching owner, serialised through OwnerResponse.

    Raises:
        HTTPException: 404 if no owner with this id exists (raised by the
            service).
    """
    return await service.get_owner_by_id(owner_id)


# =============================================================================
# SECTION 6 — PATCH /owners/{owner_id}
# =============================================================================
@router.patch(
    "/{owner_id}",
    response_model=OwnerResponse,
    summary="Update an owner's contact details",
)
async def update_owner(
    owner_id: int,
    payload: OwnerUpdate,
    service: OwnerService = Depends(get_owner_service),
    admin: User = Depends(get_current_active_superuser),
) -> OwnerResponse:
    """
    Update an owner's name, phone, email, or national_id — admin only.

    FETCH-THEN-ACT pattern: this route fetches the owner first via
    get_owner_by_id (which raises 404 if it does not exist), THEN passes
    that fetched Owner object into update_owner. The service takes an Owner
    instance, not an id — existence is confirmed before any write happens.

    type can never be changed via this endpoint: OwnerUpdate has no type
    field at all, so an individual can never be promoted to the company row.

    Args:
        owner_id: The integer primary key of the owner to update.
        payload: The validated partial-update data. Only fields the client
            actually sent are written (exclude_unset=True in the repository).
        service: The request-scoped OwnerService.
        admin: The authenticated superuser — enforces admin-only access.

    Returns:
        The updated owner, serialised through OwnerResponse.

    Raises:
        HTTPException: 404 if no owner with this id exists (raised by the
            service during the fetch step).
    """
    owner = await service.get_owner_by_id(owner_id)
    return await service.update_owner(owner, payload)


# =============================================================================
# SECTION 7 — DELETE /owners/{owner_id}
# =============================================================================
# 204 No Content — the standard status for a successful DELETE that returns
# no response body.
@router.delete(
    "/{owner_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an owner",
)
async def delete_owner(
    owner_id: int,
    service: OwnerService = Depends(get_owner_service),
    admin: User = Depends(get_current_active_superuser),
) -> None:
    """
    Delete an owner — admin only.

    Fetch-then-act, same as update: the owner is loaded first (404 if
    missing), then handed to the service for deletion.

    Deleting an owner who still has Units pointing at them fails: the
    Unit.owner_id foreign key uses PostgreSQL's RESTRICT, so the database
    rejects the delete with an IntegrityError, which
    OwnerService.delete_owner translates into a 409 Conflict. Reassign or
    delete those Units first.

    Args:
        owner_id: The integer primary key of the owner to delete.
        service: The request-scoped OwnerService.
        admin: The authenticated superuser — enforces admin-only access.

    Raises:
        HTTPException: 404 if no owner with this id exists; 409 if the owner
            still owns one or more units (both raised by the service).
    """
    owner = await service.get_owner_by_id(owner_id)
    await service.delete_owner(owner)
