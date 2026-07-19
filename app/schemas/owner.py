"""
Pydantic V2 schemas for the Owner resource.

Schema hierarchy:
  OwnerBase       — shared input fields (name, phone, email, national_id)
  OwnerCreate     — extends OwnerBase unchanged (POST /owners, individuals only)
  OwnerUpdate     — all fields optional (PATCH /owners/{id})
  OwnerResponse   — public read schema returned by every owner endpoint

These schemas sit at the HTTP boundary — they are the gate between raw client
JSON and the service layer. No business logic, no DB access here.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# OwnerType is reused directly from the model, not redefined here — one
# definition shared across the database layer and the API layer, so the
# enum can never drift between what the DB accepts and what the API emits.
from app.models.owner import OwnerType


# =============================================================================
# SECTION 2 — OwnerBase
# =============================================================================
class OwnerBase(BaseModel):
    """
    Shared foundation for OwnerCreate and OwnerUpdate.

    Never used directly as a request or response type — it exists purely to
    avoid repeating the same fields across the input schemas.
    """

    # The owner's full legal name (individual) or company name.
    # min_length=1 rejects empty strings; max_length matches String(255)
    # on the Owner model.
    name: str = Field(min_length=1, max_length=255)

    # Optional contact phone number. max_length matches String(20) on the
    # model — covers international formats with country code and separators.
    phone: Optional[str] = Field(default=None, max_length=20)

    # Optional contact email address. max_length matches String(255) on the
    # model.
    email: Optional[EmailStr] = Field(default=None, max_length=255)

    # Government-issued identification number for individual owners.
    # max_length matches String(50) on the model.
    national_id: Optional[str] = Field(default=None, max_length=50)


# =============================================================================
# SECTION 3 — OwnerCreate
# =============================================================================
class OwnerCreate(OwnerBase):
    """
    Schema for creating a new owner — POST /owners.

    Deliberately has NO "type" field. The service layer ALWAYS hardcodes
    type=OwnerType.INDIVIDUAL when creating an owner through this schema —
    the same defensive pattern as is_superuser being excluded from
    UserCreate.

    The single type="company" row (Damal Heights) is created ONLY via a
    one-time data migration, never through this schema or this endpoint.
    This is a structural guarantee, not just a convention — the field
    literally does not exist here for a client to send, and Pydantic
    ignores unknown fields by default.
    """

    pass


# =============================================================================
# SECTION 4 — OwnerUpdate
# =============================================================================
class OwnerUpdate(BaseModel):
    """
    Schema for partial owner updates — PATCH /owners/{id}.

    All fields are optional: only the fields the client actually sends get
    updated (the repository uses exclude_unset=True, same as UserUpdate).
    Omitting a field means "do not change this field."

    Also deliberately excludes "type" — an owner's type should never change
    via a normal update. An individual owner becoming "the company" makes
    no domain sense, and the company row is managed exclusively by the
    seed migration.
    """

    # Optional here, unlike OwnerBase — if omitted, the name is not changed.
    # min_length=1 still rejects an explicit empty string.
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)

    # If omitted, the phone number is not changed.
    phone: Optional[str] = Field(default=None, max_length=20)

    # If omitted, the email address is not changed.
    email: Optional[EmailStr] = Field(default=None, max_length=255)

    # If omitted, the national ID is not changed.
    national_id: Optional[str] = Field(default=None, max_length=50)


# =============================================================================
# SECTION 5 — OwnerResponse
# =============================================================================
class OwnerResponse(BaseModel):
    """
    Public-facing read schema — used as response_model on every endpoint
    returning an owner.

    Unlike UserResponse, there is no sensitive field to exclude here (no
    password-equivalent on Owner) — type IS included in the response, since
    knowing whether an owner is the company or an individual is normal,
    non-sensitive information the client should see.

    from_attributes=True lets Pydantic read directly from the SQLAlchemy
    Owner object returned by the repository/service, same mechanism as
    UserResponse.

    Does not inherit from OwnerBase because it is a completely independent
    read schema — it represents what we expose, not what we accept.
    """

    # Primary key — always present on a persisted owner.
    id: int

    # The owner's full legal name or company name.
    name: str

    # Optional contact phone number — may be None if never provided.
    phone: Optional[str] = None

    # Optional contact email address — may be None if never provided.
    email: Optional[EmailStr] = None

    # Government ID number — None for the company row and for individuals
    # who never provided one.
    national_id: Optional[str] = None

    # Whether this owner is a real individual or the developer company row.
    # Reused directly from the model — serialises to its string value
    # ("individual" / "company") because OwnerType inherits from str.
    type: OwnerType

    # UTC timestamp of when the row was first created.
    created_at: datetime

    # UTC timestamp of the most recent update — None if never updated.
    updated_at: Optional[datetime] = None

    # from_attributes=True lets Pydantic read from SQLAlchemy ORM instances
    # (object.attribute access) instead of only from plain dicts.
    model_config = ConfigDict(from_attributes=True)
