"""
Charge service — the business logic layer for all charge operations.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Enforce business rules (rejecting a backwards period range, setting
    created_by from the authenticated user rather than client input).
  - Translate "not found" and constraint-violation conditions into
    HTTPExceptions with semantically correct status codes for the router.

Out of scope for this file:
  - SQL / SQLAlchemy queries (the repository's job).
  - HTTP request/response handling (the router's job).
  - The category/party pairing rule (the Pydantic schema layer's job — see
    ChargeBase.validate_party_for_category).
  - Creating its own repository or database session (injected in).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import date
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

# Charge is imported as a type annotation only.
# The service never instantiates Charge() or writes queries — that is the
# repository's job. Note the contrast with UnitService, which additionally
# imports UnitStatus as a VALUE to hardcode on create; there is no equivalent
# here, because is_cancelled always starts False at the model level and this
# service never needs to name it.
from app.models.charge import Charge
from app.repositories.charge_repository import ChargeRepository

# ChargeCancel is imported because the repository's cancel() takes BOTH the
# Charge object and a ChargeCancel payload —
# `cancel(self, charge: Charge, payload: ChargeCancel)` — so cancel_charge
# below must accept one to pass through.
from app.schemas.charge import ChargeCancel, ChargeCreate, ChargeUpdate


# =============================================================================
# SECTION 2 — ChargeService class
# =============================================================================
class ChargeService:
    """
    Business logic layer for all charge operations.

    Sits between the router (HTTP) and the repository (database). Enforces
    all rules that determine whether an operation should be allowed and in
    what form:
      - Never writes SQL — always delegates to the repository.
      - Never handles HTTP request/response objects directly.
      - Raises HTTPException for the router to handle.
      - Receives its repository via the constructor — never creates its own
        (dependency injection pattern).

    NO delete_charge method — by design, not by omission:
      This mirrors ChargeRepository, which has no delete() either, and no
      delete method should ever be added at either layer. Financial records
      are never destroyed, only VOIDED via cancel_charge(), which flips
      is_cancelled to True and leaves the row in place. That is what keeps
      the audit trail intact: what was billed, and the fact that it was later
      cancelled, both stay recoverable forever. A hard DELETE would erase the
      evidence that a bill was ever raised at all — indistinguishable from it
      never having existed. If you came here looking for delete_charge(),
      cancel_charge() is the method you want.

      Note the knock-on effect: this service's 409 Conflict paths mean
      something different from those in UnitService.delete_unit and
      OwnerService.delete_owner. A 409 there means "other records still
      reference this row, so it cannot be removed" — a question that only
      arises when something can be removed in the first place. Here it always
      means "this charge is already cancelled": re-cancelling it
      (cancel_charge) or editing it afterwards (update_charge).
    """

    def __init__(self, repository: ChargeRepository) -> None:
        """
        Store the injected repository.

        Args:
            repository: The ChargeRepository this service delegates all
                database access to.
        """
        # Injected by dependencies.py — never instantiated directly
        # inside this class.
        #
        # ONE repository only, deliberately. A Charge references three other
        # tables (unit_id, owner_id, tenant_id), so it is tempting to inject
        # UnitRepository, OwnerRepository and TenantRepository as well and
        # pre-check that each id resolves to a real row. This service does
        # NOT do that. Validating those references is left to PostgreSQL's
        # foreign key constraints, caught as an IntegrityError in
        # create_charge below — the same approach UnitService takes with
        # owner_id. Three extra injected repositories would mean real
        # cross-feature coupling and three extra round-trips per create, to
        # duplicate a guarantee the database already enforces for free.
        self.repo = repository

    # =========================================================================
    # SECTION 3 — create_charge
    # =========================================================================
    async def create_charge(self, payload: ChargeCreate, created_by: int) -> Charge:
        """
        Create a new charge — one monthly bill raised against one unit.

        The category/party pairing rule (service_charge needs an owner and a
        percentage; rent/water/move_out need a tenant) is enforced at the
        Pydantic schema layer via ChargeBase.validate_party_for_category, so
        by the time this method runs the payload is already valid in that
        respect. This service does not re-check it — the rule lives in one
        reviewable place, not two.

        Args:
            payload: The validated charge-creation data from the client.
            created_by: The id of the authenticated staff member raising this
                charge, passed down by the router. A required parameter here —
                every charge created through the API is attributed to the user
                who made the request, and it is never read from the client
                payload (ChargeCreate has no such field).

        Returns:
            The newly created Charge instance.

        Raises:
            HTTPException: 400 if unit_id, owner_id or tenant_id does not
                match an existing record.
        """
        # unit_id, owner_id and tenant_id are all real foreign keys, so an id
        # pointing at no existing row makes the insert fail with an
        # IntegrityError at commit time inside the repository (which rolls
        # back and re-raises it). Translate that into a clean 400 here rather
        # than letting a raw database error surface as an unhandled 500 —
        # the same pattern as UnitService.create_unit's invalid-owner
        # handling.
        #
        # The message is deliberately GENERAL, naming all three fields rather
        # than the one at fault, because an IntegrityError does not identify
        # WHICH constraint failed. Producing a precise per-field message
        # would require injecting Unit/Tenant/Owner repositories to pre-check
        # each id — real cross-feature coupling and extra round-trips on every
        # create — and it would STILL need this catch as a backstop, since a
        # referenced row can be deleted in the window between the check and
        # the insert. Revisit only if the general message proves genuinely
        # confusing in practice.
        #
        # payload fields are read safely inside the except block: payload is a
        # plain Pydantic object, never tracked by the database session, so the
        # rollback cannot expire it. That is why this needs NO
        # capture-before-the-risky-call step — unlike UnitService.delete_unit,
        # where unit.id is a SQLAlchemy-tracked attribute that would trigger a
        # lazy-load (and MissingGreenlet) if read after the rollback.
        try:
            return await self.repo.create(
                unit_id=payload.unit_id,
                category=payload.category,
                amount=payload.amount,
                period=payload.period,
                owner_id=payload.owner_id,
                tenant_id=payload.tenant_id,
                percentage=payload.percentage,
                created_by=created_by,
            )
        except IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Invalid unit_id, owner_id or tenant_id — "
                    "one of the referenced records does not exist."
                ),
            )

    # =========================================================================
    # SECTION 4 — get_charge_by_id
    # =========================================================================
    async def get_charge_by_id(self, charge_id: int) -> Charge:
        """
        Fetch a charge by primary key.

        Args:
            charge_id: The integer primary key of the target charge.

        Returns:
            The matching Charge instance.

        Raises:
            HTTPException: 404 if no charge with this id exists.
        """
        # Fetch by primary key; translate "not found" into a 404 for the
        # router. Also the fetch step of the fetch-then-act pattern behind
        # update_charge and cancel_charge.
        charge = await self.repo.get_by_id(charge_id)
        if not charge:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Charge with id {charge_id} not found",
            )
        return charge

    # =========================================================================
    # SECTION 5 — get_charges_by_unit_and_period_range
    # =========================================================================
    async def get_charges_by_unit_and_period_range(
        self, unit_id: int, start: date, end: date
    ) -> list[Charge]:
        """
        Return one unit's billing history across a window of months.

        The window is INCLUSIVE at both ends — a charge dated exactly `start`
        or exactly `end` is included. Cancelled charges ARE included: this is
        the history view, and a voided bill is part of the history.

        Args:
            unit_id: The integer primary key of the unit being billed.
            start: First day of the earliest billing period to include.
            end: First day of the latest billing period to include.

        Returns:
            A list of Charge instances ordered oldest period first. Empty list
            if the unit has no charges in that window.

        Raises:
            HTTPException: 400 if start is later than end.
        """
        # Step 1 — validate the range actually makes sense.
        #
        # This check is genuine business logic and belongs HERE, not in the
        # repository. The repository stays general purpose: handed a backwards
        # range it would happily run the query and return an empty list, which
        # is indistinguishable from a legitimate "this unit has no charges in
        # that window." The client would be told "no charges exist" when the
        # truth is "you asked the question wrong." A 400 says which.
        #
        # start == end is intentionally ALLOWED — that is a valid single-month
        # query, which is why this is `>` and not `>=`.
        if start > end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start period cannot be later than end period",
            )

        # Step 2 — delegate.
        return await self.repo.get_by_unit_and_period_range(unit_id, start, end)

    # =========================================================================
    # SECTION 6 — get_charges_by_tenant_and_period
    # =========================================================================
    async def get_charges_by_tenant_and_period(self, tenant_id: int, period: date) -> list[Charge]:
        """
        Answer "what does this tenant owe for this month".

        Typically returns MULTIPLE rows — a tenant normally owes rent AND
        water for the same period, and may additionally owe a one-off
        move_out charge in their final month. There is no unique constraint on
        (tenant_id, period) and there must not be one: each category is its
        own bill.

        Args:
            tenant_id: The integer primary key of the tenant.
            period: The first day of the billing month (e.g. 2026-03-01).

        Returns:
            A list of Charge instances ordered by category. Empty list if
            nothing was billed to that tenant for that month.
        """
        # No business rules to apply — delegate straight to the repository.
        #
        # An empty result is NOT a 404. "This tenant owes nothing this month"
        # is a legitimate, correct answer to a well-formed question, unlike
        # get_charge_by_id where a missing row means the requested thing does
        # not exist.
        return await self.repo.get_by_tenant_and_period(tenant_id, period)

    # =========================================================================
    # SECTION 7 — get_charges_by_owner_and_period
    # =========================================================================
    async def get_charges_by_owner_and_period(self, owner_id: int, period: date) -> list[Charge]:
        """
        Answer "what does this owner owe for this month".

        Also typically multi-row, but for a different reason than the tenant
        equivalent above. A tenant's several rows are several CATEGORIES
        against one unit; an owner's several rows are usually the SAME
        category (service_charge) across SEVERAL UNITS they own.

        Args:
            owner_id: The integer primary key of the owner.
            period: The first day of the billing month (e.g. 2026-03-01).

        Returns:
            A list of Charge instances ordered by category. Empty list if
            nothing was billed to that owner for that month.
        """
        # No business rules to apply — delegate straight to the repository.
        # Empty result is a valid answer, not a 404 — same reasoning as
        # Section 6.
        return await self.repo.get_by_owner_and_period(owner_id, period)

    # =========================================================================
    # SECTION 8 — get_total_by_unit_and_period_range
    # =========================================================================
    async def get_total_by_unit_and_period_range(
        self, unit_id: int, start: date, end: date
    ) -> Decimal:
        """
        Total the LIVE (non-cancelled) charges for one unit across a window.

        The window is INCLUSIVE at both ends, matching
        get_charges_by_unit_and_period_range. Cancelled charges are EXCLUDED
        by the repository's query — a voided bill is not money owed. Note the
        deliberate contrast with Section 5: that method answers "what
        happened", this one answers "what is owed".

        Args:
            unit_id: The integer primary key of the unit being billed.
            start: First day of the earliest billing period to include.
            end: First day of the latest billing period to include.

        Returns:
            The total as an exact Decimal. Decimal("0.00") — never None —
            when nothing matches.

        Raises:
            HTTPException: 400 if start is later than end.
        """
        # Step 1 — same range validation as Section 5, and it matters MORE
        # here. A backwards range returns an empty set, which the repository
        # normalises from SQL's NULL to Decimal("0.00") — so without this
        # check a malformed question would come back as a confident,
        # authoritative "this unit owes 0.00". A wrong balance is a worse
        # failure than a wrong-looking empty list.
        if start > end:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="start period cannot be later than end period",
            )

        # Step 2 — delegate. The SUM is computed by PostgreSQL, not in Python.
        return await self.repo.get_total_by_unit_and_period_range(unit_id, start, end)

    # =========================================================================
    # SECTION 9 — update_charge
    # =========================================================================
    async def update_charge(self, charge: Charge, payload: ChargeUpdate) -> Charge:
        """
        Correct a charge's amount, percentage or period.

        Admin only — enforced at the router level via
        Depends(get_current_active_superuser), not here: changing what someone
        owes is high-stakes in a way that raising a routine monthly bill is
        not.

        ChargeUpdate deliberately carries only those three fields. category,
        unit_id, owner_id and tenant_id are NOT updatable — changing them
        would rewrite financial history rather than correct a typo in it.
        is_cancelled is not there either; voiding goes through cancel_charge.
        Because that restriction lives in the schema, there is no
        uniqueness-style guard to apply here — the contrast with
        UnitService.update_unit, which must check for a duplicate unit_number
        before delegating.

        The router fetches the charge first via get_charge_by_id and passes
        the Charge object here — fetch-then-act pattern, same as
        UnitService.update_unit.

        Args:
            charge: The existing Charge instance to update.
            payload: The validated partial-update data from the client.

        Returns:
            The updated Charge instance.

        Raises:
            HTTPException: 409 if the charge has already been cancelled.
        """
        # Step 1 — refuse to edit a voided charge, before anything else.
        #
        # A cancelled charge is a CLOSED financial record. The entire reason
        # is_cancelled exists instead of a hard delete is to preserve an
        # accurate audit trail.
        #
        # Editing a voided record's amount rewrites history: the row would
        # then claim "we cancelled a 25,000 charge" when what actually
        # happened was "we cancelled a 30,000 charge and issued a 25,000 one
        # instead."
        #
        # The correct action for a correction is creating a NEW charge,
        # leaving the voided one intact — the same principle as a paper
        # receipt book, where a wrong receipt is marked VOID and left in place
        # rather than erased and overwritten.
        if charge.is_cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot update a cancelled charge",
            )

        # Step 2 — delegate to the repository, which uses exclude_unset=True
        # so only fields the client actually sent change.
        return await self.repo.update(charge, payload)

    # =========================================================================
    # SECTION 10 — cancel_charge
    # =========================================================================
    async def cancel_charge(self, charge: Charge, payload: ChargeCancel) -> Charge:
        """
        Void a charge — the ONLY way a charge is ever taken out of
        circulation, and this service's counterpart to the delete_unit /
        delete_owner methods that other services have.

        The row stays in the table permanently; only its is_cancelled flag
        changes, so both facts — that the bill was raised, and that it was
        later voided — remain recoverable. See the class docstring for why
        there is no delete_charge.

        Admin only — enforced at the router level via
        Depends(get_current_active_superuser).

        The router fetches the charge first via get_charge_by_id and passes
        the Charge object here — fetch-then-act pattern, same as
        update_charge.

        Args:
            charge: The existing Charge instance to void.
            payload: The validated ChargeCancel flag from the client. That
                schema carries ONLY is_cancelled and defaults it to True, so
                an empty request body still expresses the intended action.

        Returns:
            The updated Charge instance.

        Raises:
            HTTPException: 409 if the charge has already been cancelled.
        """
        # Step 1 — refuse to re-cancel, before anything else.
        #
        # For a financial record, silently succeeding on a no-op misinforms
        # the staff member — they walk away believing their action voided the
        # charge when in fact it did nothing.
        #
        # A 409 tells them the true state: someone already voided this. That
        # is informative, not obstructive — it may also signal that a
        # colleague is working on the same problem.
        if charge.is_cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Charge is already cancelled",
            )

        # Step 2 — delegate to the repository, which only ever touches the
        # is_cancelled column.
        #
        # No IntegrityError catch here, unlike create_charge: this writes a
        # boolean to an existing row and touches no foreign key, so there is
        # no constraint left for it to violate.
        return await self.repo.cancel(charge, payload)

    # =========================================================================
    # SECTION 11 — get_all_charges
    # =========================================================================
    async def get_all_charges(self) -> list[Charge]:
        """
        Return all charges.

        Cancelled charges ARE included — this is the unfiltered admin view,
        and a voided bill is still a record.

        Returns:
            A list of all Charge instances, newest first by creation date.
        """
        # No business rules to apply — delegate straight to the repository.
        return await self.repo.get_all()
