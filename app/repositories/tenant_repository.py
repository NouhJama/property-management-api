"""
Tenant repository — the single source of all SQLAlchemy queries for the Tenant model.

This file is the ONLY place in the application that writes queries against
the `tenants` table. No other layer (routers, services, schemas) may import
SQLAlchemy and query Tenant directly.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Execute async SQLAlchemy 2.0 queries (select/insert/update/delete).
  - Return Tenant ORM instances — never Pydantic schemas.

Out of scope for this file:
  - Business logic (the service decides what a valid tenant operation is).
  - Raising HTTP exceptions (the service layer raises those).
  - Creating its own database sessions (the session is always injected).

Genuinely simpler than OwnerRepository: Tenant has no `type` column, so there
is no get_by_type() equivalent here and nothing for the service to hardcode
on create.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.tenant import Tenant
from app.schemas.tenant import TenantUpdate


# =============================================================================
# SECTION 2 — TenantRepository class
# =============================================================================
class TenantRepository:
    """
    Data-access layer for the Tenant model.

    This is the ONLY place in the app that writes SQLAlchemy queries for
    the Tenant model. All other layers talk to this class; none of them
    import or use SQLAlchemy directly.

    Contract:
      - Receives an AsyncSession injected from get_db() — never creates one.
      - Returns Tenant model instances — never Pydantic schemas or plain dicts.
      - Never contains business logic (exactly as password hashing belongs in
        the service, not the repository).
      - Never raises HTTP exceptions — only database-level errors propagate,
        re-raised unmodified for the service to translate into client-facing
        responses.
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
    async def get_by_id(self, tenant_id: int) -> Optional[Tenant]:
        """
        Fetch a single Tenant row by its primary key.

        Args:
            tenant_id: The integer primary key of the target tenant.

        Returns:
            The matching Tenant ORM instance, or None if no row exists.
        """
        # Used for GET /tenants/{id} and the fetch-then-act step of every
        # PATCH/DELETE endpoint — answers "does THIS tenant exist by its real
        # id."
        result = await self.db.execute(select(Tenant).where(Tenant.id == tenant_id))
        # scalar_one_or_none() returns the Tenant object or None.
        # If multiple rows somehow match (impossible with a primary-key
        # constraint, but guarded against here), it raises MultipleResultsFound
        # — a data-integrity protection that should never fire in practice.
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 4 — create
    # =========================================================================
    async def create(
        self,
        name: str,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        national_id: Optional[str] = None,
        created_by: Optional[int] = None,
    ) -> Tenant:
        """
        Insert a new Tenant row into the database.

        Args:
            name:        The tenant's full legal name.
            phone:       Optional contact phone number, already normalised to
                         E.164 by the schema layer.
            email:       Optional contact email address.
            national_id: Optional government-issued identification number.
            created_by:  Optional id of the User (staff member) who created
                         this row — a pure audit trail. Optional HERE so this
                         method stays general purpose for non-HTTP callers
                         (scripts, data migrations). The SERVICE is
                         responsible for always passing the authenticated
                         staff member's id when called from the real creation
                         flow.

        Returns:
            The newly created Tenant instance, fully populated from the DB
            (id and created_at are present after refresh).
        """
        tenant = Tenant(
            name=name,
            phone=phone,
            email=email,
            national_id=national_id,
            created_by=created_by,
        )

        # add() — stages the object in the session's identity map.
        # The row does NOT exist in PostgreSQL yet at this point.
        self.db.add(tenant)

        # commit() — opens a transaction, flushes the INSERT to PostgreSQL,
        # and commits. After this call the row exists in the DB and PostgreSQL
        # has assigned id and created_at.
        #
        # The rollback handling follows UnitRepository.create()'s pattern.
        # Unlike Unit, Tenant has no foreign key that a client can realistically
        # break here: created_by is nullable with ON DELETE SET NULL, so a bad
        # value would be a bug elsewhere in the app rather than a normal
        # client-triggerable failure. The guard is kept anyway for consistency
        # with the rest of the codebase, and because without a rollback ANY
        # IntegrityError would leave the session's transaction broken for the
        # remainder of the request — every later query on it would fail too.
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
        # the Python object. Without this, tenant.id and tenant.created_at
        # would still be None on the Python side (they were None before
        # commit).
        await self.db.refresh(tenant)

        return tenant

    # =========================================================================
    # SECTION 5 — update
    # =========================================================================
    async def update(self, tenant: Tenant, payload: TenantUpdate) -> Tenant:
        """
        Apply a partial update to an existing Tenant row.

        The caller fetches the Tenant first and passes it here together with
        a TenantUpdate payload. Only fields the client actually sent are
        written — fields not included in the request body are left unchanged.

        Args:
            tenant:  The existing Tenant ORM instance to be modified.
            payload: A TenantUpdate Pydantic model containing the fields to
                     change. Fields not supplied by the client are absent from
                     the model's __fields_set__ and are therefore skipped.

        Returns:
            The updated Tenant instance, reloaded from the database.
        """
        # exclude_unset=True is the correct partial-update pattern.
        #
        # Without it: model_dump() would include every field — even those the
        # client never sent — serialised to their default (usually None):
        #   {"name": None, "phone": None, "email": "a@b.com", "national_id": None}
        # That would overwrite name, phone and national_id with None even
        # though the client only intended to update email.
        #
        # With it: only fields the client explicitly included in the request
        # body appear in the dict:
        #   {"email": "a@b.com"}
        # So only email is updated — the other fields are untouched.
        update_data = payload.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            # setattr(tenant, "name", "Nouh") is exactly equivalent to
            # tenant.name = "Nouh", but works when the field name is a variable
            # at runtime (as it is here, iterating over a dict).
            setattr(tenant, field, value)

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        self.db.add(tenant)
        await self.db.commit()
        await self.db.refresh(tenant)

        return tenant

    # =========================================================================
    # SECTION 6 — delete
    # =========================================================================
    async def delete(self, tenant: Tenant) -> None:
        """
        Delete an existing Tenant row from the database.

        The service layer is responsible for fetching the tenant first and
        confirming it exists before calling this method. The repository does
        not re-fetch inside delete — single responsibility: just delete what
        it receives.

        Args:
            tenant: The Tenant ORM instance to be deleted. Must already be
                loaded by the session (e.g. retrieved via get_by_id).
        """
        # delete() — marks the object for removal and issues DELETE on commit.
        #
        # Bare re-raise on IntegrityError, the same pattern as Owner and Unit —
        # the repository never raises HTTPException, so it stays usable outside
        # an HTTP context, and translating the error into a client-facing
        # message is the SERVICE layer's job.
        #
        # Unlike OwnerRepository.delete(), no RESTRICT relationship currently
        # points AT Tenant, so this may never actually fire today. The pattern
        # is kept consistent for when Charge/Payment eventually reference
        # Tenant directly.
        try:
            await self.db.delete(tenant)
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise

    # =========================================================================
    # SECTION 7 — get_all
    # =========================================================================
    async def get_all(self) -> list[Tenant]:
        """
        Fetch all Tenant rows ordered by creation date, newest first.

        Returns:
            A list of Tenant ORM instances ordered by created_at descending.
            Returns an empty list if no tenants exist.
        """
        result = await self.db.execute(select(Tenant).order_by(Tenant.created_at.desc()))
        # scalars().all() unpacks the result rows and returns a plain Python
        # list of Tenant objects. Returns [] if the table is empty — never
        # None. order_by created_at desc — newest tenants appear first.
        return list(result.scalars().all())
