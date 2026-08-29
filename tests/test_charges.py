"""
End-to-end tests for the Charge endpoints (/api/v1/charges/*).

The permission split here is a THIRD shape, distinct from both Owner/Unit and
Tenant, and these tests assert it directly rather than assuming an earlier
pattern:

  create, read, list, all range queries  → ANY authenticated staff member
  update, cancel                         → admin only
  delete                                 → does not exist, at any layer

Issuing a bill is routine, high-volume operational work that happens on a
schedule at the start of every month, so creation is deliberately open to
ordinary staff. Changing what someone already owes, or voiding a bill
outright, is high-stakes in a way raising one is not — both PATCH routes keep
the superuser guard.

There is no delete endpoint anywhere, by design: financial records are voided
via PATCH /charges/{id}/cancel, never destroyed, so the audit trail always
survives. test_charges_has_no_delete_endpoint asserts that absence directly
rather than leaving it implied — an accidentally-added DELETE route would
otherwise pass unnoticed.

Also covers the category/party pairing validator (which party each category
requires and forbids), the period-first-of-month rule, the IntegrityError →
400 path on a bad unit_id, the two 409 guards protecting closed records, and
the route-ordering trap that /charges/{charge_id} would otherwise create.

Setup note: a Charge cannot exist on its own. It needs a real unit_id, and
either a real tenant_id or a real owner_id — and a Unit in turn needs a real
owner_id. Most tests therefore build a chain: owner → unit → tenant → charge.
The helpers below exist to make that chain a single readable line per test.
"""

from httpx import AsyncClient

# Phone is sent in local Kenyan form and comes back normalised to E164 by
# DamalPhoneNumber — same convention as test_owners.py and test_units.py.
OWNER_PAYLOAD = {
    "name": "Amina Hassan",
    "phone": "0707234780",
    "email": "amina@example.com",
    "national_id": "12345678",
}

TENANT_PAYLOAD = {
    "name": "Yusuf Omar",
    "phone": "0711223344",
    "email": "yusuf@example.com",
    "national_id": "87654321",
}


# =============================================================================
# Helpers
# =============================================================================
# Plain async functions rather than fixtures, following test_units.py. The
# setup a charge needs varies per test — some need a tenant, some an owner,
# some three charges in three different months, one needs a unit with no
# charges at all — so the choice stays at the call site. A fixture would force
# the same chain on every test whether or not it wanted it.
async def create_owner(client: AsyncClient, admin_headers: dict) -> int:
    """Create a real Owner through the API as admin and return its id.

    Owner creation is admin-only, so this always uses admin_headers even in
    tests whose subject is a staff member — the staff caller is exercised on
    the charge endpoint itself, not on this setup step.
    """
    response = await client.post("/api/v1/owners", json=OWNER_PAYLOAD, headers=admin_headers)
    assert response.status_code == 201
    return response.json()["id"]


async def create_tenant(client: AsyncClient, staff_headers: dict) -> int:
    """Create a real Tenant through the API as staff and return its id.

    Uses staff_headers because tenant creation is genuinely open to ordinary
    staff on that router — no admin is needed for this step.
    """
    response = await client.post("/api/v1/tenants", json=TENANT_PAYLOAD, headers=staff_headers)
    assert response.status_code == 201
    return response.json()["id"]


async def create_unit(
    client: AsyncClient, admin_headers: dict, owner_id: int, unit_number: str
) -> int:
    """Create a real Unit for the given owner as admin and return its id.

    unit_type is "shop", which requires bedrooms to be absent entirely, so the
    body carries no bedrooms key. unit_number is varied per test because it is
    unique building-wide.
    """
    response = await client.post(
        "/api/v1/units",
        json={
            "unit_number": unit_number,
            "floor": 0,
            "unit_type": "shop",
            "owner_id": owner_id,
        },
        headers=admin_headers,
    )
    assert response.status_code == 201
    return response.json()["id"]


async def create_rent_charge(
    client: AsyncClient,
    staff_headers: dict,
    unit_id: int,
    tenant_id: int,
    period: str = "2026-08-01",
    amount: str = "30000.00",
) -> dict:
    """Create a rent charge as staff and return the full response body.

    period and amount are parameterised so range and total tests can vary
    them. amount is sent as a STRING, never a JSON float — see the note in
    test_create_rent_charge_success.
    """
    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "rent",
            "amount": amount,
            "period": period,
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )
    assert response.status_code == 201
    return response.json()


