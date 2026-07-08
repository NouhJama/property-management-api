"""
Authentication router — the HTTP layer for register, login, and me.

Exposes the authentication endpoints and connects incoming HTTP requests
to the UserService and the auth dependencies. No business logic lives
here — every handler only wires the request to the service layer (or a
dependency) and returns the result:

  POST /api/v1/auth/register — create a new account.
  POST /api/v1/auth/login    — verify credentials, issue a JWT.
  GET  /api/v1/auth/me       — return the user the token belongs to.

Architecture position:
  routers (HTTP, this file) → services (business logic) → repositories → models
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm

from app.core.dependencies import get_current_user, get_user_service
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.user import Token, UserCreate, UserResponse
from app.services.user_service import UserService

# =============================================================================
# SECTION 2 — Router object
# =============================================================================
# prefix "/auth" combines with the "/api/v1" prefix added in main.py to
# produce /api/v1/auth/... — matching the tokenUrl set in dependencies.py.
router = APIRouter(prefix="/auth", tags=["Authentication"])


# =============================================================================
# SECTION 3 — POST /register
# =============================================================================
# 201 Created is the correct status for resource creation.
@router.post(
    "/register",
    response_model=UserResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    payload: UserCreate,
    service: UserService = Depends(get_user_service),
) -> UserResponse:
    """
    Create a new user account.

    Returns the created user — never the password hash;
    response_model=UserResponse filters it out of the response.

    Args:
        payload: The validated registration data from the client.
        service: The request-scoped UserService.

    Returns:
        The newly created user, serialised through UserResponse.

    Raises:
        HTTPException: 400 if the email is already registered (raised by
            the service).
    """
    return await service.create_user(payload)


# =============================================================================
# SECTION 4 — POST /login
# =============================================================================
@router.post(
    "/login",
    response_model=Token,
    summary="Log in and receive an access token",
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    service: UserService = Depends(get_user_service),
) -> Token:
    """
    Verify credentials and return a bearer token.

    The router (not the service) issues the token: the service verifies
    identity via authenticate_user, then this handler creates the JWT.

    Args:
        form_data: OAuth2 form fields — username (holds the email) and
            password.
        service: The request-scoped UserService.

    Returns:
        A Token with the signed JWT and token_type "bearer".

    Raises:
        HTTPException: 401 on bad credentials, 400 if the account is
            deactivated (both raised by the service).
    """
    # Step 1 — Authenticate.
    # form_data.username holds the EMAIL (the OAuth2 standard names the
    # field "username" regardless of what we use to log in).
    # authenticate_user raises 401 on bad credentials.
    user = await service.authenticate_user(form_data.username, form_data.password)

    # Step 2 — Create the JWT.
    # CRITICAL: sub MUST be str(user.id) — a string, not an int.
    # get_current_user parses it back with int(), and the JWT spec
    # requires sub to be a string.
    token = create_access_token({"sub": str(user.id)})

    # Step 3 — Return the token.
    return Token(access_token=token, token_type="bearer")


# =============================================================================
# SECTION 5 — GET /me
# =============================================================================
@router.get(
    "/me",
    response_model=UserResponse,
    summary="Get the current logged-in user",
)
async def get_me(
    current_user: User = Depends(get_current_user),
) -> UserResponse:
    """
    Return the profile of whoever the token belongs to.

    This is a PROTECTED route — get_current_user rejects the request with
    401 if the token is missing, invalid, or expired.

    Args:
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The current user, serialised through UserResponse.
    """
    # No user_id parameter needed — the token identifies the caller.
    return current_user
