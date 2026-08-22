"""
Tenant router — the HTTP layer for tenant CRUD.

Exposes the tenant endpoints and connects incoming HTTP requests to the
TenantService. No business logic lives here — every handler only wires the
request to the service layer (via dependency injection for both assembly
and auth) and returns the result:

  POST   /api/v1/tenants              — create a new tenant.
  GET    /api/v1/tenants              — list every tenant, newest first.
  GET    /api/v1/tenants/{tenant_id}  — fetch a single tenant by id.
  PATCH  /api/v1/tenants/{tenant_id}  — update a tenant's contact details.
  DELETE /api/v1/tenants/{tenant_id}  — delete a tenant.

Permission split (deliberately DIFFERENT from Owner and Unit):
  Create, read, list and update are open to ANY authenticated staff member
  (get_current_user). ONLY delete is admin-only
  (get_current_active_superuser).

  Rationale: tenant creation and updates are routine, frequent, front-desk
  operations — a walk-in renter asking about an available unit — that need
  to happen quickly without requiring an admin every time. Deletion is the
  one irreversible, infrequent operation here, and stays admin-only for the
  same reason delete is restricted elsewhere in this project.

  This is the first resource where staff can create and update. Owner and
  Unit both put create and structural update behind the superuser guard;
  that difference here is intentional, not an oversight.

Note on style: this file uses the Annotated[Type, Depends(...)] syntax
throughout — FastAPI's current recommendation, matching units.py and
get_tenant_service in dependencies.py.

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
    get_tenant_service,
)
from app.models.user import User
from app.schemas.tenant import TenantCreate, TenantResponse, TenantUpdate
from app.services.tenant_service import TenantService

# =============================================================================
# SECTION 2 — Router object
# =============================================================================
# prefix "/tenants" combines with the "/api/v1" prefix added in main.py to
# produce /api/v1/tenants/... .
router = APIRouter(prefix="/tenants", tags=["Tenants"])


# =============================================================================
# SECTION 3 — POST /tenants
# =============================================================================
# 201 Created is the correct status for resource creation, matching the
# owner, unit and register endpoints' convention.
@router.post(
    "",
    response_model=TenantResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new tenant",
)
async def create_tenant(
    payload: TenantCreate,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TenantResponse:
    """
    Create a new tenant — open to any logged-in staff member.

    Deliberately NOT admin-only, unlike POST /units and POST /owners.
    Registering a tenant is a routine front-desk operation that happens
    while a prospective renter is standing at the counter, and gating it
    behind an admin would stall ordinary work for no security benefit.

    created_by is taken from the authenticated staff member making the
    request, never from the payload — TenantCreate has no such field, and
    the service requires the id as an explicit argument.

    Args:
        payload: The validated tenant-creation data from the client.
        service: The request-scoped TenantService.
        current_user: The authenticated User resolved from the JWT. Its id
            is recorded on the new row as created_by, the audit trail of
            which staff member registered this tenant.

    Returns:
        The newly created tenant, serialised through TenantResponse.
    """
    return await service.create_tenant(payload, created_by=current_user.id)


# =============================================================================
# SECTION 4 — GET /tenants
# =============================================================================
@router.get(
    "",
    response_model=list[TenantResponse],
    summary="List all tenants",
)
async def list_tenants(
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[TenantResponse]:
    """
    Return every tenant, newest first.

    Open to any logged-in staff member — staff need to see tenant records
    for day-to-day work, same reasoning as the owner and unit list
    endpoints.

    Args:
        service: The request-scoped TenantService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        All tenants ordered by created_at descending, serialised through
        TenantResponse. An empty list if no tenants exist.
    """
    return await service.get_all_tenants()


# =============================================================================
# SECTION 5 — GET /tenants/{tenant_id}
# =============================================================================
@router.get(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Get a single tenant by id",
)
async def get_tenant(
    tenant_id: int,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TenantResponse:
    """
    Fetch a single tenant by primary key.

    Open to any logged-in staff member, same reasoning as the list endpoint.

    Args:
        tenant_id: The integer primary key of the target tenant.
        service: The request-scoped TenantService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The matching tenant, serialised through TenantResponse.

    Raises:
        HTTPException: 404 if no tenant with this id exists (raised by the
            service).
    """
    return await service.get_tenant_by_id(tenant_id)


# =============================================================================
# SECTION 6 — PATCH /tenants/{tenant_id}
# =============================================================================
@router.patch(
    "/{tenant_id}",
    response_model=TenantResponse,
    summary="Update a tenant's contact details",
)
async def update_tenant(
    tenant_id: int,
    payload: TenantUpdate,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> TenantResponse:
    """
    Update a tenant's contact details — open to any logged-in staff member.

    Covers name, phone, email and national_id. Correcting a mistyped phone
    number or updating an email is routine front-desk work, so this is
    deliberately not admin-only, unlike the structural update on Unit.

    FETCH-THEN-ACT pattern: this route fetches the tenant first via
    get_tenant_by_id (which raises 404 if it does not exist), THEN passes
    that fetched Tenant object into update_tenant. The service takes a
    Tenant instance, not an id — existence is confirmed before any write
    happens.

    Only the fields the client actually sent are written
    (exclude_unset=True in the repository); omitted fields are left
    untouched.

    Args:
        tenant_id: The integer primary key of the tenant to update.
        payload: The validated partial-update data from the client.
        service: The request-scoped TenantService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The updated tenant, serialised through TenantResponse.

    Raises:
        HTTPException: 404 if no tenant with this id exists (raised during
            the fetch step).
    """
    tenant = await service.get_tenant_by_id(tenant_id)
    return await service.update_tenant(tenant, payload)


# =============================================================================
# SECTION 7 — DELETE /tenants/{tenant_id}
# =============================================================================
# 204 No Content — the standard status for a successful DELETE that returns
# no response body.
@router.delete(
    "/{tenant_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a tenant",
)
async def delete_tenant(
    tenant_id: int,
    service: Annotated[TenantService, Depends(get_tenant_service)],
    admin: Annotated[User, Depends(get_current_active_superuser)],
) -> None:
    """
    Delete a tenant — admin only, unlike every other route on this router.

    Deletion is the one irreversible operation here, and it is infrequent,
    so it keeps the superuser guard that create and update deliberately do
    not have.

    Fetch-then-act, same as update: the tenant is loaded first (404 if
    missing), then handed to the service for deletion.

    If this tenant is ever referenced by another record through a RESTRICT
    foreign key, PostgreSQL rejects the delete with an IntegrityError, which
    TenantService.delete_tenant translates into a 409 Conflict. No existing
    table points at Tenant that way today, so that branch cannot fire yet —
    the pattern is kept consistent with Owner and Unit deliberately, for
    when Charge/Payment eventually reference Tenant directly.

    Args:
        tenant_id: The integer primary key of the tenant to delete.
        service: The request-scoped TenantService.
        admin: The authenticated superuser — presence of this dependency is
            what enforces admin-only access (403 for non-admins).

    Raises:
        HTTPException: 404 if no tenant with this id exists; 409 if the
            tenant is still referenced by another record (both raised by the
            service).
    """
    tenant = await service.get_tenant_by_id(tenant_id)
    await service.delete_tenant(tenant)
