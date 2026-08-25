"""
Pydantic V2 schemas for the Charge resource.

Schema hierarchy:
  ChargeBase      — shared input fields plus the category/party pairing rule
  ChargeCreate    — POST /charges, any logged-in staff
  ChargeUpdate    — PATCH /charges/{id}, admin only, amount/percentage/period
  ChargeCancel    — PATCH /charges/{id}/cancel, admin only
  ChargeResponse  — public read schema returned by every charge endpoint

These schemas sit at the HTTP boundary — they are the gate between raw client
JSON and the service layer. No business logic, no DB access here.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# ChargeCategory is reused directly from the model, not redefined here — one
# definition shared across the database layer and the API layer, so the enum
# can never drift between what the DB accepts and what the API emits. Same
# pattern as UnitType/UnitStatus in schemas/unit.py and OwnerType in
# schemas/owner.py.
from app.models.charge import ChargeCategory


# =============================================================================
# SECTION 2 — ChargeBase
# =============================================================================
class ChargeBase(BaseModel):
    """
    Shared foundation for ChargeCreate.

    Never used directly as a request or response type — it exists to hold the
    input fields and the category/party pairing rule in one place.

    amount uses Decimal, NEVER float. Binary floats cannot represent decimal
    fractions exactly (0.1 + 0.2 != 0.3), and those tiny errors accumulate
    across many rows into real accounting discrepancies. This mirrors
    Numeric(12, 2) on the model — exactness is the whole point, at both
    layers.
    """

    # The unit this bill is raised against. Required — a bill that belongs to
    # no unit is meaningless.
    unit_id: int

    # What this bill is for, and therefore which party owes it.
    category: ChargeCategory

    # The money owed. gt=0 rejects zero and negative bills; max_digits /
    # decimal_places mirror Numeric(12, 2) on the model.
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

    # The first day of the billing month this charge covers, e.g. 2026-03-01.
    period: date

    # Populated ONLY for service_charge — see the validator below.
    owner_id: Optional[int] = None

    # Populated ONLY for rent/water/move_out — see the validator below.
    tenant_id: Optional[int] = None

    # The rule that produced the amount for a service_charge, e.g. 10.00 for
    # 10% of the agreed rent. Mirrors Numeric(5, 2) on the model; le=100
    # because a service charge above 100% of rent is not a real scenario.
    percentage: Optional[Decimal] = Field(
        default=None, gt=0, le=100, max_digits=5, decimal_places=2
    )

    # -------------------------------------------------------------------------
    # validate_party_for_category
    #
    # mode="after" runs once every individual field has already passed its own
    # basic validation, so by the time this method executes, category is
    # guaranteed to be a real ChargeCategory member and the id fields real ints
    # or None. That means this validator never has to defend against garbage
    # types — it only checks the COMBINATION of category and the party fields,
    # which is the one thing per-field validation structurally cannot express.
    #
    # This is where the "exactly one of owner_id/tenant_id" rule is genuinely
    # enforced. The database deliberately leaves BOTH columns nullable: a
    # single column cannot conditionally point at two different tables with
    # real referential integrity — that is the polymorphic foreign key
    # anti-pattern, and it buys a fake constraint at the cost of losing the
    # real ones. So the pairing rule lives here at the schema layer instead,
    # exactly the same approach as Unit's bedrooms/unit_type rule: conditional
    # business rules stay in one reviewable place rather than split across
    # Python and DDL.
    #
    # The rules encoded here:
    #
    #   - SERVICE_CHARGE is owed by the OWNER and is derived as a percentage
    #     of the agreed rent. Hence it REQUIRES owner_id and percentage, and
    #     forbids tenant_id. The percentage records how the amount was
    #     derived, for auditing.
    #
    #   - RENT / WATER / MOVE_OUT are owed by the TENANT. They REQUIRE
    #     tenant_id, forbid owner_id, and forbid percentage — none of them is
    #     derived from a rate, so a percentage on those rows would document a
    #     calculation that never happened.
    # -------------------------------------------------------------------------
    @model_validator(mode="after")
    def validate_party_for_category(self):
        """Reject party/percentage combinations that are invalid for the category."""
        tenant_categories = (
            ChargeCategory.RENT,
            ChargeCategory.WATER,
            ChargeCategory.MOVE_OUT,
        )

        if self.category == ChargeCategory.SERVICE_CHARGE:
            if self.owner_id is None:
                raise ValueError("service_charge requires an owner_id")
            if self.tenant_id is not None:
                raise ValueError("service_charge must not have a tenant_id")
            if self.percentage is None:
                raise ValueError("service_charge requires a percentage")
        elif self.category in tenant_categories:
            if self.tenant_id is None:
                raise ValueError(f"{self.category.value} requires a tenant_id")
            if self.owner_id is not None:
                raise ValueError(f"{self.category.value} must not have an owner_id")
            if self.percentage is not None:
                raise ValueError(f"{self.category.value} must not have a percentage")

        return self


# =============================================================================
# SECTION 3 — ChargeCreate
# =============================================================================
class ChargeCreate(ChargeBase):
    """
    Schema for creating a new charge — POST /charges.

    Open to ANY logged-in staff member, not admins only: raising monthly bills
    is routine operational work, and the category/party rules inherited from
    ChargeBase already prevent a malformed charge from being created.

    Deliberately has NO "created_by" field. The service sets it from the
    authenticated staff member making the request, never from client input —
    the same defensive pattern as excluding is_superuser from UserCreate and
    created_by from UnitCreate: the field literally does not exist here for a
    client to send, and Pydantic ignores unknown fields by default.

    Deliberately has NO "is_cancelled" field either. New charges are never
    created already-cancelled; cancelling is a separate, deliberate,
    admin-only action with its own endpoint and its own schema
    (ChargeCancel, Section 5).
    """

    pass


# =============================================================================
# SECTION 4 — ChargeUpdate
# =============================================================================
class ChargeUpdate(BaseModel):
    """
    Schema for partial charge updates — PATCH /charges/{id}, admin only.

    Admin-only at the router level: changing what someone owes is high-stakes
    in a way that raising a routine monthly bill is not.

    All fields are optional — only the fields the client actually sends get
    updated (the repository uses exclude_unset=True, same as UserUpdate,
    OwnerUpdate and UnitUpdate). Omitting a field means "do not change this
    field."

    What is deliberately NOT updatable, and why:

      - "category" CANNOT be changed. Changing a charge from rent to
        service_charge would also require swapping which party owes it,
        effectively making it a different charge entirely. Cancel the wrong
        one and create a correct one instead.

      - "unit_id", "owner_id" and "tenant_id" CANNOT be changed for the same
        reason — a charge belongs to a specific unit and a specific party;
        reassigning it would silently rewrite financial history rather than
        correct it.

      - "is_cancelled" is NOT here. Cancelling has its own dedicated endpoint
        and schema (ChargeCancel, Section 5), keeping that deliberate voiding
        action clearly separate from routine amount corrections.
    """

    # If omitted, the amount is not changed. Same Decimal exactness and
    # Numeric(12, 2) bounds as ChargeBase.amount.
    amount: Optional[Decimal] = Field(default=None, gt=0, max_digits=12, decimal_places=2)

    # If omitted, the percentage is not changed. Same bounds as
    # ChargeBase.percentage.
    percentage: Optional[Decimal] = Field(
        default=None, gt=0, le=100, max_digits=5, decimal_places=2
    )

    # If omitted, the billing period is not changed.
    period: Optional[date] = None


# =============================================================================
# SECTION 5 — ChargeCancel
# =============================================================================
class ChargeCancel(BaseModel):
    """
    Schema for voiding a charge — PATCH /charges/{id}/cancel, admin only.

    A deliberately minimal schema for a deliberately narrow action: one field,
    nothing else can ride along with the request because nothing else exists
    on this schema to send.

    Charge has NO delete endpoint anywhere in this project, by design.
    Financial records are never destroyed, only voided, so the audit trail
    always survives — what was billed, and the fact that it was later
    cancelled, both stay recoverable. This is the ONLY mechanism for voiding
    a charge.
    """

    # Defaults to True: the endpoint's whole purpose is cancelling, so an
    # empty request body still expresses the intended action.
    is_cancelled: bool = True


# =============================================================================
# SECTION 6 — ChargeResponse
# =============================================================================
class ChargeResponse(BaseModel):
    """
    Public-facing read schema — used as response_model on every endpoint
    returning a charge.

    Payment status (paid / partially_paid / unpaid) is deliberately absent. It
    is DERIVED from Payment records rather than stored on Charge, so it can
    never drift out of sync with the money actually received. Payment does not
    exist yet; once it does, a separate enriched response schema may expose
    the derived status alongside these stored fields.

    from_attributes=True lets Pydantic read directly from the SQLAlchemy
    Charge object returned by the repository/service, same mechanism as
    UserResponse, OwnerResponse and UnitResponse.

    Does not inherit from ChargeBase because it is a completely independent
    read schema — it represents what we expose, not what we accept, and it
    carries no cross-field validators (data already in the database is not
    re-checked on the way out).
    """

    # Primary key — always present on a persisted charge.
    id: int

    # The unit this bill was raised against, as a plain foreign key.
    unit_id: int

    # The billed owner — populated only on service_charge rows.
    owner_id: Optional[int] = None

    # The billed tenant — populated only on rent/water/move_out rows.
    tenant_id: Optional[int] = None

    # What this bill is for — serialises to its string value ("rent" /
    # "water" / "service_charge" / "move_out") because ChargeCategory
    # inherits from str.
    category: ChargeCategory

    # The money owed, exact to two decimal places.
    amount: Decimal

    # The rate that produced the amount — None outside service_charge rows.
    percentage: Optional[Decimal] = None

    # The first day of the billing month this charge covers.
    period: date

    # Whether this charge has been voided. The only stored status field.
    is_cancelled: bool

    # The staff user who raised this charge, as a plain foreign key. Optional
    # because the column is SET NULL when that user account is deleted.
    created_by: Optional[int] = None

    # UTC timestamp of when the row was first created.
    created_at: datetime

    # UTC timestamp of the most recent update — None if never updated.
    updated_at: Optional[datetime] = None

    # from_attributes=True lets Pydantic read from SQLAlchemy ORM instances
    # (object.attribute access) instead of only from plain dicts.
    model_config = ConfigDict(from_attributes=True)