async def create_service_charge(
    client: AsyncClient,
    staff_headers: dict,
    unit_id: int,
    owner_id: int,
    period: str = "2026-08-01",
    amount: str = "3000.00",
    percentage: str = "10.00",
) -> dict:
    """Create a service charge as staff and return the full response body.

    The owner-side counterpart to create_rent_charge: service_charge is the
    one category owed by the OWNER, and the only one carrying a percentage.
    """
    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "service_charge",
            "amount": amount,
            "period": period,
            "owner_id": owner_id,
            "percentage": percentage,
        },
        headers=staff_headers,
    )
    assert response.status_code == 201
    return response.json()


# =============================================================================
# Create — valid cases
# =============================================================================
async def test_create_rent_charge_success(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A staff member creating a valid rent charge gets 201 and an audit trail.

    Note the fixture on the charge itself: staff_headers, NOT admin_headers.
    Creation being open to ordinary staff is the defining permission choice on
    this router, and it is asserted here on the happy path.

    amount is sent as the STRING "30000.00" rather than the JSON number
    30000.00. JSON numbers are IEEE-754 binary floats, which cannot represent
    decimal fractions exactly; sending and asserting the string form keeps the
    value exact end to end, matching the Decimal/Numeric(12, 2) pairing the
    schema and model deliberately use.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-RENT-OK")
    tenant_id = await create_tenant(client, staff_headers)

    # The staff member's own id — created_by must match this, proving the
    # router takes it from the authenticated caller and never from the
    # payload (ChargeCreate has no created_by field for a client to send).
    me = await client.get("/api/v1/auth/me", headers=staff_headers)
    assert me.status_code == 200
    staff_id = me.json()["id"]

    body = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    assert body["category"] == "rent"
    assert body["unit_id"] == unit_id
    assert body["amount"] == "30000.00"
    assert body["period"] == "2026-08-01"
    # Rent is owed by the TENANT — the tenant side is populated and the owner
    # side must be null, which is the pairing rule made visible on the way out.
    assert body["tenant_id"] == tenant_id
    assert body["owner_id"] is None
    # percentage documents how a service_charge was derived; rent is a flat
    # amount, so a percentage here would record a calculation that never
    # happened.
    assert body["percentage"] is None
    # A new charge is never created already-voided.
    assert body["is_cancelled"] is False
    assert body["created_by"] == staff_id
    assert "id" in body


async def test_create_service_charge_success(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A valid service charge gets 201 with the owner side populated instead.

    The mirror image of the rent test above: same endpoint, same staff
    permission, but the opposite party — and the one category that carries a
    percentage, returned exactly as "10.00" rather than a lossy 10.0 float.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-SVC-OK")

    body = await create_service_charge(client, staff_headers, unit_id, owner_id)

    assert body["category"] == "service_charge"
    assert body["amount"] == "3000.00"
    # Service charge is owed by the OWNER — exactly the inverse of rent.
    assert body["owner_id"] == owner_id
    assert body["tenant_id"] is None
    assert body["percentage"] == "10.00"
    assert body["is_cancelled"] is False


async def test_create_water_charge_success(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A water charge gets 201 and is tenant-owed, like rent.

    water is the second of the three tenant-owed categories. Its amount is
    variable (read off a meter) rather than fixed, but the party rules are
    identical to rent's — which is what this asserts.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-WATER-OK")
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "water",
            "amount": "1250.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["category"] == "water"
    assert body["amount"] == "1250.00"
    assert body["tenant_id"] == tenant_id
    assert body["owner_id"] is None


# =============================================================================
# Create — the category/party pairing validator
# =============================================================================
# All five of these return 422, not 400: the rule lives in
# ChargeBase.validate_party_for_category at the Pydantic layer, so the request
# is rejected at the HTTP boundary and never reaches the service or the
# database at all.
async def test_create_rent_charge_with_owner_id_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """Rent carrying an owner_id is rejected with 422 — wrong party entirely.

    Rent is owed by the tenant. An owner_id on a rent row would bill the
    landlord for their own tenant's rent, so the validator forbids it rather
    than quietly ignoring the field.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-RENT-OWNER")
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "rent",
            "amount": "30000.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
            "owner_id": owner_id,
        },
        headers=staff_headers,
    )

    assert response.status_code == 422


async def test_create_rent_charge_without_tenant_id_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """Rent with no tenant_id at all is rejected with 422 — nobody owes it.

    The other half of the rule from the test above: the tenant side is not
    merely allowed on a rent row, it is REQUIRED. Both columns are nullable at
    the database level (a single column cannot conditionally reference two
    different tables with real referential integrity), so this schema
    validator is the only thing enforcing it.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-RENT-NOPARTY")

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "rent",
            "amount": "30000.00",
            "period": "2026-08-01",
        },
        headers=staff_headers,
    )

    assert response.status_code == 422


async def test_create_service_charge_without_percentage_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A service charge with no percentage is rejected with 422.

    percentage records HOW the amount was derived (e.g. 10% of the agreed
    rent). Without it the row states a figure with no auditable basis, so it
    is required on this category specifically.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-SVC-NOPCT")

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "service_charge",
            "amount": "3000.00",
            "period": "2026-08-01",
            "owner_id": owner_id,
        },
        headers=staff_headers,
    )

    assert response.status_code == 422


