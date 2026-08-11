"""
Pydantic V2 schemas for the Unit resource.

Schema hierarchy:
  UnitBase          — shared structural input fields (unit_number, floor,
                      unit_type, bedrooms, size)
  UnitCreate        — extends UnitBase with an explicit owner_id (POST /units)
  UnitUpdate        — all structural fields optional (PATCH /units/{id})
  UnitStatusUpdate  — status only (PATCH /units/{id}/status)
  UnitResponse      — public read schema returned by every unit endpoint

These schemas sit at the HTTP boundary — they are the gate between raw client
JSON and the service layer. No business logic, no DB access here.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# UnitType and UnitStatus are reused directly from the model, not redefined
# here — one definition shared across the database layer and the API layer, so
# the enums can never drift between what the DB accepts and what the API emits.
# Same pattern as OwnerType in schemas/owner.py.
from app.models.unit import UnitStatus, UnitType


# =============================================================================
# SECTION 2 — UnitBase
# =============================================================================
class UnitBase(BaseModel):
    """
    Shared foundation for UnitCreate and UnitUpdate's structural fields.

    Never used directly as a request or response type — it exists purely to
    avoid repeating the same fields across the input schemas.
    """

    # Human-readable identifier, unique across the entire building
    # (e.g. "B-01", "G-03", "5-2A"). max_length matches String(50) on the model.
    unit_number: str = Field(min_length=1, max_length=50)

    # Which floor the unit is on: basement=-1, ground=0, 1st=1, upwards.
    # Deliberately unbounded and signed — the basement is a real, valid floor.
    floor: int

    # What kind of unit this is. Reused from the model's UnitType enum.
    unit_type: UnitType

    # Number of bedrooms. None means "the concept does not apply to this unit
    # type" — see validate_bedrooms_for_type below for which combinations of
    # unit_type and bedrooms are actually accepted.
    bedrooms: Optional[int] = None

    # Square footage of the unit, optional. gt=0 rejects zero and negative
    # areas, which are physically meaningless.
    size: Optional[float] = Field(default=None, gt=0)

    # -------------------------------------------------------------------------
    # validate_bedrooms_for_type
    #
    # mode="after" runs once every individual field has already passed its own
    # basic validation, so by the time this method executes, unit_type is
    # guaranteed to be a real UnitType member and bedrooms a real int or None.
    # That means this validator never has to defend against garbage types — it
    # only checks the COMBINATION of the two fields, which is the one thing
    # per-field validation structurally cannot express.
    #
    # The rules encoded here:
    #
    #   - LODGE requires 1 or 2 bedrooms (NOT 0). This reflects the actual
    #     building design as confirmed via the site engineer and the floor
    #     plans: lodge units are complete, self-contained 1BR or 2BR units,
    #     not zero-bedroom studios. This corrects an earlier, incorrect
    #     assumption made during initial design, when lodges were mistakenly
    #     modelled as studio-like 0-bedroom units.
    #
    #   - SHOP, OFFICE and RESTAURANT explicitly forbid ANY bedrooms value —
    #     not zero, not null-with-a-caveat, but the absence of the field
    #     entirely (bedrooms must be None). The concept of a bedroom does not
    #     apply to these commercial unit types at all, so sending 0 is just as
    #     wrong as sending 3: it would assert that the question was asked and
    #     answered, when the question is meaningless here.
    #
    #   - APARTMENT requires 2 or 3 specifically, matching the actual
    #     building's apartment floors — no other apartment layout exists in
    #     this building.
    # -------------------------------------------------------------------------
    @model_validator(mode="after")
    def validate_bedrooms_for_type(self):
        """Reject bedroom counts that are invalid for the given unit type."""
        if self.unit_type == UnitType.LODGE and self.bedrooms not in (1, 2):
            raise ValueError("Lodge units must have 1 or 2 bedrooms")
        if (
            self.unit_type in (UnitType.SHOP, UnitType.OFFICE, UnitType.RESTAURANT)
            and self.bedrooms is not None
        ):
            raise ValueError(f"{self.unit_type.value} units cannot have a bedrooms value")
        if self.unit_type == UnitType.APARTMENT and self.bedrooms not in (2, 3):
            raise ValueError("Apartment units must have 2 or 3 bedrooms")
        return self


# =============================================================================
# SECTION 3 — UnitCreate
# =============================================================================
class UnitCreate(UnitBase):
    """
    Schema for creating a new unit — POST /units, admin only.

    owner_id is REQUIRED and explicit. The admin creating the unit always
    decides ownership directly at creation time: the company's own owner id
    for a unit that is currently unsold, or a real buyer's owner id if the
    unit has already been sold. There is deliberately NO automatic or default
    owner_id logic anywhere in this schema or in the service — every unit
    creation must state ownership plainly, so a unit can never end up
    silently attributed to the wrong party.

    Deliberately has NO "status" field. New units ALWAYS default to
    UnitStatus.AVAILABLE, set by the service layer — never chosen by the
    client at creation time.

    Deliberately has NO "created_by" field either. The service sets it from
    the authenticated admin making the request (via
    get_current_active_superuser), never from client input. This is the same
    defensive pattern as excluding is_superuser from UserCreate and type from
    OwnerCreate: the field literally does not exist here for a client to
    send, and Pydantic ignores unknown fields by default.
    """

    # The owner this unit belongs to — a real individual owner, or the single
    # row representing the developer company for units not yet sold.
    owner_id: int


# =============================================================================
# SECTION 4 — UnitUpdate
# =============================================================================
class UnitUpdate(BaseModel):
    """
    Schema for partial unit updates — PATCH /units/{id}, admin only,
    structural fields.

    All fields are optional: only the fields the client actually sends get
    updated (the repository uses exclude_unset=True, same as UserUpdate and
    OwnerUpdate). Omitting a field means "do not change this field."

    Deliberately excludes "status". Status changes go through the SEPARATE
    UnitStatusUpdate schema and its own endpoint, which is open to any
    logged-in staff member rather than admins only — see Section 5.
    """

    # If omitted, the unit number is not changed. min_length=1 still rejects
    # an explicitly sent empty string.
    unit_number: Optional[str] = Field(default=None, min_length=1, max_length=50)

    # If omitted, the floor is not changed.
    floor: Optional[int] = None

    # If omitted, the unit type is not changed.
    unit_type: Optional[UnitType] = None

    # If omitted, the bedroom count is not changed.
    bedrooms: Optional[int] = None

    # If omitted, the size is not changed.
    size: Optional[float] = Field(default=None, gt=0)

    # If omitted, ownership is not changed. Sending a value here reassigns the
    # unit to a different owner — e.g. when an unsold company-owned unit is
    # bought by an individual.
    owner_id: Optional[int] = None

    # -------------------------------------------------------------------------
    # validate_bedrooms_for_type
    #
    # The same cross-field rules as UnitBase (see the detailed comment block in
    # Section 2 for why each rule exists), with one adjustment for the partial
    # nature of a PATCH: the check only runs when unit_type is actually
    # provided.
    #
    # Rationale: on a partial update, unit_type being None means "the client is
    # not changing the unit type in this call". Validating bedrooms against a
    # None unit_type is impossible, and forcing the client to re-send
    # unit_type just to satisfy a bedrooms check they are not making would
    # defeat the point of a partial update. When unit_type IS sent, both
    # fields are checked together exactly as in UnitBase.
    # -------------------------------------------------------------------------
    @model_validator(mode="after")
    def validate_bedrooms_for_type(self):
        """Reject invalid bedrooms/unit_type combinations, only when unit_type is sent."""
        if self.unit_type is None:
            return self
        if self.unit_type == UnitType.LODGE and self.bedrooms not in (1, 2):
            raise ValueError("Lodge units must have 1 or 2 bedrooms")
        if (
            self.unit_type in (UnitType.SHOP, UnitType.OFFICE, UnitType.RESTAURANT)
            and self.bedrooms is not None
        ):
            raise ValueError(f"{self.unit_type.value} units cannot have a bedrooms value")
        if self.unit_type == UnitType.APARTMENT and self.bedrooms not in (2, 3):
            raise ValueError("Apartment units must have 2 or 3 bedrooms")
        return self


# =============================================================================
# SECTION 5 — UnitStatusUpdate
# =============================================================================
class UnitStatusUpdate(BaseModel):
    """
    Schema for changing only a unit's occupancy status —
    PATCH /units/{id}/status.

    Open to ANY logged-in staff member, not just admins. This is the
    day-to-day operational endpoint: marking a unit under_maintenance,
    tenant_occupied, owner_occupied, or back to available. It is deliberately
    kept separate from UnitUpdate's structural fields (unit_number, floor,
    unit_type, bedrooms, size, owner_id), which remain admin-only — routine
    status bookkeeping should not require an admin, and staff should not be
    able to reassign ownership or rewrite a unit's physical description.

    Contains ONLY status. No other field can be changed through this endpoint,
    because no other field exists on this schema to send.
    """

    # The new occupancy state for the unit. Required — this schema exists
    # solely to carry it.
    status: UnitStatus


# =============================================================================
# SECTION 6 — UnitResponse
# =============================================================================
class UnitResponse(BaseModel):
    """
    Public-facing read schema — used as response_model on every endpoint
    returning a unit.

    owner_id and created_by are returned as plain integers here, not as nested
    Owner/User objects. The Unit model has no relationship() configured for
    eager-loading those, so exposing them as raw foreign keys keeps this
    simple for now and avoids accidental lazy-load queries at serialization
    time. A client wanting full owner details calls GET /owners/{owner_id}
    separately.

    from_attributes=True lets Pydantic read directly from the SQLAlchemy Unit
    object returned by the repository/service, same mechanism as UserResponse
    and OwnerResponse.

    Does not inherit from UnitBase because it is a completely independent read
    schema — it represents what we expose, not what we accept, and it carries
    no cross-field validators (data already in the database is not re-checked
    on the way out).
    """

    # Primary key — always present on a persisted unit.
    id: int

    # Human-readable identifier, unique across the building.
    unit_number: str

    # Which floor the unit is on: basement=-1, ground=0, upwards.
    floor: int

    # What kind of unit this is — serialises to its string value
    # ("shop" / "office" / "restaurant" / "lodge" / "apartment") because
    # UnitType inherits from str.
    unit_type: UnitType

    # Number of bedrooms — None for commercial unit types where the concept
    # does not apply.
    bedrooms: Optional[int] = None

    # Square footage — None if never recorded.
    size: Optional[float] = None

    # Current occupancy state — serialises to its string value because
    # UnitStatus inherits from str.
    status: UnitStatus

    # The owner this unit belongs to, as a plain foreign key.
    owner_id: int

    # The admin user who created this unit, as a plain foreign key. Optional
    # so rows created outside the normal API flow (e.g. a seed migration) can
    # still be serialised.
    created_by: Optional[int] = None

    # UTC timestamp of when the row was first created.
    created_at: datetime

    # UTC timestamp of the most recent update — None if never updated.
    updated_at: Optional[datetime] = None

    # from_attributes=True lets Pydantic read from SQLAlchemy ORM instances
    # (object.attribute access) instead of only from plain dicts.
    model_config = ConfigDict(from_attributes=True)
