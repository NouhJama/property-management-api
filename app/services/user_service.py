"""
User service — the business logic layer for all user operations.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Enforce business rules (duplicate emails, credential checks, account state).
  - Hash plain-text passwords before they ever reach the repository.
  - Raise HTTPException with semantically correct status codes for the router.

Out of scope for this file:
  - SQL / SQLAlchemy queries (the repository's job).
  - HTTP request/response handling and JWT token creation (the router's job).
  - Direct use of bcrypt or jwt internals (security.py's job).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from fastapi import HTTPException, status

from app.core.security import hash_password, verify_password

# User is imported as a type annotation only.
# The service never instantiates User() or writes queries — that is the
# repository's job.
from app.models.user import User
from app.repositories.user_repository import UserRepository
from app.schemas.user import UserCreate, UserUpdate


# =============================================================================
# SECTION 2 — UserService class
# =============================================================================
class UserService:
    """
    Business logic layer for all user operations.

    Sits between the router (HTTP) and the repository (database). Enforces
    all rules that determine whether an operation should be allowed and in
    what form:
      - Never writes SQL — always delegates to the repository.
      - Never handles HTTP request/response objects directly.
      - Raises HTTPException for the router to handle.
      - Receives its repository via the constructor — never creates its own
        (dependency injection pattern).
    """

    def __init__(self, repository: UserRepository) -> None:
        """
        Store the injected repository.

        Args:
            repository: The UserRepository this service delegates all
                database access to.
        """
        # Injected by dependencies.py — never instantiated directly
        # inside this class.
        self.repo = repository

    # =========================================================================
    # SECTION 3 — create_user
    # =========================================================================
    async def create_user(self, payload: UserCreate) -> User:
        """
        Create a new user account.

        Checks for duplicate email first. Hashes the password before
        storing. Hardcodes is_superuser=False regardless of any client
        input.

        Args:
            payload: The validated registration data from the client.

        Returns:
            The newly created User instance.

        Raises:
            HTTPException: 400 if the email is already registered.
        """
        # Step 1 — Check for duplicate email
        existing = await self.repo.get_by_email(payload.email)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A user with this email already exists",
            )

        # Step 2 — Hash the password.
        # The plain-text password is discarded after this line — only the
        # hash is passed forward. The repository never sees a plain password.
        hashed = hash_password(payload.password)

        # Step 3 — Create via repository.
        # is_superuser hardcoded False — second layer of defence. Even if
        # UserCreate somehow allowed is_superuser, the service ignores it.
        # All registrations create non-admin users.
        return await self.repo.create(
            email=payload.email,
            hashed_password=hashed,
            full_name=payload.full_name,
            is_superuser=False,
        )

    # =========================================================================
    # SECTION 4 — authenticate_user
    # =========================================================================
    # Enumeration protection:
    # Both "email not found" and "wrong password" raise the IDENTICAL error
    # message "Incorrect email or password". This prevents attackers from
    # probing the login endpoint to discover which emails are registered
    # in the system.
    async def authenticate_user(self, email: str, password: str) -> User:
        """
        Verify email and password for login.

        Returns the User object if credentials are valid. Raises 401 if
        invalid. The router creates the JWT token after this succeeds —
        not the service.

        Args:
            email: The email address supplied at login.
            password: The plain-text password supplied at login.

        Returns:
            The authenticated User instance.

        Raises:
            HTTPException: 401 if the credentials are invalid, 400 if the
                account has been deactivated.
        """
        # Step 1 — Find user by email
        user = await self.repo.get_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Step 2 — Verify password.
        # Identical error to Step 1 — intentional (enumeration protection).
        if not verify_password(password, user.hashed_password):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Incorrect email or password",
                headers={"WWW-Authenticate": "Bearer"},
            )

        # Step 3 — Check account is active
        if not user.is_active:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="This account has been deactivated.",
            )

        # Step 4 — Return authenticated user.
        # The router creates the JWT token from this user. Service verifies
        # identity — router issues tokens.
        return user

    # =========================================================================
    # SECTION 5 — get_user_by_id
    # =========================================================================
    async def get_user_by_id(self, user_id: int) -> User:
        """
        Fetch a user by primary key.

        Args:
            user_id: The integer primary key of the target user.

        Returns:
            The matching User instance.

        Raises:
            HTTPException: 404 if no user with this id exists.
        """
        # Fetch by primary key; translate "not found" into a 404 for the router.
        user = await self.repo.get_by_id(user_id)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with id {user_id} not found",
            )
        return user

    # =========================================================================
    # SECTION 6 — get_user_by_email
    # =========================================================================
    async def get_user_by_email(self, email: str) -> User:
        """
        Fetch a user by email address.

        Note: use authenticate_user() for login — this method is for
        internal lookups only.

        Args:
            email: The email address to look up.

        Returns:
            The matching User instance.

        Raises:
            HTTPException: 404 if no user with this email exists.
        """
        # Fetch by email; translate "not found" into a 404 for the router.
        user = await self.repo.get_by_email(email)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"User with email {email} not found",
            )
        return user

    # =========================================================================
    # SECTION 7 — update_user
    # =========================================================================
    async def update_user(self, user: User, payload: UserUpdate) -> User:
        """
        Update a user's profile.

        The router fetches the user first via get_user_by_id and passes the
        User object here (fetch-then-act pattern). Checks the new email is
        not taken. Hashes the new password if provided. Only fields the
        client sent are changed.

        Args:
            user: The existing User instance to update.
            payload: The validated partial-update data from the client.

        Returns:
            The updated User instance.

        Raises:
            HTTPException: 400 if the new email is already taken.
        """
        # Step 1 — If email is changing, check it is not taken
        if payload.email and payload.email != user.email:
            existing = await self.repo.get_by_email(payload.email)
            if existing:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="A user with this email already exists",
                )

        # Step 2 — If password is changing, hash the new one.
        # model_copy creates a NEW UserUpdate object with the password field
        # replaced by its hash. The original payload object is never
        # modified — immutability prevents side effects. The local variable
        # payload is reassigned to point to the new copy. Without this, the
        # repository would receive the plain text password and store it in
        # the database — a critical security failure.
        if payload.password:
            payload = payload.model_copy(update={"password": hash_password(payload.password)})

        # Step 3 — Delegate to repository.
        # The repository uses exclude_unset=True so only fields the client
        # actually sent are updated.
        return await self.repo.update(user, payload)

    # =========================================================================
    # SECTION 8 — delete_user
    # =========================================================================
    async def delete_user(self, user: User) -> None:
        """
        Delete a user permanently.

        The router fetches the user first via get_user_by_id and passes the
        User object here — fetch-then-act pattern.

        Args:
            user: The User instance to delete.
        """
        # Delegate the delete to the repository — the user's existence was
        # already confirmed by the router via get_user_by_id.
        await self.repo.delete(user)

    # =========================================================================
    # SECTION 9 — get_all_users
    # =========================================================================
    async def get_all_users(self) -> list[User]:
        """
        Return all users.

        Admin-only operation. Permission enforcement happens at the route
        level via Depends(get_current_active_superuser) — not here.

        Returns:
            A list of all User instances, newest first.
        """
        # No business rules to apply — delegate straight to the repository.
        return await self.repo.get_all()
