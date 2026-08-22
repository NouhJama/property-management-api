"""
Tenant service — the business logic layer for all tenant operations.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Attribute every created tenant to the authenticated staff member
    (created_by is never taken from client input).
  - Translate "not found" and database-constraint conditions into
    HTTPExceptions with semantically correct status codes for the router.

Out of scope for this file:
  - SQL / SQLAlchemy queries (the repository's job).
  - HTTP request/response handling (the router's job).
  - Creating its own repository or database session (injected in).

Genuinely simpler than OwnerService: Tenant has no `type` column, so there
is nothing to hardcode on create, and no company-placeholder row to protect.
Tenant also has no unique field, so unlike UnitService there is no
duplicate check on create or update either.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

# Tenant is imported as a TYPE ANNOTATION only. The service never
# instantiates Tenant() and never writes queries — that is the repository's
# job. Unlike OwnerService (which uses OwnerType as a value), there is no
# enum here to hardcode, because Tenant has no type concept at all.
from app.models.tenant import Tenant
from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import TenantCreate, TenantUpdate


# =============================================================================
# SECTION 2 — TenantService class
# =============================================================================
class TenantService:
    """
    Business logic layer for all tenant operations.

    Sits between the router (HTTP) and the repository (database). Enforces
    all rules that determine whether an operation should be allowed and in
    what form:
      - Never writes SQL — always delegates to the repository.
      - Never handles HTTP request/response objects directly.
      - Raises HTTPException for the router to handle.
      - Receives its repository via the constructor — never creates its own
        (dependency injection pattern).
    """

    def __init__(self, repository: TenantRepository) -> None:
        """
        Store the injected repository.

        Args:
            repository: The TenantRepository this service delegates all
                database access to.
        """
        # Injected by dependencies.py — never instantiated directly
        # inside this class.
        self.repo = repository

    # =========================================================================
    # SECTION 3 — create_tenant
    # =========================================================================
    async def create_tenant(self, payload: TenantCreate, created_by: int) -> Tenant:
        """
        Create a new tenant.

        Unlike OwnerService.create_owner there is no field to hardcode here —
        Tenant has no type column — and no duplicate check is performed,
        because no field on Tenant carries a uniqueness constraint (unlike
        UnitService's unit_number check). Every row created here is a genuine
        individual renter.

        Args:
            payload: The validated tenant-creation data from the client.
            created_by: The id of the authenticated staff member creating this
                tenant, passed down by the router. A required parameter here —
                every tenant created through the API is attributed to the
                staff member who made the request, and it is never read from
                the client payload (TenantCreate has no such field).

        Returns:
            The newly created Tenant instance.
        """
        # Straight delegation — no business rule to apply before the insert.
        # created_by comes from the authenticated user, never from payload.
        return await self.repo.create(
            name=payload.name,
            phone=payload.phone,
            email=payload.email,
            national_id=payload.national_id,
            created_by=created_by,
        )

    # =========================================================================
    # SECTION 4 — get_tenant_by_id
    # =========================================================================
    async def get_tenant_by_id(self, tenant_id: int) -> Tenant:
        """
        Fetch a tenant by primary key.

        Args:
            tenant_id: The integer primary key of the target tenant.

        Returns:
            The matching Tenant instance.

        Raises:
            HTTPException: 404 if no tenant with this id exists.
        """
        # Fetch by primary key; translate "not found" into a 404 for the router.
        tenant = await self.repo.get_by_id(tenant_id)
        if not tenant:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Tenant with id {tenant_id} not found",
            )
        return tenant

    # =========================================================================
    # SECTION 5 — update_tenant
    # =========================================================================
    async def update_tenant(self, tenant: Tenant, payload: TenantUpdate) -> Tenant:
        """
        Update a tenant's contact details.

        The router fetches the tenant first via get_tenant_by_id and passes
        the Tenant object here — fetch-then-act pattern, same as
        OwnerService.update_owner. No uniqueness checks are needed, unlike
        UnitService.update_unit's unit_number check: no field on Tenant is
        unique.

        Args:
            tenant: The existing Tenant instance to update.
            payload: The validated partial-update data from the client.

        Returns:
            The updated Tenant instance.
        """
        # Delegate straight to the repository — the repository uses
        # exclude_unset=True so only fields the client actually sent change.
        return await self.repo.update(tenant, payload)

    # =========================================================================
    # SECTION 6 — delete_tenant
    # =========================================================================
    async def delete_tenant(self, tenant: Tenant) -> None:
        """
        Delete a tenant.

        The router fetches the tenant first via get_tenant_by_id — same
        fetch-then-act pattern as OwnerService.delete_owner.

        tenant.id is captured into a plain variable BEFORE the delete is
        attempted, because rollback() (triggered inside the repository on
        failure) expires the tenant object — reading tenant.id directly
        inside the except block would trigger SQLAlchemy's synchronous
        lazy-load path, which raises MissingGreenlet since there's no active
        async bridge at that point.

        Unlike Owner, no RESTRICT foreign key currently references Tenant, so
        the IntegrityError branch is not realistically triggerable today. It
        is kept for consistency with OwnerService.delete_owner and
        UnitService.delete_unit, and for when Charge/Payment eventually
        reference Tenant directly.

        Args:
            tenant: The Tenant instance to delete.
        """
        # Capture the tenant id for the IntegrityError message BEFORE the
        # delete attempt — the object is expired by rollback() on failure.
        tenant_id = tenant.id
        try:
            await self.repo.delete(tenant)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Cannot delete tenant with id {tenant_id}. "
                    "They may still be referenced by other records."
                ),
            )

    # =========================================================================
    # SECTION 7 — get_all_tenants
    # =========================================================================
    async def get_all_tenants(self) -> list[Tenant]:
        """
        Return all tenants.

        Returns:
            A list of all Tenant instances, newest first.
        """
        # No business rules to apply — delegate straight to the repository.
        return await self.repo.get_all()
