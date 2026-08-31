"""
End-to-end tests for the Unit endpoints (/api/v1/units/*).

Covers the permission split that defines this resource — create, structural
update and delete are admin-only, while list, read AND the status-only update
are open to any authenticated staff member. That status endpoint has no Owner
analogue: it is the one write path on this router deliberately left open to
ordinary staff, so it gets its own dedicated test.

Also covers input validation (duplicate unit_number, an owner_id that does not
exist, the bedrooms/unit_type matrix), the fetch-then-act 404 paths, and the
409 delete-conflict path now that Charge references units.id.
"""

from httpx import AsyncClient

# Phone is sent in local Kenyan form and comes back normalised to E164 by
# DamalPhoneNumber — same as test_owners.py.
OWNER_PAYLOAD = {
    "name": "Amina Hassan",
    "phone": "0707234780",
    "email": "amina@example.com",
    "national_id": "12345678",
}


# =============================================================================
# Helpers
# =============================================================================
# A plain async helper rather than a fixture: every unit test needs a valid
# owner_id, but several also need to control WHICH headers create it, and one
# needs no owner at all (the invalid-owner test). A helper keeps that choice
# at the call site — a fixture would force the same creation on every test.
async def create_owner(client: AsyncClient, admin_headers: dict) -> int:
    """Create a real Owner through the API as admin and return its id.

    Owner creation is admin-only, so this always uses admin_headers even in
    tests whose subject is a staff member — the staff caller is exercised on
    the unit endpoint itself, not on this setup step.
    """
    response = await client.post("/api/v1/owners", json=OWNER_PAYLOAD, headers=admin_headers)
    assert response.status_code == 201
    return response.json()["id"]


def valid_unit_payload(owner_id: int, unit_number: str = "TEST-G01") -> dict:
    """Return a minimal valid UnitCreate body for the given owner.

    unit_type is "shop", which requires bedrooms to be absent entirely — so
    the default payload carries no bedrooms key at all. unit_number is varied
    per test to avoid any cross-test interference, even though the schema is
    dropped and rebuilt between tests anyway.
    """
    return {
        "unit_number": unit_number,
        "floor": 0,
        "unit_type": "shop",
        "owner_id": owner_id,
    }


async def create_unit(client: AsyncClient, admin_headers: dict, unit_number: str) -> dict:
    """Create an owner and a unit as admin, returning the created unit body."""
    owner_id = await create_owner(client, admin_headers)
    response = await client.post(
        "/api/v1/units",
        json=valid_unit_payload(owner_id, unit_number),
        headers=admin_headers,
    )
    assert response.status_code == 201
    return response.json()


