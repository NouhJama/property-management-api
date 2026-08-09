"""
End-to-end tests for the Owner endpoints (/api/v1/owners/*).

Covers the permission split that defines this resource — create, update and
delete are admin-only, while read and list are open to any authenticated
staff member — plus input validation (name, phone) and the 409 conflict
raised when deleting an owner who still owns a Unit.
"""

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

# This import MUST stay at module level, not inside the test that uses it.
# conftest's db_session fixture builds the schema with
# Base.metadata.create_all, which only creates tables for models that have
# already been imported — and nothing in the app's import graph reaches
# app/models/unit.py. pytest imports this module at collection time, before
# any fixture runs, so importing here is what registers `units` in
# Base.metadata in time for create_all to build it.
from app.models.unit import Unit, UnitStatus, UnitType

# Phone is sent in local Kenyan form and comes back normalised to E164 by
# DamalPhoneNumber: "0707234780" → "+254707234780".
VALID_OWNER_PAYLOAD = {
    "name": "Amina Hassan",
    "phone": "0707234780",
    "email": "amina@example.com",
    "national_id": "12345678",
}


# =============================================================================
# Create
# =============================================================================
async def test_create_owner_success(client: AsyncClient, admin_headers: dict):
    """An admin creating a valid owner gets 201 and a normalised phone back."""
    response = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=admin_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == VALID_OWNER_PAYLOAD["name"]
    # DamalPhoneNumber normalises to E164 before the value ever reaches the DB.
    assert body["phone"] == "+254707234780"
    # type is hardcoded by the service — OwnerCreate has no type field at all,
    # so the client never sent this value.
    assert body["type"] == "individual"
    assert "id" in body
    assert "hashed_password" not in body


async def test_create_owner_forbidden_for_staff(client: AsyncClient, staff_headers: dict):
    """A regular staff member is rejected with 403 — create is admin-only."""
    response = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=staff_headers)

    assert response.status_code == 403


async def test_create_owner_unauthenticated(client: AsyncClient):
    """No Authorization header at all is rejected with 401, not 403."""
    response = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD)

    assert response.status_code == 401


async def test_create_owner_invalid_phone(client: AsyncClient, admin_headers: dict):
    """A phone number that is not a real number fails validation with 422."""
    payload = {**VALID_OWNER_PAYLOAD, "phone": "12345"}
    response = await client.post("/api/v1/owners", json=payload, headers=admin_headers)

    assert response.status_code == 422


async def test_create_owner_short_name(client: AsyncClient, admin_headers: dict):
    """A name below the 2-character minimum fails validation with 422."""
    payload = {**VALID_OWNER_PAYLOAD, "name": "N"}
    response = await client.post("/api/v1/owners", json=payload, headers=admin_headers)

    assert response.status_code == 422


# =============================================================================
# List and read
# =============================================================================
async def test_list_owners_any_staff(client: AsyncClient, staff_headers: dict, admin_headers: dict):
    """Any logged-in staff member can list owners — the key permission split.

    Creation still requires an admin, but reading the resulting list does
    not: staff need owner records for day-to-day work.
    """
    created = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=admin_headers)
    assert created.status_code == 201
    created_id = created.json()["id"]

    response = await client.get("/api/v1/owners", headers=staff_headers)

    assert response.status_code == 200
    owners = response.json()
    assert any(owner["id"] == created_id for owner in owners)


async def test_get_owner_by_id_not_found(client: AsyncClient, staff_headers: dict):
    """Fetching an id that does not exist returns 404, not an empty body."""
    response = await client.get("/api/v1/owners/999999", headers=staff_headers)

    assert response.status_code == 404


# =============================================================================
# Update
# =============================================================================
async def test_update_owner_forbidden_for_staff(
    client: AsyncClient, admin_headers: dict, staff_headers: dict
):
    """A regular staff member cannot update an owner — 403, update is admin-only."""
    created = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=admin_headers)
    owner_id = created.json()["id"]

    response = await client.patch(
        f"/api/v1/owners/{owner_id}",
        json={"phone": "0711111111"},
        headers=staff_headers,
    )

    assert response.status_code == 403


async def test_update_owner_success(client: AsyncClient, admin_headers: dict):
    """An admin PATCH updates only the field sent, leaving the rest untouched."""
    created = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=admin_headers)
    owner_id = created.json()["id"]
    original_email = created.json()["email"]

    response = await client.patch(
        f"/api/v1/owners/{owner_id}",
        json={"phone": "0711111111"},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+254711111111"
    # email was never sent in the PATCH body — exclude_unset in the repository
    # means it must be completely unchanged, not overwritten with None.
    assert body["email"] == original_email


# =============================================================================
# Delete
# =============================================================================
async def test_delete_owner_forbidden_for_staff(
    client: AsyncClient, admin_headers: dict, staff_headers: dict
):
    """A regular staff member cannot delete an owner — 403, delete is admin-only."""
    created = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=admin_headers)
    owner_id = created.json()["id"]

    response = await client.delete(f"/api/v1/owners/{owner_id}", headers=staff_headers)

    assert response.status_code == 403


async def test_delete_owner_success(client: AsyncClient, admin_headers: dict):
    """An admin delete returns 204 and the owner is genuinely gone afterwards."""
    created = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=admin_headers)
    owner_id = created.json()["id"]

    response = await client.delete(f"/api/v1/owners/{owner_id}", headers=admin_headers)

    assert response.status_code == 204

    # A 204 alone proves no error was raised — this second call proves the row
    # is actually gone.
    follow_up = await client.get(f"/api/v1/owners/{owner_id}", headers=admin_headers)
    assert follow_up.status_code == 404


async def test_delete_owner_with_units_conflict(
    client: AsyncClient, admin_headers: dict, db_session: AsyncSession
):
    """Deleting an owner who still owns a Unit returns 409, not 500.

    Proves the full 409 path end to end: PostgreSQL's RESTRICT on
    Unit.owner_id raises an IntegrityError, which OwnerService.delete_owner
    catches and translates into a clean 409 Conflict.
    """

    created = await client.post("/api/v1/owners", json=VALID_OWNER_PAYLOAD, headers=admin_headers)
    owner_id = created.json()["id"]

    # The Unit row is created directly against the session — Unit has no
    # repository, service or router yet, so there is no API path to it.
    unit = Unit(
        unit_number="TEST-CONFLICT-1",
        floor=1,
        unit_type=UnitType.APARTMENT,
        bedrooms=2,
        owner_id=owner_id,
        status=UnitStatus.AVAILABLE,
    )
    db_session.add(unit)
    await db_session.commit()

    response = await client.delete(f"/api/v1/owners/{owner_id}", headers=admin_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail
    assert "cannot delete owner" in detail.lower()