async def test_create_service_charge_with_tenant_id_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A service charge carrying a tenant_id is rejected with 422.

    The exact inverse of test_create_rent_charge_with_owner_id_rejected:
    service_charge is owed by the owner, so the tenant side is forbidden. The
    validator refuses BOTH directions of the pairing, not just one.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-SVC-TENANT")
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "service_charge",
            "amount": "3000.00",
            "period": "2026-08-01",
            "owner_id": owner_id,
            "percentage": "10.00",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )

    assert response.status_code == 422


async def test_create_water_charge_with_percentage_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A water charge carrying a percentage is rejected with 422.

    percentage belongs to service_charge alone. A water bill comes from a
    meter reading, not a rate, so a percentage on that row would document a
    calculation that never happened — the validator forbids it on every
    tenant-owed category.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-WATER-PCT")
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "water",
            "amount": "1250.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
            "percentage": "10.00",
        },
        headers=staff_headers,
    )

    assert response.status_code == 422


# =============================================================================
# Create — other validation
# =============================================================================
async def test_create_charge_mid_month_period_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A period that is not the first of the month is rejected with 422.

    period identifies WHICH MONTH a bill covers, not a day within it. A charge
    dated 2026-08-29 and one dated 2026-08-01 both mean "August 2026", but
    every range query and grouping would treat them as different periods —
    silently fragmenting a tenant's month across rows nothing joins back
    together.

    The rule REJECTS rather than normalising to day 1: an API that quietly
    rewrites what a client submitted hides the mistake instead of surfacing
    it, and a caller who meant September but typed 2026-08-29 would get a 201
    and never learn they billed the wrong month.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-MIDMONTH")
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "rent",
            "amount": "30000.00",
            "period": "2026-08-29",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )

    assert response.status_code == 422


async def test_create_charge_zero_amount_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A zero amount is rejected with 422 — gt=0 rejects zero and negatives.

    A bill for nothing is not a bill. Recording one would inflate the row
    count of what a tenant "owes" without changing the total, which is
    misleading rather than harmless.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-ZERO")
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "rent",
            "amount": "0.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )

    assert response.status_code == 422


async def test_create_charge_invalid_unit_id_rejected(client: AsyncClient, staff_headers: dict):
    """A unit_id referencing no existing unit returns 400, not 500.

    Proves the IntegrityError → 400 translation in ChargeService.create_charge:
    unit_id is a real foreign key, so the insert fails at commit time inside
    the repository, which rolls back and re-raises; the service turns that
    into a clean 400 rather than letting a raw database error surface as an
    unhandled 500.

    Note this is 400 and not 422 — the payload is structurally valid and
    passes every schema rule, so it reaches the service. Only the database can
    know that unit 999999 does not exist.
    """
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": 999999,
            "category": "rent",
            "amount": "30000.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )

    assert response.status_code == 400


