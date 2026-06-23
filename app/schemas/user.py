"""
Pydantic V2 schemas for the User resource.

Schema hierarchy:
  UserBase        — shared input fields (email, full_name)
  UserCreate      — extends UserBase with plain-text password (registration only)
  UserUpdate      — all fields optional (PATCH /users/me)
  UserResponse    — public read schema returned by every user endpoint
  UserInDB        — internal schema that includes hashed_password (login only)

These schemas sit at the HTTP boundary — they are the gate between raw client
JSON and the service layer. No business logic, no DB access, no hashing here.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


# =============================================================================
# SECTION 2 — UserBase
# =============================================================================
class UserBase(BaseModel):
    """
    Shared foundation for all user input schemas.

    Never used directly as a request or response type — it exists purely to
    avoid repeating the same fields across UserCreate and UserUpdate.
    Only fields that every user input schema shares belong here.
    """

    # EmailStr rejects invalid formats before any business logic runs —
    # e.g. 'notanemail' raises a 422 Unprocessable Entity automatically.
    email: EmailStr

    # Optional display name shown in the UI and on documents.
    # Users can leave it blank at registration and fill it in later.
    full_name: Optional[str] = Field(default=None, max_length=100)


# =============================================================================
# SECTION 3 — UserCreate
# =============================================================================
class UserCreate(UserBase):
    """
    Schema for new user registration — POST /auth/register.

    This is the ONLY schema that ever receives a plain-text password.
    The service layer hashes it with bcrypt immediately on receipt and
    writes only the hash to the database. The plain-text value is never
    stored, never returned, and never logged.

    is_superuser is deliberately absent. Even if a client sends
    is_superuser=true in their JSON body, Pydantic ignores unknown fields
    by default — clients cannot self-assign admin privileges through this
    schema. The service hardcodes is_superuser=False on every registration.
    """

    # The user's plain-text password at registration time.
    # This is the ONLY schema that ever receives a plain-text password.
    # It is hashed by the service layer immediately and never persisted.
    # The User model stores hashed_password — this field is intentionally
    # named differently to make the distinction impossible to miss.
    password: str = Field(min_length=8, max_length=100)

    @field_validator("password")
    @classmethod
    def password_must_not_be_blank(cls, value: str) -> str:
        """Strip whitespace and reject passwords that are blank or whitespace-only."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("Password cannot be blank or whitespace only")
        return stripped


# =============================================================================
# SECTION 4 — UserUpdate
# =============================================================================
class UserUpdate(UserBase):
    """
    Schema for profile updates — PATCH /users/me.

    All fields are optional: users may want to change only their email,
    only their name, or only their password. Sending a field as None (or
    omitting it entirely) means "do not change this field."

    The service layer inspects each field: if it is not None, the change
    is applied. If password is provided it goes through the same bcrypt
    hashing as in UserCreate before being written to the database.
    """

    # Override the required email from UserBase to optional.
    # If None, the email is not changed.
    email: Optional[EmailStr] = None  # type: ignore[assignment]

    # Already optional in UserBase — kept explicit here for clarity.
    # If None, the display name is not changed.
    full_name: Optional[str] = None

    # Plain-text password for an account password change.
    # If None, the password is not changed.
    # Hashed by the service layer before being written, same as UserCreate.
    password: Optional[str] = Field(default=None, min_length=8, max_length=100)


# =============================================================================
# SECTION 5 — UserResponse
# =============================================================================
class UserResponse(BaseModel):
    """
    Public-facing read schema — used as response_model on every user endpoint.

    This is the API's security boundary on the way OUT. hashed_password is
    deliberately not a field here. Even if the User ORM object carries
    hashed_password, FastAPI's response_model filter silently drops any
    attribute not declared in this schema — clients never see password hashes
    under any circumstance, regardless of what the ORM object holds.

    Does not inherit from UserBase because it is a completely independent
    read schema — it represents what we expose, not what we accept.
    """

    # Primary key — always present on a persisted user.
    id: int

    # The user's verified email address.
    email: EmailStr

    # Optional display name — may be None if the user never set one.
    full_name: Optional[str] = None

    # Whether the account is currently enabled.
    # False means the user is suspended but their data is preserved.
    is_active: bool

    # Whether the account has full administrative access.
    is_superuser: bool

    # UTC timestamp of when the row was first created.
    created_at: datetime

    # UTC timestamp of the most recent update — None if never updated.
    updated_at: Optional[datetime] = None

    # from_attributes=True lets Pydantic read from SQLAlchemy ORM instances
    # (object.attribute access) instead of only from plain dicts.
    # Without this, FastAPI cannot serialise the User object returned by the
    # repository into a JSON response — it would raise a validation error.
    # This is why UserResponse is the ONLY schema that needs ConfigDict —
    # it is the only one reading from a DB object. All other schemas receive
    # dicts from the client.
    model_config = ConfigDict(from_attributes=True)


# =============================================================================
# SECTION 6 — UserInDB (internal use only)
# =============================================================================
class UserInDB(UserResponse):
    """
    Internal schema that exposes hashed_password — for service-layer use only.

    Never used as a response_model on any route. Its sole purpose is during
    login verification: the authentication service fetches the full user record
    (including the hash), compares it against the plain-text password the client
    sent using bcrypt.checkpw(), then discards this object. Think of UserInDB
    as the full internal record and UserResponse as the sanitised public view.
    """

    # The bcrypt hash stored in the database.
    # Present here so the auth service can verify login credentials.
    # Never returned to the client — this schema is never a response_model.
    hashed_password: str

    # Explicitly repeated from UserResponse — inherited automatically, but
    # stated here to make it clear this schema also reads from ORM objects.
    model_config = ConfigDict(from_attributes=True)
