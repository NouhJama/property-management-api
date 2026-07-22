"""
Owner repository — the single source of all SQLAlchemy queries for the Owner model.

This file is the ONLY place in the application that writes queries against
the `owners` table. No other layer (routers, services, schemas) may import
SQLAlchemy and query Owner directly.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Execute async SQLAlchemy 2.0 queries (select/insert/update/delete).
  - Return Owner ORM instances — never Pydantic schemas.

Out of scope for this file:
  - Business logic (e.g. hardcoding type=INDIVIDUAL on create — the service
    layer decides that, exactly as password hashing lives in the service).
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

from app.models.owner import Owner, OwnerType
from app.schemas.owner import OwnerUpdate


# =============================================================================
# SECTION 2 — OwnerRepository class
# =============================================================================
class OwnerRepository:
    """
    Data-access layer for the Owner model.

    This is the ONLY place in the app that writes SQLAlchemy queries for
    the Owner model. All other layers talk to this class; none of them
    import or use SQLAlchemy directly.

    Contract:
      - Receives an AsyncSession injected from get_db() — never creates one.
      - Returns Owner model instances — never Pydantic schemas or plain dicts.
      - Never contains business logic (no hardcoding type=INDIVIDUAL here —
        that belongs in the service, exactly like password hashing belongs
        in the service, not the repository).
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
    async def get_by_id(self, owner_id: int) -> Optional[Owner]:
        """
        Fetch a single Owner row by its primary key.

        Args:
            owner_id: The integer primary key of the target owner.

        Returns:
            The matching Owner ORM instance, or None if no row exists.
        """
        result = await self.db.execute(select(Owner).where(Owner.id == owner_id))
        # scalar_one_or_none() returns the Owner object or None.
        # If multiple rows somehow match (impossible with a primary-key
        # constraint, but guarded against here), it raises MultipleResultsFound
        # — a data-integrity protection that should never fire in practice.
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 4 — get_by_type
    # =========================================================================
    async def get_by_type(self, owner_type: OwnerType) -> list[Owner]:
        """
        Fetch all Owner rows matching a given type.

        Args:
            owner_type: The OwnerType to filter by (INDIVIDUAL or COMPANY).

        Returns:
            A list of Owner ORM instances of that type. Empty list if none.
        """
        result = await self.db.execute(select(Owner).where(Owner.type == owner_type))
        # Returns a LIST, not a single Owner — even though the partial unique
        # index guarantees at most one COMPANY row, this method is
        # general-purpose (also used for filtering "all individual owners",
        # which is naturally many).
        #
        # The caller (service) is responsible for handling the COMPANY case,
        # e.g. taking the first/only result when looking up the single company
        # row specifically.
        return list(result.scalars().all())

    # =========================================================================
    # SECTION 5 — create
    # =========================================================================
    async def create(
        self,
        name: str,
        type: OwnerType,
        phone: Optional[str] = None,
        email: Optional[str] = None,
        national_id: Optional[str] = None,
    ) -> Owner:
        """
        Insert a new Owner row into the database.

        Args:
            name:        The owner's full legal name or company name.
            type:        The OwnerType for this row. This is a REQUIRED
                         parameter here at the repository level — the
                         repository itself does not hardcode or decide the
                         value. The SERVICE layer is responsible for always
                         passing type=OwnerType.INDIVIDUAL when called from
                         the normal create-owner flow. This repository method
                         stays general purpose and does not encode that
                         business rule itself.
            phone:       Optional contact phone number.
            email:       Optional contact email address.
            national_id: Optional government-issued identification number.

        Returns:
            The newly created Owner instance, fully populated from the DB
            (id and created_at are present after refresh).
        """
        owner = Owner(
            name=name,
            type=type,
            phone=phone,
            email=email,
            national_id=national_id,
        )

        # add() — stages the object in the session's identity map.
        # The row does NOT exist in PostgreSQL yet at this point.
        self.db.add(owner)

        # commit() — opens a transaction, flushes the INSERT to PostgreSQL,
        # and commits. After this call the row exists in the DB and PostgreSQL
        # has assigned id and created_at.
        await self.db.commit()

        # refresh() — issues a SELECT to reload the row from the DB back onto
        # the Python object. Without this, owner.id and owner.created_at would
        # still be None on the Python side (they were None before commit).
        await self.db.refresh(owner)

        return owner

    # =========================================================================
    # SECTION 6 — update
    # =========================================================================
    async def update(self, owner: Owner, payload: OwnerUpdate) -> Owner:
        """
        Apply a partial update to an existing Owner row.

        The caller fetches the Owner first and passes it here together with
        an OwnerUpdate payload. Only fields the client actually sent are
        written — fields not included in the request body are left unchanged.

        Args:
            owner:   The existing Owner ORM instance to be modified.
            payload: An OwnerUpdate Pydantic model containing the fields to
                     change. Fields not supplied by the client are absent from
                     the model's __fields_set__ and are therefore skipped.

        Returns:
            The updated Owner instance, reloaded from the database.
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
            # setattr(owner, "name", "Nouh") is exactly equivalent to
            # owner.name = "Nouh", but works when the field name is a variable
            # at runtime (as it is here, iterating over a dict).
            setattr(owner, field, value)

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        self.db.add(owner)
        await self.db.commit()
        await self.db.refresh(owner)

        return owner

    # =========================================================================
    # SECTION 7 — delete
    # =========================================================================
    async def delete(self, owner: Owner) -> None:
        """
        Delete an existing Owner row from the database.

        The service layer is responsible for fetching the owner first and
        confirming it exists before calling this method. The repository does
        not re-fetch inside delete — single responsibility: just delete what
        it receives.

        PostgreSQL's RESTRICT on Unit.owner_id means this will raise
        IntegrityError if any Unit still references this owner. This method
        rolls back the broken transaction and re-raises the SAME
        IntegrityError, unmodified — it never raises HTTPException itself,
        so it stays usable outside an HTTP context. Translating the error
        into a client-facing message is the SERVICE layer's job.

        Args:
            owner: The Owner ORM instance to be deleted. Must already be
                loaded by the session (e.g. retrieved via get_by_id).
        """
        # delete() — marks the object for removal and issues DELETE on commit.
        #
        # PostgreSQL's RESTRICT on Unit.owner_id (established when we built
        # Unit) means this delete will FAIL with an IntegrityError if any Unit
        # still references this owner — the repository does not need to check
        # for that itself, the database enforces it.
        try:
            await self.db.delete(owner)
            await self.db.commit()
        except IntegrityError:
            await self.db.rollback()
            raise

    # =========================================================================
    # SECTION 8 — get_all
    # =========================================================================
    async def get_all(self) -> list[Owner]:
        """
        Fetch all Owner rows ordered by creation date, newest first.

        Returns:
            A list of Owner ORM instances ordered by created_at descending.
            Returns an empty list if no owners exist.
        """
        result = await self.db.execute(select(Owner).order_by(Owner.created_at.desc()))
        # scalars().all() unpacks the result rows and returns a plain Python
        # list of Owner objects. Returns [] if the table is empty — never None.
        # order_by created_at desc — newest owners appear first in the list.
        return list(result.scalars().all())