async def test_create_charge_unauthenticated(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """Open to any staff still means authenticated — no header at all is 401.

    The body sent here is entirely valid, so a 401 proves the auth guard runs
    and rejects before any of it matters.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-401")
    tenant_id = await create_tenant(client, staff_headers)

    response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "rent",
            "amount": "30000.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
    )

    assert response.status_code == 401


# =============================================================================
# List and read
# =============================================================================
async def test_list_charges_any_staff(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """Any logged-in staff member can list charges, and a created one appears."""
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-LIST")
    tenant_id = await create_tenant(client, staff_headers)
    created = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    response = await client.get("/api/v1/charges", headers=staff_headers)

    assert response.status_code == 200
    charges = response.json()
    assert any(charge["id"] == created["id"] for charge in charges)


async def test_get_charge_by_id_not_found(client: AsyncClient, staff_headers: dict):
    """Fetching an id that does not exist returns 404, not an empty body."""
    response = await client.get("/api/v1/charges/999999", headers=staff_headers)

    assert response.status_code == 404


async def test_get_charge_by_id_success(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """Fetching a real charge by id returns 200 and the same row that was created."""
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-GET-OK")
    tenant_id = await create_tenant(client, staff_headers)
    created = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    response = await client.get(f"/api/v1/charges/{created['id']}", headers=staff_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == created["id"]
    assert body["amount"] == "30000.00"
    assert body["tenant_id"] == tenant_id


# =============================================================================
# Route ordering
# =============================================================================
async def test_get_charges_by_unit_route_not_swallowed(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """GET /charges/unit/{id} resolves to the unit route, not /charges/{charge_id}.

    This test exists to guard ONE specific regression, and it is worth stating
    plainly because the failure mode is silent and easy to reintroduce.

    FastAPI matches routes in DECLARATION ORDER, not by specificity. The
    charges router declares "/charges/{charge_id}" AFTER the "/unit",
    "/tenant" and "/owner" routes precisely so this request reaches the right
    handler. If someone reorders those handlers — moving the by-id route up,
    or adding a new literal-prefix route below it — then "/charges/unit/12"
    would match "/charges/{charge_id}" instead, FastAPI would try to parse the
    literal string "unit" as an integer charge_id, and the caller would get a
    confusing 422 validation error complaining about a path parameter they
    never knowingly sent.

    Asserting 200 (and specifically NOT 422) catches that immediately. Four
    endpoints depend on the ordering; nothing else in the test suite would
    notice it breaking.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-ORDERING")
    tenant_id = await create_tenant(client, staff_headers)
    await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    response = await client.get(
        f"/api/v1/charges/unit/{unit_id}",
        params={"start": "2026-08-01", "end": "2026-08-01"},
        headers=staff_headers,
    )

    # 422 here means "unit" was parsed as a charge_id — the exact regression
    # this test guards against.
    assert response.status_code == 200
    assert isinstance(response.json(), list)


# =============================================================================
# Range and period queries
# =============================================================================
async def test_get_charges_by_unit_and_period_range(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A period range returns exactly the charges inside it, both ends inclusive.

    Three charges are created in three consecutive months and the query asks
    for the first two. Asserting the August one is ABSENT is the substantive
    half: a range query that returned everything would still pass a
    "the ones I want are present" check.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-RANGE")
    tenant_id = await create_tenant(client, staff_headers)

    june = await create_rent_charge(client, staff_headers, unit_id, tenant_id, period="2026-06-01")
    july = await create_rent_charge(client, staff_headers, unit_id, tenant_id, period="2026-07-01")
    august = await create_rent_charge(
        client, staff_headers, unit_id, tenant_id, period="2026-08-01"
    )

    response = await client.get(
        f"/api/v1/charges/unit/{unit_id}",
        params={"start": "2026-06-01", "end": "2026-07-01"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    returned_ids = [charge["id"] for charge in response.json()]
    # Both ends inclusive: June is the start boundary, July the end boundary,
    # and both must be present rather than treated as exclusive.
    assert june["id"] in returned_ids
    assert july["id"] in returned_ids
    assert august["id"] not in returned_ids
    assert len(returned_ids) == 2


async def test_get_charges_by_unit_backwards_range_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A start later than end returns 400 rather than an empty list.

    The repository would happily run a backwards range and return nothing,
    which is indistinguishable from a legitimate "this unit has no charges in
    that window". The service's guard exists so the caller is told they asked
    the question wrong instead of being told a false answer.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-RANGE-BACK")

    response = await client.get(
        f"/api/v1/charges/unit/{unit_id}",
        params={"start": "2026-08-01", "end": "2026-06-01"},
        headers=staff_headers,
    )

    assert response.status_code == 400


async def test_get_charges_by_tenant_and_period(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A tenant's month returns ALL its categories, not just one row.

    A tenant normally owes rent AND water for the same period — each category
    is its own bill, and there is deliberately no unique constraint on
    (tenant_id, period) that would prevent it. Creating both and asserting
    both come back is what proves this endpoint answers "everything this
    tenant owes this month" rather than "a charge this tenant owes".
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-TENANT-PERIOD")
    tenant_id = await create_tenant(client, staff_headers)

    rent = await create_rent_charge(client, staff_headers, unit_id, tenant_id)
    water_response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "water",
            "amount": "1250.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )
    assert water_response.status_code == 201
    water = water_response.json()

    response = await client.get(
        f"/api/v1/charges/tenant/{tenant_id}",
        params={"period": "2026-08-01"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    returned_ids = [charge["id"] for charge in response.json()]
    assert rent["id"] in returned_ids
    assert water["id"] in returned_ids
    assert len(returned_ids) == 2


async def test_get_charges_by_owner_and_period(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """An owner's month returns their service charges.

    The owner-side counterpart to the tenant endpoint. Where a tenant's
    several rows are several CATEGORIES against one unit, an owner's are
    usually the same category across several units they own — but the shape of
    the request and response is identical.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-OWNER-PERIOD")
    created = await create_service_charge(client, staff_headers, unit_id, owner_id)

    response = await client.get(
        f"/api/v1/charges/owner/{owner_id}",
        params={"period": "2026-08-01"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    charges = response.json()
    assert any(charge["id"] == created["id"] for charge in charges)
    assert all(charge["owner_id"] == owner_id for charge in charges)


# =============================================================================
# The aggregate total
# =============================================================================
async def test_get_unit_total(client: AsyncClient, staff_headers: dict, admin_headers: dict):
    """The total sums a unit's charges and echoes the query inputs back.

    30000.00 + 1250.00 = 31250.00, asserted as a STRING: the sum is computed
    by PostgreSQL over a Numeric(12, 2) column and serialised from a Decimal,
    so the exact two-decimal form survives the whole round trip. A float would
    have made this assertion fragile for no reason.

    The echoed unit_id/start/end are what make ChargeTotalResponse
    self-describing rather than a bare number.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-TOTAL")
    tenant_id = await create_tenant(client, staff_headers)

    await create_rent_charge(client, staff_headers, unit_id, tenant_id, amount="30000.00")
    water_response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "water",
            "amount": "1250.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )
    assert water_response.status_code == 201

    response = await client.get(
        f"/api/v1/charges/unit/{unit_id}/total",
        params={"start": "2026-08-01", "end": "2026-08-01"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == "31250.00"
    assert body["unit_id"] == unit_id
    assert body["start"] == "2026-08-01"
    assert body["end"] == "2026-08-01"


async def test_get_unit_total_excludes_cancelled(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A cancelled charge is genuinely excluded from the total.

    This is the only test that proves is_cancelled does real work in the
    aggregate query rather than merely being stored and returned. Two charges
    totalling 31250.00 are created, the 1250.00 water charge is voided, and
    the total must drop to exactly 30000.00.

    That is the whole justification for cancel-instead-of-delete: the voided
    row survives for the audit trail, but it stops counting as money owed.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-TOTAL-CANCEL")
    tenant_id = await create_tenant(client, staff_headers)

    await create_rent_charge(client, staff_headers, unit_id, tenant_id, amount="30000.00")
    water_response = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit_id,
            "category": "water",
            "amount": "1250.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )
    assert water_response.status_code == 201
    water_id = water_response.json()["id"]

    cancel_response = await client.patch(
        f"/api/v1/charges/{water_id}/cancel",
        json={},
        headers=admin_headers,
    )
    assert cancel_response.status_code == 200
    assert cancel_response.json()["is_cancelled"] is True

    response = await client.get(
        f"/api/v1/charges/unit/{unit_id}/total",
        params={"start": "2026-08-01", "end": "2026-08-01"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    # 30000.00, not 31250.00 — the voided water charge no longer counts.
    assert response.json()["total"] == "30000.00"

    # And the row itself is still there: cancelled, not destroyed. Without
    # this second assertion the test would also pass if cancel had deleted it.
    still_there = await client.get(f"/api/v1/charges/{water_id}", headers=staff_headers)
    assert still_there.status_code == 200
    assert still_there.json()["is_cancelled"] is True


async def test_get_unit_total_empty_returns_zero(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A unit with no charges in range totals "0.00", never null.

    SQL SUM over zero rows returns NULL, not 0. The repository normalises that
    to Decimal("0.00") so callers never have to handle a missing figure — a
    client displaying a balance should show 0.00, not blank or "null".
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-TOTAL-EMPTY")

    response = await client.get(
        f"/api/v1/charges/unit/{unit_id}/total",
        params={"start": "2026-08-01", "end": "2026-08-01"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == "0.00"
    assert body["total"] is not None


async def test_get_unit_total_backwards_range_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A backwards range on the total returns 400, and this matters more here.

    On the history endpoint a backwards range would return a suspicious empty
    list. Here it would return a confident, authoritative-looking "0.00" — a
    wrong balance is a worse failure than a wrong-looking empty list, which is
    why the same guard exists on both methods.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-TOTAL-BACK")

    response = await client.get(
        f"/api/v1/charges/unit/{unit_id}/total",
        params={"start": "2026-08-01", "end": "2026-06-01"},
        headers=staff_headers,
    )

    assert response.status_code == 400


# =============================================================================
# Update — admin only
# =============================================================================
async def test_update_charge_forbidden_for_staff(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A staff member cannot correct a charge — 403, update is admin-only.

    Note the asymmetry this asserts: the SAME staff token that successfully
    CREATED this charge is rejected when changing it. Issuing a bill is
    routine; changing what someone already owes is not.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-UPD-403")
    tenant_id = await create_tenant(client, staff_headers)
    charge = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    response = await client.patch(
        f"/api/v1/charges/{charge['id']}",
        json={"amount": "25000.00"},
        headers=staff_headers,
    )

    assert response.status_code == 403


async def test_update_charge_success_by_admin(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """An admin PATCH updates only the field sent, leaving the rest untouched."""
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-UPD-OK")
    tenant_id = await create_tenant(client, staff_headers)
    charge = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    response = await client.patch(
        f"/api/v1/charges/{charge['id']}",
        json={"amount": "25000.00"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["amount"] == "25000.00"
    # None of these were sent in the PATCH body — exclude_unset in the
    # repository means they must be completely unchanged, not overwritten with
    # None. category and the party fields are not even present on
    # ChargeUpdate, so nothing could have reached them.
    assert body["category"] == charge["category"]
    assert body["period"] == charge["period"]
    assert body["unit_id"] == charge["unit_id"]
    assert body["tenant_id"] == charge["tenant_id"]
    assert body["is_cancelled"] is False


# =============================================================================
# Cancel — admin only, and the replacement for delete
# =============================================================================
async def test_cancel_charge_forbidden_for_staff(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A staff member cannot void a charge — 403, cancel is admin-only.

    Cancelling is this router's equivalent of the delete endpoints elsewhere,
    and it keeps the same superuser guard those have.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-CANCEL-403")
    tenant_id = await create_tenant(client, staff_headers)
    charge = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    response = await client.patch(
        f"/api/v1/charges/{charge['id']}/cancel",
        json={},
        headers=staff_headers,
    )

    assert response.status_code == 403


async def test_cancel_charge_success_by_admin(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """An admin cancel returns 200 with is_cancelled true, and the row survives.

    An empty JSON body is sent deliberately: ChargeCancel carries only
    is_cancelled and defaults it to True, so the endpoint's whole purpose is
    expressed without the client having to state it.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-CANCEL-OK")
    tenant_id = await create_tenant(client, staff_headers)
    charge = await create_rent_charge(client, staff_headers, unit_id, tenant_id)
    assert charge["is_cancelled"] is False

    response = await client.patch(
        f"/api/v1/charges/{charge['id']}/cancel",
        json={},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["is_cancelled"] is True

    # The row is voided, not removed — a follow-up GET still finds it. This is
    # the audit trail the whole no-delete design exists to preserve.
    follow_up = await client.get(f"/api/v1/charges/{charge['id']}", headers=staff_headers)
    assert follow_up.status_code == 200
    assert follow_up.json()["amount"] == "30000.00"


# =============================================================================
# The 409 guards on closed records
# =============================================================================
async def test_cancel_already_cancelled_charge_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """Cancelling an already-cancelled charge returns 409, not a silent no-op.

    Silently succeeding would misinform the admin: they would walk away
    believing their action voided the charge when it did nothing. A 409 tells
    them the true state — someone already voided this — which may also signal
    that a colleague is working on the same problem.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-RECANCEL")
    tenant_id = await create_tenant(client, staff_headers)
    charge = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    first = await client.patch(
        f"/api/v1/charges/{charge['id']}/cancel",
        json={},
        headers=admin_headers,
    )
    assert first.status_code == 200

    second = await client.patch(
        f"/api/v1/charges/{charge['id']}/cancel",
        json={},
        headers=admin_headers,
    )

    assert second.status_code == 409


async def test_update_cancelled_charge_rejected(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """Editing a cancelled charge returns 409 — a voided record is closed.

    Editing a voided row rewrites history: it would then claim "we cancelled a
    25000 charge" when what actually happened was "we cancelled a 30000 charge
    and issued a 25000 one instead". The correct action for a correction is
    creating a NEW charge and leaving the voided one intact — the same
    principle as a paper receipt book, where a wrong receipt is marked VOID
    and left in place rather than erased and overwritten.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-UPD-CANCELLED")
    tenant_id = await create_tenant(client, staff_headers)
    charge = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    cancel_response = await client.patch(
        f"/api/v1/charges/{charge['id']}/cancel",
        json={},
        headers=admin_headers,
    )
    assert cancel_response.status_code == 200

    response = await client.patch(
        f"/api/v1/charges/{charge['id']}",
        json={"amount": "25000.00"},
        headers=admin_headers,
    )

    assert response.status_code == 409


# =============================================================================
# No delete endpoint
# =============================================================================
async def test_charges_has_no_delete_endpoint(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """DELETE /charges/{id} returns 405 — Charge has no delete route by design.

    Financial records are voided via PATCH /charges/{id}/cancel, never
    destroyed, so the audit trail always survives: what was billed, and the
    fact that it was later cancelled, both stay recoverable. That absence is
    deliberate at all three layers — no delete() on ChargeRepository, no
    delete_charge() on ChargeService, no DELETE route on the router — and none
    should ever be added.

    405 Method Not Allowed is the honest response here: the path exists (GET
    and PATCH both work on it), the method does not. Asserting it directly
    means an accidentally-added DELETE route fails this test loudly, rather
    than shipping unnoticed because nothing was watching for it.

    The request is made as an ADMIN deliberately: a 403 from a staff token
    would prove nothing, since it could equally mean "the route exists but you
    lack permission". An admin has permission for every write on this router,
    so a 405 can only mean the route genuinely does not exist.
    """
    owner_id = await create_owner(client, admin_headers)
    unit_id = await create_unit(client, admin_headers, owner_id, "CHG-NO-DELETE")
    tenant_id = await create_tenant(client, staff_headers)
    charge = await create_rent_charge(client, staff_headers, unit_id, tenant_id)

    response = await client.delete(f"/api/v1/charges/{charge['id']}", headers=admin_headers)

    assert response.status_code == 405

    # The charge is still there afterwards, untouched and not cancelled — the
    # rejected DELETE changed nothing at all.
    follow_up = await client.get(f"/api/v1/charges/{charge['id']}", headers=staff_headers)
    assert follow_up.status_code == 200
    assert follow_up.json()["is_cancelled"] is False
