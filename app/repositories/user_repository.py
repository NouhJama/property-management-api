"""
User repository — the single source of all SQLAlchemy queries for the User model.

This file is the ONLY place in the application that writes queries against
the `users` table. No other layer (routers, services, schemas) may import
SQLAlchemy and query User directly.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Execute async SQLAlchemy 2.0 queries (select/insert/update/delete).
  - Return User ORM instances — never Pydantic schemas.

Out of scope for this file:
  - Business logic (e.g. deciding whether a user is allowed to do something).
  - Password hashing (the service layer does that before calling create/update).
  - Raising HTTP exceptions (the service layer raises those).
  - Creating its own database sessions (the session is always injected).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User
from app.schemas.user import UserUpdate


# =============================================================================
# SECTION 2 — UserRepository class
# =============================================================================
class UserRepository:
    """
    Data-access layer for the User model.

    This is the ONLY place in the app that writes SQLAlchemy queries for
    the User model. All other layers talk to this class; none of them
    import or use SQLAlchemy directly.

    Contract:
      - Receives an AsyncSession injected from get_db() — never creates one.
      - Returns User model instances — never Pydantic schemas or plain dicts.
      - Never contains business logic or password hashing.
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
    async def get_by_id(self, user_id: int) -> Optional[User]:
        """
        Fetch a single User row by its primary key.

        Args:
            user_id: The integer primary key of the target user.

        Returns:
            The matching User ORM instance, or None if no row exists.
        """
        result = await self.db.execute(select(User).where(User.id == user_id))
        # scalar_one_or_none() returns the User object or None.
        # If multiple rows somehow match (impossible with a primary-key
        # constraint, but guarded against here), it raises MultipleResultsFound
        # — a data-integrity protection that should never fire in practice.
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 4 — get_by_email
    # =========================================================================
    async def get_by_email(self, email: str) -> Optional[User]:
        """
        Fetch a single User row by email address.

        Args:
            email: The email address to search for.

        Returns:
            The matching User ORM instance, or None if no row exists.
        """
        result = await self.db.execute(select(User).where(User.email == email))
        # Used in two distinct places in the application:
        #   1. During registration — the service checks the return value;
        #      if not None, the email is already taken and a 409 is raised.
        #   2. During login — the service fetches the user to compare the
        #      supplied plain-text password against the stored bcrypt hash.
        return result.scalar_one_or_none()

    # =========================================================================
    # SECTION 5 — create
    # =========================================================================
    async def create(
        self,
        email: str,
        hashed_password: str,
        full_name: Optional[str] = None,
        is_superuser: bool = False,
    ) -> User:
        """
        Insert a new User row into the database.

        The caller (service layer) is responsible for hashing the plain-text
        password before passing hashed_password here. This method never
        receives or handles plain-text passwords.

        Args:
            email:           The user's unique email address.
            hashed_password: The bcrypt hash of the user's password.
                             The service layer hashes the plain password
                             before calling create() — this repository
                             never sees or handles plain-text passwords.
            full_name:       Optional display name (may be set later).
            is_superuser:    Defaults to False; only set True for admin
                             accounts, never via the public registration API.

        Returns:
            The newly created User instance, fully populated from the DB
            (id and created_at are present after refresh).
        """
        user = User(
            email=email,
            hashed_password=hashed_password,
            full_name=full_name,
            is_superuser=is_superuser,
            # is_active defaults to True from the model column definition —
            # new accounts are immediately usable without manual activation.
        )

        # add() — stages the object in the session's identity map.
        # The row does NOT exist in PostgreSQL yet at this point.
        self.db.add(user)

        # commit() — opens a transaction, flushes the INSERT to PostgreSQL,
        # and commits. After this call the row exists in the DB and PostgreSQL
        # has assigned id and created_at.
        await self.db.commit()

        # refresh() — issues a SELECT to reload the row from the DB back onto
        # the Python object. Without this, user.id and user.created_at would
        # still be None on the Python side (they were None before commit).
        await self.db.refresh(user)

        return user

    # =========================================================================
    # SECTION 6 — update
    # =========================================================================
    async def update(self, user: User, payload: UserUpdate) -> User:
        """
        Apply a partial update to an existing User row.

        The caller fetches the User first and passes it here together with
        a UserUpdate payload. Only fields the client actually sent are
        written — fields not included in the request body are left unchanged.

        Args:
            user:    The existing User ORM instance to be modified.
            payload: A UserUpdate Pydantic model containing the fields to
                     change. Fields not supplied by the client are absent from
                     the model's __fields_set__ and are therefore skipped.

        Returns:
            The updated User instance, reloaded from the database.
        """
        # exclude_unset=True is the correct partial-update pattern.
        #
        # Without it: model_dump() would include every field — even those the
        # client never sent — serialised to their default (usually None):
        #   {"email": None, "password": None, "full_name": "Nouh"}
        # That would overwrite email and password with None even though the
        # client only intended to update full_name.
        #
        # With it: only fields the client explicitly included in the request
        # body appear in the dict:
        #   {"full_name": "Nouh"}
        # So only full_name is updated — email and password are untouched.
        update_data = payload.model_dump(exclude_unset=True)

        for field, value in update_data.items():
            # setattr(user, "full_name", "Nouh") is exactly equivalent to
            # user.full_name = "Nouh", but works when the field name is a
            # variable at runtime (as it is here, iterating over a dict).
            setattr(user, field, value)

        # Re-add to session to mark the object as dirty and stage the UPDATE.
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)

        return user

    # =========================================================================
    # SECTION 7 — delete
    # =========================================================================
    async def delete(self, user: User) -> None:
        """
        Delete an existing User row from the database.

        The service layer is responsible for fetching the user first and
        confirming it exists before calling this method. The repository does
        not re-fetch inside delete — single responsibility: just delete what
        it receives.

        Args:
            user: The User ORM instance to be deleted. Must already be
                  loaded by the session (e.g. retrieved via get_by_id).
        """
        # delete() — marks the object for removal and issues DELETE on commit.
        await self.db.delete(user)
        await self.db.commit()

    # =========================================================================
    # SECTION 8 — get_all
    # =========================================================================
    async def get_all(self) -> list[User]:
        """
        Fetch all User rows ordered by creation date, newest first.

        This method is admin-only. The ROUTE that calls this method enforces
        the is_superuser permission check — this repository does not check
        permissions. Repositories are auth-blind by design: they only query
        data, never decide who is allowed to see it.

        Returns:
            A list of User ORM instances ordered by created_at descending.
            Returns an empty list if no users exist.
        """
        result = await self.db.execute(select(User).order_by(User.created_at.desc()))
        # scalars().all() unpacks the result rows and returns a plain Python
        # list of User objects. Returns [] if the table is empty — never None.
        # order_by created_at desc — newest users appear first in the list.
        return list(result.scalars().all())
