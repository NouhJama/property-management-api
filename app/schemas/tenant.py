"""
Pydantic V2 schemas for the Tenant resource.

Schema hierarchy:
  TenantBase      — shared input fields (name, phone, email, national_id)
  TenantCreate    — extends TenantBase unchanged (POST /tenants)
  TenantUpdate    — all fields optional (PATCH /tenants/{id})
  TenantResponse  — public read schema returned by every tenant endpoint

Deliberately simpler than the Owner schemas: a Tenant is always a real
individual renter, so there is no "type" field to exclude from
TenantCreate and no company-placeholder row to special-case.

Note what is absent: no unit_id on any schema here. Which unit a tenant
currently rents is derived from an active rent-category Charge
(Charge.tenant_id + Charge.unit_id), not stored on the tenant — see the
Tenant model docstring for the full reasoning.

These schemas sit at the HTTP boundary — they are the gate between raw
client JSON and the service layer. No business logic, no DB access here.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# DamalPhoneNumber comes from the shared types module, NOT from
# app/schemas/owner.py — Tenant has no dependency on the Owner resource, and
# routing a shared type through another resource's module would invent one.
from app.schemas.common import DamalPhoneNumber


# =============================================================================
# SECTION 2 — TenantBase
# =============================================================================
class TenantBase(BaseModel):
    """
    Shared foundation for TenantCreate and TenantUpdate.

    Never used directly as a request or response type — it exists purely to
    avoid repeating the same fields across the input schemas.
    """

    # The tenant's full legal name. min_length=2 rejects trivially short
    # names; max_length matches String(255) on the Tenant model. See
    # strip_and_validate_name below for why min_length alone is not enough.
    name: str = Field(min_length=2, max_length=255)

    # Optional contact phone number, validated as a real Kenyan-defaulted
    # number and stored in E.164 form. See DamalPhoneNumber in
    # app/schemas/common.py.
    phone: Optional[DamalPhoneNumber] = None

    # Optional contact email address. max_length matches String(255) on the
    # Tenant model. Not checked for uniqueness — a family renting together
    # may legitimately share one address, same as Owner.email.
    email: Optional[EmailStr] = Field(default=None, max_length=255)

    # Government-issued identification number for the tenant.
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
# SECTION 3 — TenantCreate
# =============================================================================
class TenantCreate(TenantBase):
    """
    Schema for creating a new tenant — POST /tenants.

    Adds nothing to TenantBase. Unlike OwnerCreate there is no "type" field
    to defensively omit, because Tenant has no type concept at all — every
    row is a genuine individual renter.

    created_by is likewise absent by design: the service sets it from the
    authenticated user making the request, never from client input — the
    same defensive pattern as OwnerCreate and UserCreate.
    """

    pass


# =============================================================================
# SECTION 4 — TenantUpdate
# =============================================================================
class TenantUpdate(BaseModel):
    """
    Schema for partial tenant updates — PATCH /tenants/{id}.

    All fields are optional: only the fields the client actually sends get
    updated (the repository uses exclude_unset=True, same as OwnerUpdate).
    Omitting a field means "do not change this field."
    """

    # Optional here, unlike TenantBase — if omitted, the name is not changed.
    # min_length=2 still rejects an explicit empty or trivially short string;
    # the validator below applies the same strip-then-recheck as TenantBase
    # whenever a value is actually provided.
    name: Optional[str] = Field(default=None, min_length=2, max_length=255)

    # If omitted, the phone number is not changed. When provided, validated
    # and stored the same way as TenantBase — see DamalPhoneNumber in
    # app/schemas/common.py.
    phone: Optional[DamalPhoneNumber] = None

    # If omitted, the email address is not changed. max_length matches
    # String(255) on the Tenant model, same as TenantBase.
    email: Optional[EmailStr] = Field(default=None, max_length=255)

    # If omitted, the national ID is not changed.
    national_id: Optional[str] = Field(default=None, max_length=50)

    @field_validator("name")
    @classmethod
    def strip_and_validate_name(cls, v: Optional[str]) -> Optional[str]:
        """Same strip-then-recheck as TenantBase, but None-aware.

        On a PATCH, name is optional: None means "leave the name unchanged",
        so it passes through untouched. When a real string IS provided, it is
        stripped and re-checked exactly as in TenantBase — see that validator
        for why min_length alone is insufficient.
        """
        if v is None:
            return v
        stripped = v.strip()
        if len(stripped) < 2:
            raise ValueError("Name must be at least 2 characters")
        return stripped


# =============================================================================
# SECTION 5 — TenantResponse
# =============================================================================
class TenantResponse(BaseModel):
    """
    Public-facing read schema — used as response_model on every endpoint
    returning a tenant.

    There is no sensitive field to exclude here (no password-equivalent on
    Tenant), and no type field to expose either, unlike OwnerResponse.

    from_attributes=True lets Pydantic read directly from the SQLAlchemy
    Tenant object returned by the repository/service, same mechanism as
    OwnerResponse.

    Does not inherit from TenantBase because it is a completely independent
    read schema — it represents what we expose, not what we accept.

    created_by appears HERE ONLY — never on TenantCreate or TenantUpdate. The
    service sets it from the authenticated user making the request, never
    from client input.
    """

    # Primary key — always present on a persisted tenant.
    id: int

    # The tenant's full legal name.
    name: str

    # Optional contact phone number — may be None if never provided.
    # Typed as DamalPhoneNumber rather than str so the value read back out
    # of the database is held to the same E.164 contract it went in under.
    phone: Optional[DamalPhoneNumber] = None

    # Optional contact email address — may be None if never provided.
    email: Optional[EmailStr] = None

    # Government ID number — None for tenants who never provided one.
    national_id: Optional[str] = None

    # The staff user who created this tenant, as a plain foreign key.
    # Optional so rows whose creating User was later deleted (the FK is
    # ON DELETE SET NULL) can still be serialised.
    created_by: Optional[int] = None

    # UTC timestamp of when the row was first created.
    created_at: datetime

    # UTC timestamp of the most recent update — None if never updated.
    updated_at: Optional[datetime] = None

    # from_attributes=True lets Pydantic read from SQLAlchemy ORM instances
    # (object.attribute access) instead of only from plain dicts.
    model_config = ConfigDict(from_attributes=True)
