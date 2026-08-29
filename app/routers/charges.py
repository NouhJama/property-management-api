"""
Charge router — the HTTP layer for all charge operations.

Exposes the charge endpoints and connects incoming HTTP requests to the
ChargeService. No business logic lives here — every handler only wires the
request to the service layer (via dependency injection for both assembly
and auth) and returns the result:

  POST   /api/v1/charges                            — issue a new bill.
  GET    /api/v1/charges                            — list every charge.
  GET    /api/v1/charges/unit/{unit_id}             — one unit's history
                                                      across a period range.
  GET    /api/v1/charges/unit/{unit_id}/total       — one unit's summed total
                                                      across a period range.
  GET    /api/v1/charges/tenant/{tenant_id}         — what a tenant owes for
                                                      one month.
  GET    /api/v1/charges/owner/{owner_id}           — what an owner owes for
                                                      one month.
  GET    /api/v1/charges/{charge_id}                — fetch a single charge.
  PATCH  /api/v1/charges/{charge_id}                — correct a charge.
  PATCH  /api/v1/charges/{charge_id}/cancel         — void a charge.

Permission split:
  CREATE and every READ are open to ANY authenticated staff member
  (get_current_user). BOTH patch routes — the correction and the cancel —
  are ADMIN ONLY (get_current_active_superuser).

  Rationale: issuing a bill is routine staff work, the kind of thing that
  happens on a schedule at the start of every month and should not need an
  admin standing by. Changing what someone already owes, or voiding a bill
  outright, is high-stakes in a way raising one is not — those two stay
  behind the superuser guard.

NO DELETE ROUTE, ANYWHERE — by design, not by omission:
  Financial records are voided, never destroyed. PATCH
  /charges/{charge_id}/cancel is this router's counterpart to the DELETE
  endpoints on Owner, Unit and Tenant: the row survives with
  is_cancelled=True, so both facts — that the bill was raised, and that it
  was later voided — stay recoverable forever. The audit trail must always
  survive. This matches ChargeService and ChargeRepository, neither of
  which has a delete method either; no delete should ever be added at any
  of the three layers.

Note on style: this file uses the Annotated[Type, Depends(...)] syntax
throughout — FastAPI's current recommendation, matching units.py,
tenants.py and get_charge_service in dependencies.py.

Architecture position:
  routers (HTTP, this file) → services (business logic) → repositories → models
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.core.dependencies import (
    get_charge_service,
    get_current_active_superuser,
    get_current_user,
)
from app.models.user import User
from app.schemas.charge import (
    ChargeCancel,
    ChargeCreate,
    ChargeResponse,
    ChargeTotalResponse,
    ChargeUpdate,
)
from app.services.charge_service import ChargeService

# =============================================================================
# SECTION 2 — Router object
# =============================================================================
# prefix "/charges" combines with the "/api/v1" prefix added in main.py to
# produce /api/v1/charges/... .
router = APIRouter(prefix="/charges", tags=["Charges"])


# =============================================================================
# SECTION 3 — POST /charges
# =============================================================================
# 201 Created is the correct status for resource creation, matching the
# owner, unit, tenant and register endpoints' convention.
@router.post(
    "",
    response_model=ChargeResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Issue a new charge",
)
async def create_charge(
    payload: ChargeCreate,
    service: Annotated[ChargeService, Depends(get_charge_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChargeResponse:
    """
    Issue a new monthly bill — open to any logged-in staff member.

    Deliberately NOT admin-only. Raising the month's charges is routine,
    high-volume operational work; gating it behind an admin would stall
    ordinary billing for no security benefit. The high-stakes actions on
    this router — correcting an existing charge and voiding one — are the
    ones that keep the superuser guard.

    created_by is taken from the authenticated staff member making the
    request, never from the payload — ChargeCreate has no such field, and
    the service requires the id as an explicit argument.

    The category/party pairing rule (service_charge needs an owner and a
    percentage; rent/water/move_out need a tenant) is enforced by
    ChargeCreate at the schema layer, so it has already passed before this
    handler runs.

    Args:
        payload: The validated charge-creation data from the client.
        service: The request-scoped ChargeService.
        current_user: The authenticated User resolved from the JWT. Its id
            is recorded on the new row as created_by, the audit trail of
            which staff member raised this bill.

    Returns:
        The newly created charge, serialised through ChargeResponse.

    Raises:
        HTTPException: 400 if unit_id, owner_id or tenant_id does not match
            an existing record (raised by the service).
    """
    return await service.create_charge(payload, created_by=current_user.id)


# =============================================================================
# SECTION 4 — GET /charges
# =============================================================================
@router.get(
    "",
    response_model=list[ChargeResponse],
    summary="List all charges",
)
async def list_charges(
    service: Annotated[ChargeService, Depends(get_charge_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChargeResponse]:
    """
    Return every charge, newest period first.

    Open to any logged-in staff member — staff need to see billing records
    for day-to-day work, same reasoning as the owner, unit and tenant list
    endpoints.

    Cancelled charges ARE included. This is the unfiltered view, and a
    voided bill is still part of the record.

    Args:
        service: The request-scoped ChargeService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        All charges, serialised through ChargeResponse. An empty list if no
        charges exist.
    """
    return await service.get_all_charges()


# =============================================================================
# SECTION 5 — GET /charges/unit/{unit_id}
# =============================================================================
@router.get(
    "/unit/{unit_id}",
    response_model=list[ChargeResponse],
    summary="List a unit's charges across a period range",
)
async def list_charges_by_unit(
    unit_id: int,
    start: Annotated[date, Query(description="First billing period, inclusive")],
    end: Annotated[date, Query(description="Last billing period, inclusive")],
    service: Annotated[ChargeService, Depends(get_charge_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChargeResponse]:
    """
    Return all charges for one unit across a range of billing periods.

    The range is INCLUSIVE on both ends — a charge dated exactly `start` or
    exactly `end` is returned. A single-month query is expressed by passing
    the same date for both.

    Cancelled charges ARE included: this is the history view, and a voided
    bill is part of the history. Contrast the total endpoint below, which
    excludes them because it answers "what is owed" rather than "what
    happened".

    Args:
        unit_id: The integer primary key of the unit being billed.
        start: First day of the earliest billing period to include.
        end: First day of the latest billing period to include.
        service: The request-scoped ChargeService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The unit's charges in that window, oldest period first, serialised
        through ChargeResponse. An empty list if nothing was billed.

    Raises:
        HTTPException: 400 if start is later than end (raised by the
            service, so a malformed range is never mistaken for an empty
            result).
    """
    return await service.get_charges_by_unit_and_period_range(unit_id, start, end)


# =============================================================================
# SECTION 6 — GET /charges/unit/{unit_id}/total
# =============================================================================
@router.get(
    "/unit/{unit_id}/total",
    response_model=ChargeTotalResponse,
    summary="Total a unit's charges across a period range",
)
async def get_unit_charge_total(
    unit_id: int,
    start: Annotated[date, Query(description="First billing period, inclusive")],
    end: Annotated[date, Query(description="Last billing period, inclusive")],
    service: Annotated[ChargeService, Depends(get_charge_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChargeTotalResponse:
    """
    Return the summed total for one unit across a range of billing periods.

    Cancelled charges are EXCLUDED from the sum — handled in the repository
    query, not here. A voided bill is not money owed, which is the whole
    difference between this endpoint and the history endpoint above.

    The sum is computed by PostgreSQL, and comes back as Decimal("0.00")
    rather than null when nothing matches, so a client never has to defend
    against a missing figure.

    The response echoes unit_id, start and end back alongside the total, so
    the payload is self-describing rather than a bare number — see
    ChargeTotalResponse. That schema is constructed here, directly from the
    service's Decimal return value, because there is no ORM object to read
    it off.

    Args:
        unit_id: The integer primary key of the unit being billed.
        start: First day of the earliest billing period to include.
        end: First day of the latest billing period to include.
        service: The request-scoped ChargeService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        A ChargeTotalResponse carrying the query inputs and the total.

    Raises:
        HTTPException: 400 if start is later than end (raised by the
            service — a backwards range must not come back as an
            authoritative-looking 0.00).
    """
    total = await service.get_total_by_unit_and_period_range(unit_id, start, end)
    return ChargeTotalResponse(unit_id=unit_id, start=start, end=end, total=total)


# =============================================================================
# SECTION 7 — GET /charges/tenant/{tenant_id}
# =============================================================================
@router.get(
    "/tenant/{tenant_id}",
    response_model=list[ChargeResponse],
    summary="List a tenant's charges for one period",
)
async def list_charges_by_tenant(
    tenant_id: int,
    period: Annotated[date, Query(description="The billing period, e.g. 2026-03-01")],
    service: Annotated[ChargeService, Depends(get_charge_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChargeResponse]:
    """
    Return what this tenant owes for one specific month.

    Takes a single `period`, not a range — this answers a point-in-time
    question, unlike the unit endpoints above.

    Typically returns SEVERAL rows: a tenant normally owes rent AND water
    for the same period, and may additionally owe a one-off move_out charge
    in their final month. Each category is its own bill.

    An empty list is a legitimate answer meaning "nothing was billed to this
    tenant that month" — it is not a 404.

    Args:
        tenant_id: The integer primary key of the tenant.
        period: The first day of the billing month (e.g. 2026-03-01).
        service: The request-scoped ChargeService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The tenant's charges for that period, ordered by category,
        serialised through ChargeResponse.
    """
    return await service.get_charges_by_tenant_and_period(tenant_id, period)


# =============================================================================
# SECTION 8 — GET /charges/owner/{owner_id}
# =============================================================================
@router.get(
    "/owner/{owner_id}",
    response_model=list[ChargeResponse],
    summary="List an owner's charges for one period",
)
async def list_charges_by_owner(
    owner_id: int,
    period: Annotated[date, Query(description="The billing period, e.g. 2026-03-01")],
    service: Annotated[ChargeService, Depends(get_charge_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> list[ChargeResponse]:
    """
    Return what this owner owes for one specific month.

    Same shape as the tenant endpoint above — a single `period`, not a
    range — but multi-row for a different reason. A tenant's several rows
    are several CATEGORIES against one unit; an owner's several rows are
    usually the SAME category (service_charge) across SEVERAL UNITS they
    own.

    An empty list is a legitimate answer, not a 404.

    Args:
        owner_id: The integer primary key of the owner.
        period: The first day of the billing month (e.g. 2026-03-01).
        service: The request-scoped ChargeService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The owner's charges for that period, ordered by category,
        serialised through ChargeResponse.
    """
    return await service.get_charges_by_owner_and_period(owner_id, period)


# =============================================================================
# SECTION 9 — GET /charges/{charge_id}
# =============================================================================
# ⚠️ ROUTE ORDERING — this route MUST stay declared AFTER the /unit, /tenant
# and /owner routes above (Sections 5–8). FastAPI matches routes in
# DECLARATION ORDER, not by specificity: if "/charges/{charge_id}" came
# first, a request for "/charges/unit/12" would match it, FastAPI would try
# to parse "unit" as the integer charge_id, and the client would get a
# confusing 422 validation error instead of the unit's charges. Moving this
# handler above any of them silently breaks four endpoints, so keep it last
# among the GETs.
@router.get(
    "/{charge_id}",
    response_model=ChargeResponse,
    summary="Get a single charge by id",
)
async def get_charge(
    charge_id: int,
    service: Annotated[ChargeService, Depends(get_charge_service)],
    current_user: Annotated[User, Depends(get_current_user)],
) -> ChargeResponse:
    """
    Fetch a single charge by primary key.

    Open to any logged-in staff member, same reasoning as the list
    endpoints.

    Args:
        charge_id: The integer primary key of the target charge.
        service: The request-scoped ChargeService.
        current_user: The authenticated User resolved from the JWT.

    Returns:
        The matching charge, serialised through ChargeResponse.

    Raises:
        HTTPException: 404 if no charge with this id exists (raised by the
            service).
    """
    return await service.get_charge_by_id(charge_id)


# =============================================================================
# SECTION 10 — PATCH /charges/{charge_id}
# =============================================================================
@router.patch(
    "/{charge_id}",
    response_model=ChargeResponse,
    summary="Correct a charge's amount, percentage or period",
)
async def update_charge(
    charge_id: int,
    payload: ChargeUpdate,
    service: Annotated[ChargeService, Depends(get_charge_service)],
    admin: Annotated[User, Depends(get_current_active_superuser)],
) -> ChargeResponse:
    """
    Correct a charge's amount, percentage or period — ADMIN ONLY.

    Changing what someone already owes is high-stakes in a way that raising
    a routine monthly bill is not, so this keeps the superuser guard that
    POST /charges deliberately does not have.

    category, unit_id, owner_id and tenant_id are NOT updatable at all —
    ChargeUpdate carries only the three correctable fields, so nothing else
    can be reached through this endpoint. Reassigning a charge's category or
    party would rewrite financial history rather than correct a typo in it;
    the right move there is to cancel the wrong charge and issue a correct
    one.

    FETCH-THEN-ACT pattern: this route fetches the charge first via
    get_charge_by_id (which raises 404 if it does not exist), THEN passes
    that fetched Charge object into update_charge. The service takes a
    Charge instance, not an id — existence is confirmed before any write
    happens, same as units.py and tenants.py.

    Only the fields the client actually sent are written (exclude_unset=True
    in the repository); omitted fields are left untouched.

    Args:
        charge_id: The integer primary key of the charge to correct.
        payload: The validated partial-update data from the client.
        service: The request-scoped ChargeService.
        admin: The authenticated superuser — presence of this dependency is
            what enforces admin-only access (403 for non-admins).

    Returns:
        The updated charge, serialised through ChargeResponse.

    Raises:
        HTTPException: 404 if no charge with this id exists (raised during
            the fetch step); 409 if the charge has already been cancelled —
            a voided record is closed and must not be edited.
    """
    charge = await service.get_charge_by_id(charge_id)
    return await service.update_charge(charge, payload)


# =============================================================================
# SECTION 11 — PATCH /charges/{charge_id}/cancel
# =============================================================================
@router.patch(
    "/{charge_id}/cancel",
    response_model=ChargeResponse,
    summary="Void a charge",
)
async def cancel_charge(
    charge_id: int,
    payload: ChargeCancel,
    service: Annotated[ChargeService, Depends(get_charge_service)],
    admin: Annotated[User, Depends(get_current_active_superuser)],
) -> ChargeResponse:
    """
    Void a charge — ADMIN ONLY.

    This endpoint REPLACES delete entirely; there is no DELETE route on this
    router, and there must never be one. The row survives with
    is_cancelled=True, so the audit trail stays intact: what was billed, and
    the fact that it was later voided, both remain recoverable forever.
    Cancelled charges are excluded from total calculations, so voiding a
    bill still removes it from what is owed.

    Deliberately a PATCH, not a DELETE. Mapping this to DELETE would
    misrepresent what actually happens — nothing is removed — and would
    undermine the audit-trail design by advertising destruction the system
    never performs.

    Fetch-then-act, same as the correction endpoint above: the charge is
    loaded first (404 if missing), then handed to the service.

    Args:
        charge_id: The integer primary key of the charge to void.
        payload: The validated ChargeCancel flag. That schema carries ONLY
            is_cancelled and defaults it to True, so an empty request body
            still expresses the intended action.
        service: The request-scoped ChargeService.
        admin: The authenticated superuser — enforces admin-only access.

    Returns:
        The voided charge, serialised through ChargeResponse.

    Raises:
        HTTPException: 404 if no charge with this id exists (raised during
            the fetch step); 409 if the charge is already cancelled —
            silently succeeding on a no-op would misinform the staff member
            about a financial record's true state.
    """
    charge = await service.get_charge_by_id(charge_id)
    return await service.cancel_charge(charge, payload)
