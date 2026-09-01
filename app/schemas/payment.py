"""
Pydantic V2 schemas for the Payment resource.

Schema hierarchy:
  PaymentBase     — shared input fields for recording money received
  PaymentCreate   — POST /payments, any logged-in staff
  PaymentCancel   — PATCH /payments/{id}/cancel
  PaymentResponse — public read schema returned by every payment endpoint

Note the deliberate absence of a PaymentUpdate: Payment has no general update
endpoint anywhere in this project, by design. See PaymentCancel (Section 4)
for the reasoning.

These schemas sit at the HTTP boundary — they are the gate between raw client
JSON and the service layer. No business logic, no DB access here.
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import date, datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

# PaymentMethod is reused directly from the model, not redefined here — one
# definition shared across the database layer and the API layer, so the enum
# can never drift between what the DB accepts and what the API emits. Same
# pattern as ChargeCategory in schemas/charge.py, UnitType/UnitStatus in
# schemas/unit.py and OwnerType in schemas/owner.py.
from app.models.payment import PaymentMethod


# =============================================================================
# SECTION 2 — PaymentBase
# =============================================================================
class PaymentBase(BaseModel):
    """
    Shared foundation for PaymentCreate.

    Never used directly as a request or response type — it exists to hold the
    input fields for recording money received in one place.

    amount uses Decimal, NEVER float. Binary floats cannot represent decimal
    fractions exactly (0.1 + 0.2 != 0.3), and those tiny errors accumulate
    across many rows into real accounting discrepancies. This mirrors
    Numeric(12, 2) on the model — exactness is the whole point, at both
    layers.

    What this schema CANNOT check: the real rule is that amount must EXACTLY
    EQUAL the charge's amount, because partial payments are not permitted.
    A schema has no access to the Charge row — it can see charge_id but not
    the charge's amount — so all that is expressible here is amount > 0. The
    equality check lives in PaymentService, which can fetch the charge and
    compare. Same division of labour as the model, which likewise can only
    guarantee the precision, not the match.

    method and reference are INDEPENDENTLY optional, and no validator links
    the two. A reference without a method is deliberately allowed: a staff
    member might record an M-Pesa code without selecting the method from a
    dropdown, and rejecting that would discard genuinely useful information
    over a formality. The reference is the part that lets a payment be traced
    back to the real-world transaction; the method is the part that can be
    inferred later from the reference's format.

    paid_at is when the money was actually RECEIVED, deliberately distinct
    from created_at (when a staff member entered the record). These genuinely
    differ when a payment is logged a day or two late — cash handed over on a
    Friday and entered the following Monday — and backdating paid_at is the
    correct response, not a workaround.
    """

    # The charge this money settles. Required — a payment that settles no
    # charge has no meaning in this domain.
    charge_id: int

    # The money received. gt=0 rejects zero and negative payments;
    # max_digits / decimal_places mirror Numeric(12, 2) on the model.
    amount: Decimal = Field(gt=0, max_digits=12, decimal_places=2)

    # The date the money actually arrived — a plain date, because the banking
    # day is the meaningful unit here.
    paid_at: date

    # How the money arrived. Optional by design: money known to have arrived
    # should never be blocked from being recorded just because the channel
    # wasn't captured at the time.
    method: Optional[PaymentMethod] = None

    # The external identifier — M-Pesa transaction code, bank slip number, or
    # office receipt number. max_length=100 mirrors String(100) on the model.
    reference: Optional[str] = Field(default=None, max_length=100)


# =============================================================================
# SECTION 3 — PaymentCreate
# =============================================================================
class PaymentCreate(PaymentBase):
    """
    Schema for recording a new payment — POST /payments.

    Open to ANY logged-in staff member, not admins only: recording money that
    has been received is routine front-desk work, and the service layer's
    amount-equals-charge check already prevents a malformed payment from
    being recorded.

    Deliberately has NO "created_by" field. The service sets it from the
    authenticated staff member making the request, never from client input —
    the same defensive pattern as excluding is_superuser from UserCreate and
    created_by from ChargeCreate: the field literally does not exist here for
    a client to send, and Pydantic ignores unknown fields by default. It
    matters more here than elsewhere, since the service reads created_by back
    to decide who may later void the row.

    Deliberately has NO "is_cancelled" field either. New payments are never
    created already-cancelled; voiding is a separate, deliberate action with
    its own endpoint, its own schema (PaymentCancel, Section 4) and its own
    permission rules.
    """

    pass


# =============================================================================
# SECTION 4 — PaymentCancel
# =============================================================================
class PaymentCancel(BaseModel):
    """
    Schema for voiding a payment — PATCH /payments/{id}/cancel.

    A deliberately minimal schema for a deliberately narrow action: one field,
    nothing else can ride along with the request because nothing else exists
    on this schema to send.

    Payment has NO delete endpoint and NO general update endpoint anywhere in
    this project, by design. With the amount forced to equal the charge's
    amount, and method/reference being incidental detail, there is genuinely
    nothing meaningful to edit — void the mistaken record and re-record it
    correctly instead. That is why there is no PaymentUpdate class in this
    file, in contrast to ChargeUpdate.

    Cancelling frees the charge's single active-payment slot — the partial
    unique index on the model excludes cancelled rows from its uniqueness
    check — so a corrected payment can be recorded against that charge, while
    the mistaken record survives permanently in the audit trail. Financial
    records are never destroyed, only voided.

    Permission rules for this action are enforced in PaymentService, NOT here
    — a schema sees only the request body, never who is making the request or
    when the row was created. An admin may void any payment; a non-admin
    staff member may only void a payment they themselves recorded, and only
    within 24 hours of recording it (measured against created_by and
    created_at on the row).
    """

    # Defaults to True: the endpoint's whole purpose is cancelling, so an
    # empty request body still expresses the intended action.
    is_cancelled: bool = True


# =============================================================================
# SECTION 5 — PaymentResponse
# =============================================================================
class PaymentResponse(BaseModel):
    """
    Public-facing read schema — used as response_model on every endpoint
    returning a payment.

    created_by is included: it is non-sensitive audit information (a plain
    user id, no personal detail), and it is genuinely load-bearing here rather
    than merely informational — the service reads it, together with
    created_at, to decide whether a non-admin may void this payment. A client
    that can see both fields can tell in advance whether the cancel action is
    available to the current user, instead of discovering it through a 403.

    from_attributes=True lets Pydantic read directly from the SQLAlchemy
    Payment object returned by the repository/service, same mechanism as
    UserResponse, OwnerResponse, UnitResponse and ChargeResponse.

    Does not inherit from PaymentBase because it is a completely independent
    read schema — it represents what we expose, not what we accept, and it
    carries none of the input constraints (data already in the database is
    not re-checked on the way out).
    """

    # Primary key — always present on a persisted payment.
    id: int

    # The charge this payment settles, as a plain foreign key.
    charge_id: int

    # The money received, exact to two decimal places.
    amount: Decimal

    # The date the money actually arrived.
    paid_at: date

    # How the money arrived — serialises to its string value ("mpesa" /
    # "bank_transfer" / "cash") because PaymentMethod inherits from str.
    # None when the channel wasn't captured.
    method: Optional[PaymentMethod] = None

    # The external transaction identifier — None when it wasn't captured.
    reference: Optional[str] = None

    # Whether this payment has been voided. A cancelled row no longer
    # occupies its charge's single active-payment slot.
    is_cancelled: bool

    # The staff user who recorded this payment, as a plain foreign key.
    # Optional because the column is SET NULL when that user account is
    # deleted — which also means no non-admin can ever self-void that row.
    created_by: Optional[int] = None

    # UTC timestamp of when the row was entered — distinct from paid_at, and
    # the timestamp the 24-hour self-void window is measured against.
    created_at: datetime

    # UTC timestamp of the most recent update — None if never updated.
    updated_at: Optional[datetime] = None

    # from_attributes=True lets Pydantic read from SQLAlchemy ORM instances
    # (object.attribute access) instead of only from plain dicts.
    model_config = ConfigDict(from_attributes=True)
