# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import HTTPException, status
from jwt.exceptions import InvalidTokenError
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from app.core.config import settings

# =============================================================================
# SECTION 2 — Password context
# =============================================================================
# PasswordHash is the modern replacement for passlib's CryptContext.
# BcryptHasher wraps the bcrypt algorithm — deliberately slow to resist
# brute-force attacks; cost factor is baked in at hash time.
#
# Created once at module level, never per request:
#   - instantiation is cheap; bcrypt work happens inside hash() / verify()
#   - module-level singleton avoids repeated object creation under load
#
# bcrypt automatically generates a random salt for every hash() call.
# The salt is embedded inside the resulting hash string, so verify()
# can re-derive and compare without any extra storage.
pwd_context = PasswordHash((BcryptHasher(),))


# =============================================================================
# SECTION 3 — hash_password
# =============================================================================
# Called by user_service before passing a new password to the repository.
# Returns the bcrypt hash string (60 chars) to store in the DB column.
# One-way: the plain password cannot be recovered from the hash.
# Never log or store the plain_password argument — treat it as a secret.
def hash_password(plain_password: str) -> str:
    """Hash a plain-text password using bcrypt.

    Args:
        plain_password: The raw password provided by the user.

    Returns:
        A bcrypt hash string suitable for storage in the database.
    """
    return pwd_context.hash(plain_password)


# =============================================================================
# SECTION 4 — verify_password
# =============================================================================
# pwdlib extracts the salt embedded in hashed_password, re-hashes
# plain_password with that same salt, then compares the two results.
# Uses a constant-time comparison internally to prevent timing attacks.
#
# Returns True on match, False on mismatch — does NOT raise on failure.
# The service layer is responsible for deciding what to do with False
# (typically raise 401 Unauthorized).
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain-text password against a stored bcrypt hash.

    Args:
        plain_password: The raw password provided by the user at login.
        hashed_password: The bcrypt hash retrieved from the database.

    Returns:
        True if the password matches, False otherwise.
    """
    return pwd_context.verify(plain_password, hashed_password)


# =============================================================================
# SECTION 5 — create_access_token
# =============================================================================
# data should contain {"sub": str(user.id)}.
#   "sub" (subject) is the JWT standard claim identifying the user.
#   "exp" (expiry) is added here — PyJWT validates it automatically on decode.
#
# PyJWT signs the payload with SECRET_KEY using the configured algorithm
# (HS256 by default). Any tampering with the payload invalidates the
# signature and causes jwt.decode() to raise InvalidTokenError.
#
# Returns a compact string — three Base64URL segments joined by dots.
# This is the complete token; send it directly to the client.
def create_access_token(
    data: dict,
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Create a signed JWT access token.

    Args:
        data: Claims to encode — must include {"sub": str(user_id)}.
        expires_delta: Custom lifetime; defaults to settings value if omitted.

    Returns:
        A signed JWT string to return to the client.
    """
    to_encode = data.copy()

    if expires_delta is not None:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(
            minutes=settings.access_token_expire_minutes
        )

    to_encode.update({"exp": expire})

    return jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.algorithm,
    )


# =============================================================================
# SECTION 6 — verify_token
# =============================================================================
# jwt.decode() performs two checks in one call:
#   1. Signature verification — rejects any token whose payload was tampered with.
#   2. Expiry check — rejects tokens whose "exp" claim is in the past.
#
# InvalidTokenError is the base class for all PyJWT failure modes:
#   ExpiredSignatureError, DecodeError, InvalidSignatureError, etc.
# All failure cases map to 401 Unauthorized — callers receive a uniform error.
#
# WWW-Authenticate: Bearer in the response header tells the client that
# it must supply a valid Bearer token to access the resource (RFC 6750).
#
# On success, returns the full payload dict.
# Callers extract the user ID with payload.get("sub").
def verify_token(token: str) -> dict:
    """Decode and validate a JWT access token.

    Args:
        token: The raw JWT string from the Authorization header.

    Returns:
        The decoded payload dict on success.

    Raises:
        HTTPException 401: If the token is expired, tampered, or malformed.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
        return payload
    except InvalidTokenError:
        raise credentials_exception
