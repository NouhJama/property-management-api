"""
Owner service — the business logic layer for all owner operations.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Enforce business rules (hardcoding the individual type on create,
    guarding against a missing company row).
  - Translate "not found" and misconfiguration conditions into
    HTTPExceptions with semantically correct status codes for the router.

Out of scope for this file:
  - SQL / SQLAlchemy queries (the repository's job).
  - HTTP request/response handling (the router's job).
  - Creating its own repository or database session (injected in).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
import logging

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

# Owner is imported as a type annotation only.
# The service never instantiates Owner() or writes queries — that is the
# repository's job. OwnerType, by contrast, is used as a VALUE here (to
# hardcode the individual type on create and to look up the company row).
from app.models.owner import Owner, OwnerType
from app.repositories.owner_repository import OwnerRepository
from app.schemas.owner import OwnerCreate, OwnerUpdate

logger = logging.getLogger(__name__)


# =============================================================================
# SECTION 2 — OwnerService class
# =============================================================================
class OwnerService:
    """
    Business logic layer for all owner operations.

    Sits between the router (HTTP) and the repository (database). Enforces
    all rules that determine whether an operation should be allowed and in
    what form:
      - Never writes SQL — always delegates to the repository.
      - Never handles HTTP request/response objects directly.
      - Raises HTTPException for the router to handle.
      - Receives its repository via the constructor — never creates its own
        (dependency injection pattern).
    """

    def __init__(self, repository: OwnerRepository) -> None:
        """
        Store the injected repository.

        Args:
            repository: The OwnerRepository this service delegates all
                database access to.
        """
        # Injected by dependencies.py — never instantiated directly
        # inside this class.
        self.repo = repository

    # =========================================================================
    # SECTION 3 — create_owner
    # =========================================================================
    async def create_owner(self, payload: OwnerCreate) -> Owner:
        """
        Create a new INDIVIDUAL owner.

        The type is ALWAYS hardcoded here — OwnerCreate has no type field at
        all, and this is the second layer of that same defensive pattern. No
        duplicate check on email is performed — Owner.email is intentionally
        not unique (a family may share one email across multiple owners).

        Args:
            payload: The validated owner-creation data from the client.

        Returns:
            The newly created Owner instance.
        """
        # type hardcoded OwnerType.INDIVIDUAL — never derived from payload.
        # OwnerCreate has no type field for the client to send in the first
        # place. The single type=COMPANY row is created exclusively by its
        # dedicated data migration, never through this method.
        return await self.repo.create(
            name=payload.name,
            type=OwnerType.INDIVIDUAL,
            phone=payload.phone,
            email=payload.email,
            national_id=payload.national_id,
        )

    # =========================================================================
    # SECTION 4 — get_company_owner
    # =========================================================================
    async def get_company_owner(self) -> Owner:
        """
        Fetch the single company owner row (Damal Heights).

        This is the default owner_id used when creating a Unit that hasn't
        been sold yet. Used by UnitService when creating new units.

        Logs a critical-level diagnostic message server-side if the company
        row is missing, but returns only a generic error to the client —
        internal system state must never be exposed via HTTPException detail.

        Returns:
            The company Owner instance.

        Raises:
            HTTPException: 500 if the company row is missing entirely.
        """
        companies = await self.repo.get_by_type(OwnerType.COMPANY)
        if not companies:
            # 500, not 404: this is not a "client made a mistake" error — a
            # missing company row means the system itself is misconfigured
            # (the seed migration wasn't run). The client calling this has no
            # way to fix it themselves. The concrete diagnostic goes to the
            # server log only; the client sees a generic message so no
            # internal implementation detail leaks over the wire.
            logger.critical(
                "Company owner row missing — check that the seed migration has been applied."
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="An unexpected error occurred. Please try again or contact support.",
            )
        # The partial unique index guarantees there is never MORE than one —
        # this method only needs to guard against zero, so taking the first
        # (and only) result is safe.
        return companies[0]

    # =========================================================================
    # SECTION 5 — get_owner_by_id
    # =========================================================================
    async def get_owner_by_id(self, owner_id: int) -> Owner:
        """
        Fetch an owner by primary key.

        Args:
            owner_id: The integer primary key of the target owner.

        Returns:
            The matching Owner instance.

        Raises:
            HTTPException: 404 if no owner with this id exists.
        """
        # Fetch by primary key; translate "not found" into a 404 for the router.
        owner = await self.repo.get_by_id(owner_id)
        if not owner:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Owner with id {owner_id} not found",
            )
        return owner

    # =========================================================================
    # SECTION 6 — update_owner
    # =========================================================================
    async def update_owner(self, owner: Owner, payload: OwnerUpdate) -> Owner:
        """
        Update an owner's contact details.

        The router fetches the owner first via get_owner_by_id and passes the
        Owner object here — fetch-then-act pattern, same as
        UserService.update_user. No email-uniqueness check needed since
        Owner.email is not unique.

        Args:
            owner: The existing Owner instance to update.
            payload: The validated partial-update data from the client.

        Returns:
            The updated Owner instance.
        """
        # Delegate straight to the repository — the repository uses
        # exclude_unset=True so only fields the client actually sent change.
        return await self.repo.update(owner, payload)

    # =========================================================================
    # SECTION 7 — delete_owner
    # =========================================================================
    async def delete_owner(self, owner: Owner) -> None:
        """
        Delete an owner.

        The router fetches the owner first via get_owner_by_id. Note:
        PostgreSQL will REJECT this delete with an IntegrityError if any
        Unit.owner_id still references this owner (the foreign key uses
        RESTRICT, not SET NULL) — the caller must reassign or delete those
        Units first. This method does not catch that error itself; it
        propagates up as an unhandled 500 for now. Revisit once UnitService
        exists, to decide whether to catch IntegrityError here and convert it
        to a clean 409 Conflict with a message like "Cannot delete an owner
        who still owns one or more units."

        Args:
            owner: The Owner instance to delete.
        """
        # Delegate the delete to the repository — the owner's existence was
        # already confirmed by the router via get_owner_by_id.
        # catches the database integrity error and translates to
        # user friendly message.
        try:
            await self.repo.delete(owner)
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot delete owner with id {owner.id}"
                        " There may be a one or more units still associated with this owner.",
            )

    # =========================================================================
    # SECTION 8 — get_all_owners
    # =========================================================================
    async def get_all_owners(self) -> list[Owner]:
        """
        Return all owners.

        Returns:
            A list of all Owner instances, newest first.
        """
        # No business rules to apply — delegate straight to the repository.
        return await self.repo.get_all()
