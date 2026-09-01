"""
Payment service — the business logic layer for all payment operations.

Architecture position:
  routers (HTTP) → services (business logic) → repositories (queries) → models (DB)

Responsibilities of this file:
  - Enforce the business rules the database structurally cannot express: that
    a payment's amount must EXACTLY EQUAL its charge's amount, that a
    cancelled charge cannot be paid, and that a non-admin may only void a
    payment they themselves recorded and only within a fixed window.
  - Set created_by from the authenticated user rather than client input.
  - Translate "not found" and constraint-violation conditions into
    HTTPExceptions with semantically correct status codes for the router.

Out of scope for this file:
  - SQL / SQLAlchemy queries (the repository's job).
  - HTTP request/response handling (the router's job).
  - The amount > 0 and max-length checks (the Pydantic schema layer's job —
    see PaymentBase).
  - Creating its own repositories or database session (injected in).
"""

# =============================================================================
# SECTION 1 — Imports
# =============================================================================
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

# Payment is imported as a type annotation only. The service never
# instantiates Payment() or writes queries — that is the repository's job.
from app.models.payment import Payment

# User is imported as a real VALUE dependency, not merely an annotation:
# cancel_payment reads is_superuser and id off the authenticated user to
# decide whether the void is permitted. No other service in this project
# needs the User object itself — the others delegate the entire permission
# question to router-level guards.
from app.models.user import User
from app.repositories.charge_repository import ChargeRepository
from app.repositories.payment_repository import PaymentRepository

# PaymentCancel is imported because the repository's cancel() takes BOTH the
# Payment object and a PaymentCancel payload —
# `cancel(self, payment: Payment, payload: PaymentCancel)` — so cancel_payment
# below must accept one to pass through.
from app.schemas.payment import PaymentCancel, PaymentCreate

# =============================================================================
# SECTION 2 — Module-level constant
# =============================================================================
# How long a non-admin staff member may still void a payment they recorded
# themselves. Admins are not subject to it at all.
#
# A named module-level constant rather than a bare `24` buried inside
# cancel_payment: this is a POLICY number, not an implementation detail. It is
# the kind of value that gets revisited ("make it 48 hours", "make it until
# end of business day"), and when that happens it should be changed in one
# obvious place and be greppable from outside this file — a test asserting
# the boundary should import this constant rather than hardcode its own 24
# and silently drift from the real rule.
#
# The window exists to make correcting a fresh mistake routine — a staff
# member who fat-fingers a reference should not need an admin to fix it
# minutes later — while keeping older records under admin control, since a
# void that arrives days after the fact is a reconciliation decision rather
# than a typo correction.
SELF_VOID_WINDOW_HOURS = 24


