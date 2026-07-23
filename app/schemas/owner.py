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

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from pydantic_extra_types.phone_numbers import PhoneNumber

# OwnerType is reused directly from the model, not redefined here — one
# definition shared across the database layer and the API layer, so the
# enum can never drift between what the DB accepts and what the API emits.
from app.models.owner import OwnerType


# =============================================================================
# SECTION 2 — DamalPhoneNumber type
# =============================================================================
# Intended for reuse by ANY future model that needs phone validation (e.g. a
# future Client model), not just Owner — hence the general, non-Kenya-exclusive
# name even though Kenya is merely the default region.
class DamalPhoneNumber(PhoneNumber):
    """Application-wide phone number type.

    Defaults to Kenya (KE) when a client sends a number with no
    explicit country code, but accepts and correctly validates
    international numbers with any country code just as well.
    Always normalizes to E164 format (e.g. "+254707234780") — the
    most compact standard representation, chosen specifically to
    fit within our String(20) database columns (RFC3966, the
    library's other common format, includes a "tel:" prefix and
    hyphens that can exceed 20 characters for some valid numbers).
    """

    default_region_code = "KE"
    phone_format = "E164"


# =============================================================================
# SECTION 3 — OwnerBase
# =============================================================================
class OwnerBase(BaseModel):
    """
    Shared foundation for OwnerCreate and OwnerUpdate.

    Never used directly as a request or response type — it exists purely to
    avoid repeating the same fields across the input schemas.
    """

    # The owner's full legal name (individual) or company name.
    # min_length=2 rejects trivially short names; max_length matches
    # String(255) on the Owner model. See strip_and_validate_name below for
    # why min_length alone is not enough.
    name: str = Field(min_length=2, max_length=255)

    # Optional contact phone number, validated as a real Kenyan-defaulted
    # number and stored in E.164 form. See the DamalPhoneNumber type above.
    phone: Optional[DamalPhoneNumber] = None

    # Optional contact email address. max_length matches String(255) on the
    # model.
    email: Optional[EmailStr] = Field(default=None, max_length=255)

    # Government-issued identification number for individual owners.
    # max_length matches String(50) on the model.
    national_id: Optional[str] = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def strip_and_validate_name(cls, v: str) -> str:
        """Strip surrounding whitespace, then re-check the cleaned length.

        min_length=2 on the Field alone is not enough: a raw value like " "
        (a single space) or "a " (one stray character plus padding) would
        satisfy the length check while being a meaningless name. This
        validator strips whitespace FIRST, then re-checks the length on the
        cleaned value, and returns that cleaned value so leading/trailing
        whitespace never gets stored.

        min_length=2 is a reasonable floor without being overly strict —
        deliberately no character-pattern regex, since real names legitimately
        include hyphens, apostrophes, and non-Latin scripts.
        """
        stripped = v.strip()
        if len(stripped) < 2:
            raise ValueError("Name must be at least 2 characters")
        return stripped


# =============================================================================
# SECTION 4 — OwnerCreate
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
# SECTION 5 — OwnerUpdate
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
    # min_length=2 still rejects an explicit empty or trivially short string;
    # the validator below applies the same strip-then-recheck as OwnerBase
    # whenever a value is actually provided.
    name: Optional[str] = Field(default=None, min_length=2, max_length=255)

    # If omitted, the phone number is not changed. When provided, validated
    # and stored the same way as OwnerBase — see the DamalPhoneNumber type above.
    phone: Optional[DamalPhoneNumber] = None

    # If omitted, the email address is not changed.
    email: Optional[EmailStr] = Field(default=None, max_length=255)

    # If omitted, the national ID is not changed.
    national_id: Optional[str] = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def strip_and_validate_name(cls, v: Optional[str]) -> Optional[str]:
        """Same strip-then-recheck as OwnerBase, but None-aware.

        On a PATCH, name is optional: None means "leave the name unchanged",
        so it passes through untouched. When a real string IS provided, it is
        stripped and re-checked exactly as in OwnerBase — see that validator
        for why min_length alone is insufficient.
        """
        if v is None:
            return v
        stripped = v.strip()
        if len(stripped) < 2:
            raise ValueError("Name must be at least 2 characters")
        return stripped


# =============================================================================
# SECTION 6 — OwnerResponse
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
