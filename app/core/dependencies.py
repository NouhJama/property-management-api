"""
Dependency wiring — the composition layer that connects FastAPI routes
to the rest of the application.

This file has two jobs:

  1. ASSEMBLY — builds the dependency chain
         get_db → UserRepository → UserService
     so routes declare a single Depends(get_user_service) instead of
     constructing the session/repository/service chain by hand.

  2. AUTHENTICATION — turns an incoming JWT into a verified User via
     get_current_user, and enforces admin access via
     get_current_active_superuser.

Architecture position:
  routers (HTTP) → [dependencies.py wires the layers] → services → repositories → models

Out of scope for this file:
  - Business logic (services), queries (repositories), hashing/JWT internals
    (security.py). This file only wires existing pieces together.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import verify_token
from app.database import get_db
from app.models.user import User
from app.repositories.owner_repository import OwnerRepository
from app.repositories.tenant_repository import TenantRepository
from app.repositories.unit_repository import UnitRepository
from app.repositories.user_repository import UserRepository
from app.services.owner_service import OwnerService
from app.services.tenant_service import TenantService
from app.services.unit_service import UnitService
from app.services.user_service import UserService

# =============================================================================
# SECTION 2 — OAuth2 scheme
# =============================================================================
# OAuth2PasswordBearer does two things:
#   1. At request time, it extracts the token from the
#      "Authorization: Bearer <token>" header and returns it as a str.
#      If the header is missing, it rejects the request with a 401
#      (auto_error=True is the default).
#   2. In the OpenAPI schema, it documents the password flow so the
#      interactive /docs page shows the "Authorize" button.
#
# tokenUrl points at the login endpoint that will issue tokens
# (OAuth2PasswordRequestForm). It is documentation for OpenAPI — it does
# not create the endpoint. Must match the auth router once it exists.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/v1/auth/login")


# =============================================================================
# SECTION 3 — Assembly: repository and service factories
# =============================================================================
def get_user_repository(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UserRepository:
    """Build a UserRepository bound to the request-scoped session.

    FastAPI resolves get_db first, then injects the resulting
    AsyncSession here. One repository per request.

    Args:
        db: The request-scoped AsyncSession from get_db().

    Returns:
        A UserRepository ready to execute queries on this request's session.
    """
    return UserRepository(db)


def get_user_service(
    repository: Annotated[UserRepository, Depends(get_user_repository)],
) -> UserService:
    """Build a UserService on top of the request-scoped repository.

    This is the dependency routes actually declare:
        service: Annotated[UserService, Depends(get_user_service)]
    FastAPI resolves the whole chain (get_db → get_user_repository →
    here) automatically.

    Args:
        repository: The request-scoped UserRepository.

    Returns:
        A UserService wired to this request's repository and session.
    """
    return UserService(repository)


def get_owner_repository(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> OwnerRepository:
    """Build an OwnerRepository bound to the request-scoped session.

    FastAPI resolves get_db first, then injects the resulting
    AsyncSession here. Plain def — it only constructs an object and
    awaits nothing itself.

    Args:
        db: The request-scoped AsyncSession from get_db().

    Returns:
        An OwnerRepository ready to execute queries on this request's session.
    """
    return OwnerRepository(db)


def get_owner_service(
    repository: Annotated[OwnerRepository, Depends(get_owner_repository)],
) -> OwnerService:
    """Build an OwnerService on top of the request-scoped repository.

    Completes the assembly chain get_db → get_owner_repository → here,
    so a route only declares:
        service: Annotated[OwnerService, Depends(get_owner_service)]
    to receive a finished service.

    No Owner-specific auth guard is added here. Owner routes reuse the
    existing get_current_user and get_current_active_superuser
    dependencies as-is — those verify identity generically and have no
    dependency on which resource is being protected.

    Args:
        repository: The request-scoped OwnerRepository.

    Returns:
        An OwnerService wired to this request's repository and session.
    """
    return OwnerService(repository)


def get_unit_repository(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> UnitRepository:
    """Build a UnitRepository with a live database session.

    Uses the Annotated[Type, Depends(...)] syntax, FastAPI's currently
    recommended form and the style every dependency in this file already
    follows. New dependency functions default to it.

    Args:
        db: The request-scoped AsyncSession from get_db().

    Returns:
        A UnitRepository ready to execute queries on this request's session.
    """
    return UnitRepository(db)


def get_unit_service(
    repository: Annotated[UnitRepository, Depends(get_unit_repository)],
) -> UnitService:
    """Build a UnitService with a repository.

    Completes the assembly chain: get_db → get_unit_repository → here,
    so a route only declares:
        service: Annotated[UnitService, Depends(get_unit_service)]
    to receive a finished service.

    No new auth guard needed — the (not yet built) unit router reuses the
    existing get_current_user and get_current_active_superuser directly,
    exactly as Owner does.

    Args:
        repository: The request-scoped UnitRepository.

    Returns:
        A UnitService wired to this request's repository and session.
    """
    return UnitService(repository)


def get_tenant_repository(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> TenantRepository:
    """Build a TenantRepository with a live database session.

    Uses the same Annotated[Type, Depends(...)] syntax as the
    Unit dependency functions.
    """
    return TenantRepository(db)


def get_tenant_service(
    repository: Annotated[TenantRepository, Depends(get_tenant_repository)],
) -> TenantService:
    """Build a TenantService with a repository.

    Completes the assembly chain: get_db → repository → service.
    No new auth guard needed — the (not yet built) tenant router
    will reuse get_current_user for create/update/read/list and
    get_current_active_superuser for delete only, per the
    permission split already decided for Tenant (staff can
    create/update, but delete stays admin-only).
    """
    return TenantService(repository)


# =============================================================================
# SECTION 4 — Authentication: get_current_user
# =============================================================================
async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    service: Annotated[UserService, Depends(get_user_service)],
) -> User:
    """Resolve the JWT from the Authorization header into a verified User.

    Flow:
      1. oauth2_scheme extracts the raw token (401 if the header is missing).
      2. verify_token() validates signature + expiry and returns the payload
         (raises 401 itself on any invalid token).
      3. The "sub" claim is parsed as the user's integer primary key.
      4. The user is loaded from the database and checked for is_active.

    Args:
        token: The raw JWT extracted by oauth2_scheme.
        service: The request-scoped UserService.

    Returns:
        The authenticated, active User instance.

    Raises:
        HTTPException: 401 if the token is invalid or the user no longer
            exists; 400 if the account has been deactivated.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # verify_token guarantees "sub" is present, but it stores the id as a
    # string (JWT spec: sub must be a string). A non-numeric sub means a
    # token we never issued — treat it exactly like a tampered token.
    payload = verify_token(token)
    try:
        user_id = int(payload["sub"])
    except (TypeError, ValueError):
        raise credentials_exception

    # A valid token for a since-deleted user must NOT leak that the account
    # existed: translate the service's 404 into the same generic 401 that
    # every other credential failure produces.
    try:
        user = await service.get_user_by_id(user_id)
    except HTTPException:
        raise credentials_exception

    # Deactivated accounts keep their rows (and their old tokens) but lose
    # access immediately. Same 400 the login path uses.
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="This account has been deactivated.",
        )

    return user


# =============================================================================
# SECTION 5 — Authorization: get_current_active_superuser
# =============================================================================
async def get_current_active_superuser(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Require the authenticated user to be a superuser.

    Chains on get_current_user, so authentication (valid token, active
    account) is already guaranteed — this only adds the admin check.
    Use on admin-only routes, e.g. listing all users:
        Depends(get_current_active_superuser)

    Args:
        current_user: The already-authenticated User from get_current_user.

    Returns:
        The same User, confirmed to be a superuser.

    Raises:
        HTTPException: 403 if the user is authenticated but not an admin —
            identity is known (not 401), permission is lacking (403).
    """
    if not current_user.is_superuser:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user