# =============================================================================
# SECTION 3 — PaymentService class
# =============================================================================
class PaymentService:
    """
    Business logic layer for all payment operations.

    Sits between the router (HTTP) and the repositories (database). Enforces
    all rules that determine whether an operation should be allowed and in
    what form:
      - Never writes SQL — always delegates to a repository.
      - Never handles HTTP request/response objects directly.
      - Raises HTTPException for the router to handle.
      - Receives its repositories via the constructor — never creates its own
        (dependency injection pattern).

    TWO repositories — the deliberate exception in this project:
      Every other service here takes exactly ONE repository. This one takes
      PaymentRepository AND ChargeRepository, and that is justified rather
      than convenient.

      Note the deliberate CONTRAST with ChargeService, which refused to
      inject Unit/Owner/Tenant repositories. There, pre-checking would have
      meant THREE foreign repositories injected purely to produce a nicer
      error message for rules PostgreSQL already enforced for free via
      foreign keys — real cross-feature coupling and three extra round-trips
      per create, buying nothing but wording, and still needing an
      IntegrityError backstop anyway.

      Here the situation is genuinely different in kind. Two of this
      service's rules — that a payment's amount must EXACTLY EQUAL the
      charge's amount, and that a cancelled charge cannot be paid — are
      business rules the database CANNOT express at all. No constraint, check
      or index can compare a value in `payments` against a value in `charges`
      on insert. Without reading the Charge row there is no way to enforce
      them anywhere. The dependency is therefore necessary, not merely an
      improvement to an error message.

      And it costs ONE extra query, not two: the charge is fetched ONCE at
      the top of create_payment and BOTH checks read from that same object.

    NO delete_payment method — by design, not by omission:
      This mirrors PaymentRepository, which has no delete() either, and no
      delete method should ever be added at either layer. Financial records
      are never destroyed, only VOIDED via cancel_payment(), which flips
      is_cancelled to True and leaves the row in place. Money recorded in
      error stays visible as a mistaken record rather than vanishing as if it
      had never been entered.

    NO update_payment method — also by design:
      Unlike ChargeService, which has update_charge for correcting an amount
      or period, there is nothing here for a general PATCH to do. The amount
      is pinned to the charge's amount by the rule above, so it cannot be
      edited to anything else and still be valid; method and reference are
      incidental detail. Void the mistaken payment and record a corrected
      one — which is precisely what the partial unique index's exclusion of
      cancelled rows makes possible. There is no PaymentUpdate schema for the
      same reason.

    Where the 409s in this class come from:
      As in ChargeService, a 409 here never means "other records reference
      this row, so it cannot be removed" (the UnitService/OwnerService sense
      of the code) — nothing is removable here. It means a state conflict:
      the charge is already cancelled, the charge already has an active
      payment, or this payment is already cancelled.
    """

    def __init__(
        self,
        repository: PaymentRepository,
        charge_repository: ChargeRepository,
    ) -> None:
        """
        Store the injected repositories.

        Args:
            repository: The PaymentRepository this service delegates all
                payment database access to.
            charge_repository: The ChargeRepository, needed to read the Charge
                row that create_payment validates against. See the class
                docstring for why this second repository is justified here
                and was deliberately avoided in ChargeService.
        """
        # Both injected by dependencies.py — never instantiated directly
        # inside this class. They share the same request-scoped AsyncSession,
        # so the two repositories are two query surfaces over one transaction,
        # not two independent connections.
        self.repo = repository
        self.charge_repo = charge_repository

    # =========================================================================
    # SECTION 4 — create_payment
    # =========================================================================
    async def create_payment(self, payload: PaymentCreate, created_by: int) -> Payment:
        """
        Record money actually received against one charge.

        Open to any logged-in staff member — enforced at the router level via
        Depends(get_current_user), not here: recording money that has arrived
        is routine front-desk work.

        This is the method that carries almost all of this service's business
        logic, because a payment is only meaningful in relation to its charge.
        Four conditions are checked, in a deliberate order (see the inline
        comments): the charge exists, the charge is live, the amount matches
        exactly, and no active payment already occupies the charge's slot.

        Args:
            payload: The validated payment-creation data from the client.
            created_by: The id of the authenticated staff member recording
                this payment, passed down by the router. A required parameter
                here — every payment created through the API is attributed to
                the user who made the request, and it is never read from the
                client payload (PaymentCreate has no such field). This field
                carries more weight than the equivalent on Charge: it is read
                back by cancel_payment to decide who may void the row.

        Returns:
            The newly created Payment instance.

        Raises:
            HTTPException: 400 if charge_id matches no existing charge, or if
                the amount does not exactly equal the charge's amount; 409 if
                the charge is cancelled, or already has an active payment.
        """
        # ---------------------------------------------------------------------
        # Step 1 — fetch the charge ONCE.
        #
        # Steps 2 and 3 both read from this single object, which is what keeps
        # the two-repository design to one extra query rather than two. Do not
        # be tempted to re-fetch per check.
        #
        # A missing charge is a 400, not a 404. The 404 in this project means
        # "the resource you addressed in the URL does not exist"; here the
        # client addressed POST /payments, which exists fine — they sent a bad
        # VALUE in the body. That is the same call ChargeService.create_charge
        # makes when a foreign key does not resolve, and keeps the two create
        # paths consistent.
        # ---------------------------------------------------------------------
        charge = await self.charge_repo.get_by_id(payload.charge_id)
        if not charge:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Charge with id {payload.charge_id} does not exist",
            )

        # ---------------------------------------------------------------------
        # Step 2 — refuse to pay a voided charge.
        #
        # A cancelled charge is a CLOSED financial record: the bill was
        # withdrawn, so no money is owed against it. Recording a payment
        # against one would assert that a withdrawn bill was settled, which
        # is not something that can be true — and it would quietly make the
        # books disagree with themselves, since the charge is excluded from
        # every outstanding-balance total while the payment is not.
        #
        # In practice this almost always means the staff member pasted the
        # wrong charge id, and the money they are holding belongs against the
        # REPLACEMENT charge that was raised when this one was voided. A 409
        # naming the situation sends them looking for it; a silent success
        # would strand the payment on a dead record.
        #
        # 409 rather than 400 because nothing is wrong with the request's
        # SHAPE — the charge id is real and well-formed. The conflict is with
        # the current STATE of that row, which is exactly what 409 encodes,
        # matching ChargeService's use of it for the already-cancelled case.
        # ---------------------------------------------------------------------
        if charge.is_cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(f"Charge with id {payload.charge_id} is cancelled and cannot be paid"),
            )

        # ---------------------------------------------------------------------
        # Step 3 — the amount must EXACTLY equal the charge's amount.
        #
        # This is THE rule that makes this service take a second repository.
        # Partial payments are not permitted in this domain: a charge is
        # settled in full or not at all, which is what lets payment status be
        # derived from the mere EXISTENCE of an active Payment row rather than
        # from summing partial amounts against a target. The schema layer can
        # only check amount > 0 — it has no access to the Charge — and the
        # database cannot compare across tables on insert. Here is the only
        # place the rule can live.
        #
        # Both operands are Decimal, never float, all the way from the request
        # body (PaymentBase.amount) through Numeric(12, 2) in both tables.
        # That is what makes `!=` trustworthy here: comparing binary floats
        # for exact equality is a well-known trap, but Decimal comparison is
        # exact by construction. Note also that Decimal equality compares
        # NUMERIC VALUE, not representation — Decimal("30000.0") ==
        # Decimal("30000.00") is True — so a client sending one fewer
        # trailing zero is correctly accepted rather than rejected on a
        # formatting technicality.
        #
        # Both figures go in the message. "Amount does not match" alone would
        # force the staff member to go and look up the charge; showing what
        # was sent alongside what was expected usually makes the mistake
        # self-evident (a transposed digit, or the wrong charge entirely).
        # ---------------------------------------------------------------------
        if payload.amount != charge.amount:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Payment amount {payload.amount} does not match the charge "
                    f"amount {charge.amount}. Partial payments are not permitted."
                ),
            )

        # ---------------------------------------------------------------------
        # Step 4 — pre-check the one-active-payment-per-charge rule.
        #
        # The partial unique index on the model is the REAL guarantee. This
        # check exists purely so the client gets a specific, actionable error
        # naming the payment that is blocking, instead of a vague one derived
        # from a bare IntegrityError that cannot tell which constraint failed.
        #
        # Reading active.id here is safe: no rollback has occurred at this
        # point, so the object is not expired. Contrast the except block
        # below, where that is no longer true.
        # ---------------------------------------------------------------------
        active = await self.repo.get_active_by_charge_id(payload.charge_id)
        if active:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Charge with id {payload.charge_id} already has an active "
                    f"payment (id {active.id}). Cancel it before recording "
                    "another."
                ),
            )

        # ---------------------------------------------------------------------
        # Step 5 — capture-before-the-risky-call, then insert.
        #
        # charge.id is captured into a plain int BEFORE the insert is
        # attempted. The repository calls rollback() when the commit fails,
        # and rollback EXPIRES every object attached to the session — `charge`
        # included, since it was loaded through charge_repo on this same
        # session. Reading charge.id inside the except block would then
        # trigger SQLAlchemy's synchronous lazy-load path and raise
        # MissingGreenlet, masking the real error with an unrelated crash.
        # Same bug, same fix, as OwnerService.delete_owner and
        # UnitService.delete_unit.
        #
        # Note that ChargeService.create_charge needed NO such capture: it
        # reads only `payload` fields in its except block, and a Pydantic
        # object is never attached to the session, so nothing can expire it.
        # The difference here is precisely that this service holds an ORM
        # object across the risky call — the cost of the second repository.
        # ---------------------------------------------------------------------
        charge_id = charge.id

        try:
            return await self.repo.create(
                charge_id=payload.charge_id,
                amount=payload.amount,
                paid_at=payload.paid_at,
                method=payload.method,
                reference=payload.reference,
                created_by=created_by,
            )
        except IntegrityError:
            # Reaching here means the database rejected what all four checks
            # above had just approved — so this is a genuine RACE, not a
            # missed validation.
            #
            # The overwhelmingly likely cause is the partial unique index:
            # two staff members recording the same payment at the same moment
            # both passed Step 4, and PostgreSQL let exactly one win. That is
            # the index doing precisely the job it exists for, and the reason
            # Step 4 can never replace it.
            #
            # (The other possibility is a foreign key: the charge was voided
            # and hard-deleted, or the acting user's account was removed,
            # between Step 1 and this commit. Neither is reachable through the
            # API as built — charges are never deleted — so the message leads
            # with the race.)
            #
            # 409, not 400: the request was valid when it was made, and
            # retrying after checking the charge is the sensible next action.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Could not record payment for charge {charge_id} — an "
                    "active payment was recorded for it concurrently. "
                    "Re-check the charge before retrying."
                ),
            )

    # =========================================================================
    # SECTION 5 — get_payment_by_id
    # =========================================================================
    async def get_payment_by_id(self, payment_id: int) -> Payment:
        """
        Fetch a payment by primary key.

        Args:
            payment_id: The integer primary key of the target payment.

        Returns:
            The matching Payment instance.

        Raises:
            HTTPException: 404 if no payment with this id exists.
        """
        # Fetch by primary key; translate "not found" into a 404 for the
        # router. Also the fetch step of the fetch-then-act pattern behind
        # cancel_payment — which additionally needs created_by and created_at
        # off the returned row to decide whether the caller may void it.
        payment = await self.repo.get_by_id(payment_id)
        if not payment:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Payment with id {payment_id} not found",
            )
        return payment

    # =========================================================================
    # SECTION 6 — get_active_payment_by_charge_id
    # =========================================================================
    async def get_active_payment_by_charge_id(self, charge_id: int) -> Payment | None:
        """
        Return the single ACTIVE payment for a charge, or None.

        This is the query that answers "is this charge paid?" — the derived
        payment status that Charge deliberately does not store as a column.

        Args:
            charge_id: The integer primary key of the charge.

        Returns:
            The active Payment instance, or None if the charge has no live
            payment — either none was ever recorded, or every payment recorded
            against it has since been voided.
        """
        # Returns Optional rather than raising 404 on a miss, and the
        # distinction is the whole point of this method. "This charge is
        # unpaid" is a legitimate, correct answer to a well-formed question —
        # it is in fact the answer for every charge the moment it is raised.
        # A 404 would misreport the normal state of an unpaid bill as a
        # missing resource. Same reasoning as ChargeService's empty-list
        # returns, one step further: here even the singular result is
        # legitimately absent.
        #
        # The router is responsible for turning None into whatever the
        # endpoint's contract says (a 404 on GET /payments/charge/{id}/active,
        # or a null field on an enriched charge response) — that is a
        # presentation decision, and it differs per endpoint.
        return await self.repo.get_active_by_charge_id(charge_id)

    # =========================================================================
    # SECTION 7 — get_payments_by_charge_id
    # =========================================================================
    async def get_payments_by_charge_id(self, charge_id: int) -> list[Payment]:
        """
        Return the FULL payment history for one charge, cancelled rows
        included.

        Deliberately distinct from get_active_payment_by_charge_id above:
        that method answers "what is the live payment", this one answers
        "what has happened to this bill" — which may be several voided
        attempts plus at most one active payment.

        Args:
            charge_id: The integer primary key of the charge.

        Returns:
            A list of Payment instances ordered newest-recorded first. Empty
            list if nothing has ever been recorded against the charge.
        """
        # No business rules to apply — delegate straight to the repository.
        # An empty result is NOT a 404: a charge with no payments yet is the
        # normal state of every newly raised bill.
        return await self.repo.get_by_charge_id(charge_id)

    # =========================================================================
    # SECTION 8 — cancel_payment
    # =========================================================================
    async def cancel_payment(
        self,
        payment: Payment,
        payload: PaymentCancel,
        current_user: User,
    ) -> Payment:
        """
        Void a payment — the ONLY way a payment is ever taken out of
        circulation, and this service's counterpart to the delete_unit /
        delete_owner methods that other services have.

        The row stays in the table permanently; only its is_cancelled flag
        changes, so both facts — that the payment was recorded, and that it
        was later voided — remain recoverable. Voiding also FREES the charge's
        single active-payment slot, since the partial unique index counts only
        non-cancelled rows, which is what makes recording a corrected payment
        possible at all.

        PERMISSION IS ENFORCED HERE, not at the router — the one place in this
        project where that is true, and the reason this method takes
        current_user at all. Every other admin-only action (cancel_charge,
        delete_unit, delete_owner) is guarded by a blanket
        Depends(get_current_active_superuser) at the route, because the answer
        depends only on WHO is asking. This rule also depends on the ROW: a
        non-admin may void a payment they recorded themselves, within
        SELF_VOID_WINDOW_HOURS of recording it. A router-level guard cannot
        express that, because it has not loaded the payment yet.

        The router fetches the payment first via get_payment_by_id and passes
        the Payment object here — fetch-then-act pattern, same as
        ChargeService.cancel_charge.

        Args:
            payment: The existing Payment instance to void.
            payload: The validated PaymentCancel flag from the client. That
                schema carries ONLY is_cancelled and defaults it to True, so
                an empty request body still expresses the intended action.
            current_user: The authenticated staff member requesting the void,
                injected by the router from Depends(get_current_user).

        Returns:
            The updated Payment instance.

        Raises:
            HTTPException: 403 if a non-admin tries to void a payment they did
                not record, or one recorded longer ago than the self-void
                window allows; 409 if the payment has already been cancelled.
        """
        # ---------------------------------------------------------------------
        # Step 1 — permission, BEFORE the state check.
        #
        # This ordering is deliberate and differs from ChargeService.
        # cancel_charge, which checks state first — but there is no conflict
        # with that precedent, because ChargeService has no service-level
        # permission check at all to order against.
        #
        # Permission comes first here so that a staff member with no right to
        # touch this row learns nothing about it. If the state check ran
        # first, a 409 would confirm "this payment exists and is already
        # cancelled" to someone who is not allowed to act on it either way.
        # Deciding on identity first, and only then on state, keeps the 403
        # free of information the caller has not earned.
        # ---------------------------------------------------------------------
        if not current_user.is_superuser:
            # An admin may void ANY payment, with no time limit — everything
            # below applies only to non-admin staff.
            #
            # created_by is Optional on the model (ondelete="SET NULL"), so a
            # NULL here means the recording user's account was deleted. The
            # comparison then fails for every non-admin, which is the correct
            # outcome: an orphaned financial record should require admin
            # judgement, not be self-servable by whoever happens to ask.
            if payment.created_by != current_user.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You can only cancel payments that you recorded",
                )

            # created_at is TIMESTAMPTZ (DateTime(timezone=True)), so asyncpg
            # returns it timezone-AWARE and this subtraction is safe. Both
            # sides must be aware — mixing an aware and a naive datetime
            # raises TypeError — which is exactly why the model stores UTC
            # with an offset rather than a naive local timestamp, and why
            # datetime.now(timezone.utc) is used here rather than
            # datetime.now().
            age = datetime.now(timezone.utc) - payment.created_at
            if age > timedelta(hours=SELF_VOID_WINDOW_HOURS):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail=(
                        f"Payments can only be cancelled by the staff member who "
                        f"recorded them within {SELF_VOID_WINDOW_HOURS} hours. "
                        "Ask an administrator to cancel this payment."
                    ),
                )

        # ---------------------------------------------------------------------
        # Step 2 — refuse to re-cancel.
        #
        # Same reasoning as ChargeService.cancel_charge: for a financial
        # record, silently succeeding on a no-op misinforms the staff member,
        # who walks away believing their action voided the payment when in
        # fact it did nothing. A 409 tells them the true state — someone
        # already voided this — which may also signal that a colleague is
        # working on the same correction.
        #
        # It matters more here than it does for a charge. A voided payment has
        # already released the charge's active-payment slot, so a second void
        # reported as success could easily be read as "the slot is free now"
        # when the real story is that someone else freed it and may already
        # have recorded the replacement.
        # ---------------------------------------------------------------------
        if payment.is_cancelled:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Payment is already cancelled",
            )

        # ---------------------------------------------------------------------
        # Step 3 — delegate to the repository, which only ever touches the
        # is_cancelled column.
        #
        # No IntegrityError catch here, unlike create_payment: this writes a
        # boolean to an existing row and touches no foreign key. The partial
        # unique index cannot be violated by this write either — cancelling
        # REMOVES a row from the index's scope rather than adding one.
        # ---------------------------------------------------------------------
        return await self.repo.cancel(payment, payload)

    # =========================================================================
    # SECTION 9 — get_all_payments
    # =========================================================================
    async def get_all_payments(self) -> list[Payment]:
        """
        Return all payments.

        Cancelled payments ARE included — this is the unfiltered admin view,
        and a voided payment is still a record.

        Returns:
            A list of all Payment instances, newest first by payment date.
        """
        # No business rules to apply — delegate straight to the repository.
        return await self.repo.get_all()
