"""
Security helpers — password hashing and JWT token handling.

This module is the ONLY place in the application that touches the pwdlib
and jwt libraries. Every other layer (services, routers, dependencies)
imports the four public helpers below and never deals with hashing or
token internals directly:

  hash_password()       — bcrypt-hash a plain-text password (registration).
  verify_password()     — check a plain-text password against a stored hash (login).
  create_access_token() — issue a signed JWT after successful authentication.
  verify_token()        — decode and validate a JWT from an incoming request.

No database access, no HTTP concerns, and no business logic live here.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import jwt
from fastapi import HTTPException, status
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

# =============================================================================
# SECTION 2 — Password context
# =============================================================================
# pwd_context is the single pwdlib PasswordHash instance for the whole app.
# It is configured with bcrypt only — matching the hashes already stored in
# users.hashed_password. Adding a new hasher first in this tuple later would
# migrate users transparently: verify() still accepts old bcrypt hashes.
pwd_context = PasswordHash((BcryptHasher(),))


# =============================================================================
# SECTION 3 — hash_password
# =============================================================================
def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password with bcrypt.

    Args:
        plain_password: The plain-text password to hash.

    Returns:
        The bcrypt hash as a string (~60 characters), safe to store in
        the users.hashed_password column.
    """
    # pwdlib generates a fresh random salt per call, so hashing the same
    # password twice produces two different hashes — this is intentional.
    return pwd_context.hash(plain_password)


# =============================================================================
# SECTION 4 — verify_password
# =============================================================================
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Check a plain-text password against a stored bcrypt hash.

    Args:
        plain_password: The password supplied by the client at login.
        hashed_password: The bcrypt hash stored in the database.

    Returns:
        True if the password matches the hash, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


# =============================================================================
# SECTION 5 — create_access_token
# =============================================================================
def create_access_token(data: dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a signed JWT access token.

    Called by the auth router after authenticate_user() succeeds — the
    service layer verifies identity, this helper issues the token.

    Args:
        data: The claims to embed in the token (e.g. {"sub": user.email}).
        expires_delta: Optional custom lifetime. Defaults to
            settings.access_token_expire_minutes when not provided.

    Returns:
        The encoded JWT as a string.
    """
    # Copy so the caller's dict is never mutated when we add the exp claim.
    to_encode = data.copy()

    # Expiry is always UTC — the "exp" claim is validated by jwt.decode()
    # on every request, so expired tokens are rejected automatically.
    expire = datetime.now(timezone.utc) + (
        expires_delta
        if expires_delta is not None
        else timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})

    # Sign with the application secret — settings.secret_key comes from .env
    # and is never hard-coded anywhere in the codebase.
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


# =============================================================================
# SECTION 6 — verify_token
# =============================================================================
def verify_token(token: str) -> dict[str, Any]:
    """
    Decode and validate a JWT access token.

    Returns the decoded claims if the token is valid. Raises a 401
    HTTPException if the token is expired, tampered with, or malformed —
    callers (e.g. get_current_user in dependencies.py) let it propagate
    straight to the client.

    Args:
        token: The encoded JWT extracted from the Authorization header.

    Returns:
        The decoded payload dict.

    Raises:
        HTTPException: 401 if the token is invalid in any way.
    """
    try:
        # decode() verifies the signature and the exp claim in one step.
        # algorithms is an explicit allow-list — never derived from the
        # token header, which would allow algorithm-confusion attacks.
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except InvalidTokenError:
        # Covers expired signature, bad signature, and malformed tokens.
        # One identical error for every failure mode — never reveal to the
        # client WHY the token was rejected.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