# =============================================================================
# Create
# =============================================================================
async def test_create_unit_success(client: AsyncClient, admin_headers: dict):
    """An admin creating a valid unit gets 201, status available, and an audit trail."""
    owner_id = await create_owner(client, admin_headers)

    # The admin's own id — created_by must match this, proving the router takes
    # it from the authenticated caller and never from the payload (UnitCreate
    # has no created_by field for a client to send).
    me = await client.get("/api/v1/auth/me", headers=admin_headers)
    assert me.status_code == 200
    admin_id = me.json()["id"]

    response = await client.post(
        "/api/v1/units",
        json=valid_unit_payload(owner_id, "TEST-G01"),
        headers=admin_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["unit_number"] == "TEST-G01"
    assert body["owner_id"] == owner_id
    # status is hardcoded by the service — UnitCreate has no status field at
    # all, so the client never sent this value.
    assert body["status"] == "available"
    assert body["created_by"] == admin_id
    assert "id" in body


async def test_create_unit_forbidden_for_staff(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """A regular staff member is rejected with 403 — create is admin-only."""
    owner_id = await create_owner(client, admin_headers)

    response = await client.post(
        "/api/v1/units",
        json=valid_unit_payload(owner_id, "TEST-G02"),
        headers=staff_headers,
    )

    assert response.status_code == 403


async def test_create_unit_unauthenticated(client: AsyncClient, admin_headers: dict):
    """No Authorization header at all is rejected with 401, not 403."""
    owner_id = await create_owner(client, admin_headers)

    response = await client.post("/api/v1/units", json=valid_unit_payload(owner_id, "TEST-G03"))

    assert response.status_code == 401


async def test_create_unit_duplicate_unit_number(client: AsyncClient, admin_headers: dict):
    """A second unit reusing an existing unit_number is rejected with 400.

    unit_number is unique building-wide; the service checks for a duplicate
    before inserting so the client gets a clean 400 rather than the database's
    unique constraint surfacing as a 500.
    """
    owner_id = await create_owner(client, admin_headers)
    payload = valid_unit_payload(owner_id, "TEST-DUP")

    first = await client.post("/api/v1/units", json=payload, headers=admin_headers)
    assert first.status_code == 201

    second = await client.post("/api/v1/units", json=payload, headers=admin_headers)

    assert second.status_code == 400


async def test_create_unit_invalid_owner(client: AsyncClient, admin_headers: dict):
    """An owner_id referencing no existing owner returns 400, not 500.

    Proves the IntegrityError → 400 translation: owner_id is a real foreign
    key, so the insert fails at commit time; the repository rolls back and
    re-raises, and UnitService.create_unit turns that into a clean 400.
    """
    response = await client.post(
        "/api/v1/units",
        json=valid_unit_payload(999999, "TEST-BAD-OWNER"),
        headers=admin_headers,
    )

    assert response.status_code == 400
    assert "999999" in response.json()["detail"]


async def test_create_unit_lodge_zero_bedrooms(client: AsyncClient, admin_headers: dict):
    """A lodge with 0 bedrooms fails validation with 422 — the corrected rule.

    Lodges are self-contained 1BR or 2BR units, not zero-bedroom studios, so 0
    is no longer valid after the correction confirmed via the floor plans.
    """
    owner_id = await create_owner(client, admin_headers)
    payload = {
        **valid_unit_payload(owner_id, "TEST-LODGE-0"),
        "unit_type": "lodge",
        "bedrooms": 0,
    }

    response = await client.post("/api/v1/units", json=payload, headers=admin_headers)

    assert response.status_code == 422


async def test_create_unit_lodge_valid_bedrooms(client: AsyncClient, admin_headers: dict):
    """A lodge with 1 bedroom is accepted with 201 — the other half of the rule."""
    owner_id = await create_owner(client, admin_headers)
    payload = {
        **valid_unit_payload(owner_id, "TEST-LODGE-1"),
        "unit_type": "lodge",
        "bedrooms": 1,
    }

    response = await client.post("/api/v1/units", json=payload, headers=admin_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["unit_type"] == "lodge"
    assert body["bedrooms"] == 1


async def test_create_unit_apartment_wrong_bedrooms(client: AsyncClient, admin_headers: dict):
    """An apartment with 1 bedroom fails validation with 422 — 2 or 3 only.

    No other apartment layout exists in this building, so 1 is rejected at the
    schema boundary before it ever reaches the service.
    """
    owner_id = await create_owner(client, admin_headers)
    payload = {
        **valid_unit_payload(owner_id, "TEST-APT-1"),
        "unit_type": "apartment",
        "bedrooms": 1,
    }

    response = await client.post("/api/v1/units", json=payload, headers=admin_headers)

    assert response.status_code == 422


# =============================================================================
# List and read
# =============================================================================
async def test_list_units_any_staff(client: AsyncClient, staff_headers: dict, admin_headers: dict):
    """Any logged-in staff member can list units — creation still needs an admin.

    The same permission split as owners: staff need unit records for day-to-day
    work, so reading is open even though writing is not.
    """
    created = await create_unit(client, admin_headers, "TEST-LIST-1")

    response = await client.get("/api/v1/units", headers=staff_headers)

    assert response.status_code == 200
    units = response.json()
    assert any(unit["id"] == created["id"] for unit in units)


async def test_get_unit_by_id_not_found(client: AsyncClient, staff_headers: dict):
    """Fetching an id that does not exist returns 404, not an empty body."""
    response = await client.get("/api/v1/units/999999", headers=staff_headers)

    assert response.status_code == 404


# =============================================================================
# Update — structural (admin only)
# =============================================================================
async def test_update_unit_forbidden_for_staff(
    client: AsyncClient, admin_headers: dict, staff_headers: dict
):
    """A staff member cannot change structural fields — 403, admin-only.

    This is the counterpart to the status test below: the SAME staff token
    that may change status is rejected here, which is the whole point of
    splitting the two endpoints.
    """
    unit = await create_unit(client, admin_headers, "TEST-UPD-403")

    response = await client.patch(
        f"/api/v1/units/{unit['id']}",
        json={"floor": 5},
        headers=staff_headers,
    )

    assert response.status_code == 403


async def test_update_unit_success(client: AsyncClient, admin_headers: dict):
    """An admin PATCH updates only the field sent, leaving the rest untouched."""
    unit = await create_unit(client, admin_headers, "TEST-UPD-OK")

    response = await client.patch(
        f"/api/v1/units/{unit['id']}",
        json={"floor": 5},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["floor"] == 5
    # None of these were sent in the PATCH body — exclude_unset in the
    # repository means they must be completely unchanged, not overwritten
    # with None.
    assert body["unit_number"] == unit["unit_number"]
    assert body["unit_type"] == unit["unit_type"]
    assert body["owner_id"] == unit["owner_id"]
    assert body["status"] == unit["status"]


# =============================================================================
# Update — status only (any staff)
# =============================================================================
async def test_update_unit_status_any_staff(
    client: AsyncClient, admin_headers: dict, staff_headers: dict
):
    """A staff member CAN change a unit's status — the key permission test.

    This is the one write endpoint on this router open to ordinary staff, and
    the only one with no Owner analogue. Marking a unit under_maintenance is
    routine day-to-day bookkeeping that should not require an admin, while the
    structural PATCH above stays admin-only.
    """
    unit = await create_unit(client, admin_headers, "TEST-STATUS-1")
    assert unit["status"] == "available"

    response = await client.patch(
        f"/api/v1/units/{unit['id']}/status",
        json={"status": "under_maintenance"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "under_maintenance"


async def test_update_unit_status_unauthenticated(client: AsyncClient, admin_headers: dict):
    """Open to any staff still means authenticated — no header at all is 401."""
    unit = await create_unit(client, admin_headers, "TEST-STATUS-401")

    response = await client.patch(
        f"/api/v1/units/{unit['id']}/status",
        json={"status": "under_maintenance"},
    )

    assert response.status_code == 401


# =============================================================================
# Delete
# =============================================================================
async def test_delete_unit_forbidden_for_staff(
    client: AsyncClient, admin_headers: dict, staff_headers: dict
):
    """A regular staff member cannot delete a unit — 403, delete is admin-only."""
    unit = await create_unit(client, admin_headers, "TEST-DEL-403")

    response = await client.delete(f"/api/v1/units/{unit['id']}", headers=staff_headers)

    assert response.status_code == 403


async def test_delete_unit_success(client: AsyncClient, admin_headers: dict):
    """An admin delete returns 204 and the unit is genuinely gone afterwards."""
    unit = await create_unit(client, admin_headers, "TEST-DEL-OK")

    response = await client.delete(f"/api/v1/units/{unit['id']}", headers=admin_headers)

    assert response.status_code == 204

    # A 204 alone proves no error was raised — this second call proves the row
    # is actually gone.
    follow_up = await client.get(f"/api/v1/units/{unit['id']}", headers=admin_headers)
    assert follow_up.status_code == 404


async def test_delete_unit_with_charges_conflict(
    client: AsyncClient, admin_headers: dict, staff_headers: dict
):
    """Deleting a unit that still has a Charge against it returns 409, not 500.

    This path was UNREACHABLE until Charge landed. UnitService.delete_unit has
    translated IntegrityError into a 409 Conflict for weeks, but no table
    referenced units.id, so PostgreSQL had no reason to reject any delete and
    that except block never once executed. Charge.unit_id is declared with no
    ondelete, so it falls back to RESTRICT — deleting a unit with a charge
    against it is now genuinely refused by the database, and this is the first
    test that actually drives the translation.

    It is also the first real verification of the capture-unit_id-before-delete
    fix. rollback() inside the repository expires the Unit object, so reading
    unit.id inside the except block would hit SQLAlchemy's synchronous
    lazy-load path and raise MissingGreenlet. That bug was found and fixed in
    OwnerService.delete_owner and applied preemptively here; with nothing to
    trigger the branch, "preemptively" meant "unproven". If the capture were
    missing, this test would fail with a 500 rather than the expected 409.

    The follow-up GET is not decoration: a 409 alone only proves an error was
    raised. Asserting the unit still reads back with 200 proves the delete was
    genuinely rolled back rather than half-applied.
    """
    unit = await create_unit(client, admin_headers, "TEST-DEL-CONFLICT")

    # A Charge needs a real tenant as the owing party for the rent category.
    # Tenant creation is open to staff on that router, so no admin is needed.
    tenant = await client.post(
        "/api/v1/tenants",
        json={
            "name": "Yusuf Omar",
            "phone": "0711223344",
            "email": "yusuf@example.com",
            "national_id": "87654321",
        },
        headers=staff_headers,
    )
    assert tenant.status_code == 201

    # amount is sent as a STRING, never a JSON float — Numeric(12, 2) pairs
    # with Decimal, and a binary float cannot hold "30000.00" exactly.
    # period must be the first day of a month.
    charge = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit["id"],
            "category": "rent",
            "amount": "30000.00",
            "period": "2026-08-01",
            "tenant_id": tenant.json()["id"],
        },
        headers=staff_headers,
    )
    assert charge.status_code == 201

    response = await client.delete(f"/api/v1/units/{unit['id']}", headers=admin_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail
    assert "cannot delete unit" in detail.lower()

    # The critical half of this test. Without it, a delete that had somehow
    # succeeded while still returning 409 would go unnoticed.
    follow_up = await client.get(f"/api/v1/units/{unit['id']}", headers=admin_headers)
    assert follow_up.status_code == 200
