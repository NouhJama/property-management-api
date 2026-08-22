"""
End-to-end tests for the Tenant endpoints (/api/v1/tenants/*).

The permission split here is INVERTED relative to Owner and Unit, and these
tests assert that inversion directly rather than assuming the earlier
pattern:

  create, read, list, update  → ANY authenticated staff member
  delete                      → admin only

Registering and correcting tenant records is routine, frequent front-desk
work — a walk-in renter asking about an available unit — so it deliberately
does not require an admin. Deletion is the one irreversible, infrequent
operation on this router, and keeps the superuser guard.

That means the tests below invert their Unit counterparts on four of the
five routes: where test_units.py asserts a staff member gets 403 on create
and update, the tenant equivalents assert staff SUCCEED. Only
test_delete_tenant_forbidden_for_staff keeps the 403 shape.

Also covers input validation (name minimum length, phone format), the
created_by audit trail, and the fetch-then-act 404 paths.

Note: the 409 delete-conflict path is NOT tested here — nothing currently
references Tenant with a RESTRICT foreign key. See the comment at the
bottom of this file.
"""

from httpx import AsyncClient


# =============================================================================
# Helpers
# =============================================================================
# A plain module-level function rather than a fixture: Tenant has no foreign
# key to satisfy and no unique field, so a valid body needs no setup at all —
# unlike Unit, which had to create an Owner first. Tests that need a tenant
# to already exist create one inline with staff_headers, since creation is
# open to staff on this router.
def valid_tenant_payload(name: str = "Test Tenant") -> dict:
    """Return a minimal valid TenantCreate body.

    Phone is sent in local Kenyan form ("0711223344") and comes back
    normalised to E164 ("+254711223344") by DamalPhoneNumber — same
    convention as test_owners.py and test_units.py.

    The name is parameterised so a test can vary it, but nothing on Tenant is
    unique, so reusing the default across tests is harmless (unlike Unit's
    unit_number).
    """
    return {
        "name": name,
        "phone": "0711223344",
        "email": "tenant@example.com",
        "national_id": "87654321",
    }


# =============================================================================
# Create
# =============================================================================
async def test_create_tenant_success(client: AsyncClient, staff_headers: dict):
    """A staff member creating a valid tenant gets 201 and a normalised phone.

    Note the fixture: this is staff_headers, NOT admin_headers. Creation
    being open to ordinary staff is the defining difference between this
    router and Owner/Unit, and it is asserted here on the happy path.
    """
    response = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload(),
        headers=staff_headers,
    )

    assert response.status_code == 201
    body = response.json()
    assert body["name"] == "Test Tenant"
    # Sent as "0711223344" — DamalPhoneNumber normalises to E164 on the way in.
    assert body["phone"] == "+254711223344"
    assert body["email"] == "tenant@example.com"
    assert body["national_id"] == "87654321"
    assert "id" in body


async def test_create_tenant_created_by_matches_authenticated_user(
    client: AsyncClient, staff_headers: dict
):
    """created_by is taken from the token, never from anything the client sends.

    TenantCreate has no created_by field at all, so a client cannot even
    attempt to set it — the router passes current_user.id explicitly. This
    test proves the value that lands on the row is the authenticated staff
    member's own id, which is the one part of the audit trail a client could
    otherwise try to spoof.
    """
    me = await client.get("/api/v1/auth/me", headers=staff_headers)
    assert me.status_code == 200
    staff_id = me.json()["id"]

    response = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload(),
        headers=staff_headers,
    )

    assert response.status_code == 201
    assert response.json()["created_by"] == staff_id


async def test_create_tenant_unauthenticated(client: AsyncClient):
    """Open to any staff still means authenticated — no header at all is 401.

    "Not admin-only" is not "not protected": the route still depends on
    get_current_user, so an anonymous caller is rejected before the service
    is ever reached.
    """
    response = await client.post("/api/v1/tenants", json=valid_tenant_payload())

    assert response.status_code == 401


async def test_create_tenant_short_name(client: AsyncClient, staff_headers: dict):
    """A one-character name is rejected with 422 at the schema boundary.

    TenantBase sets min_length=2 on name, so the request never reaches the
    service or the database.
    """
    payload = {**valid_tenant_payload(), "name": "N"}

    response = await client.post("/api/v1/tenants", json=payload, headers=staff_headers)

    assert response.status_code == 422


async def test_create_tenant_invalid_phone(client: AsyncClient, staff_headers: dict):
    """A phone number that is not a real number is rejected with 422.

    "12345" is too short to be a valid Kenyan number, so DamalPhoneNumber
    fails validation before the service is reached.
    """
    payload = {**valid_tenant_payload(), "phone": "12345"}

    response = await client.post("/api/v1/tenants", json=payload, headers=staff_headers)

    assert response.status_code == 422


# =============================================================================
# List and read
# =============================================================================
async def test_list_tenants_any_staff(client: AsyncClient, staff_headers: dict):
    """Any logged-in staff member can list tenants.

    Both the creation and the listing use staff_headers here — on this router
    an admin is not required for either, so no admin token appears in this
    test at all.
    """
    created = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload("Listed Tenant"),
        headers=staff_headers,
    )
    assert created.status_code == 201
    created_id = created.json()["id"]

    response = await client.get("/api/v1/tenants", headers=staff_headers)

    assert response.status_code == 200
    tenants = response.json()
    assert any(tenant["id"] == created_id for tenant in tenants)


async def test_get_tenant_by_id_not_found(client: AsyncClient, staff_headers: dict):
    """Fetching an id that does not exist returns 404, not an empty body."""
    response = await client.get("/api/v1/tenants/999999", headers=staff_headers)

    assert response.status_code == 404


# =============================================================================
# Update — any staff
# =============================================================================
async def test_update_tenant_success_by_staff(client: AsyncClient, staff_headers: dict):
    """A staff member CAN update a tenant — the key inverted permission test.

    The direct counterpart to test_update_unit_forbidden_for_staff and
    test_update_owner_forbidden_for_staff, which both assert 403 for exactly
    this shape of request. Here the same kind of caller succeeds: correcting
    a mistyped phone number is routine front-desk work, not an administrative
    act.

    Also confirms exclude_unset — only phone was sent, so every other field
    must survive untouched rather than being overwritten with None.
    """
    created = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload("Updatable Tenant"),
        headers=staff_headers,
    )
    assert created.status_code == 201
    tenant = created.json()

    response = await client.patch(
        f"/api/v1/tenants/{tenant['id']}",
        json={"phone": "0722334455"},
        headers=staff_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["phone"] == "+254722334455"
    # None of these were sent in the PATCH body — they must be unchanged.
    assert body["name"] == tenant["name"]
    assert body["email"] == tenant["email"]
    assert body["national_id"] == tenant["national_id"]


async def test_update_tenant_unauthenticated(client: AsyncClient, staff_headers: dict):
    """Update is open to any staff, but still not to an anonymous caller — 401."""
    created = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload("Unauthenticated Update"),
        headers=staff_headers,
    )
    assert created.status_code == 201
    tenant_id = created.json()["id"]

    response = await client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"phone": "0722334455"},
    )

    assert response.status_code == 401


# =============================================================================
# Delete — admin only
# =============================================================================
async def test_delete_tenant_forbidden_for_staff(client: AsyncClient, staff_headers: dict):
    """A staff member cannot delete a tenant — 403, the ONE admin-only route here.

    The same staff token that just successfully created this tenant is
    rejected on the delete, which is precisely the split this router defines:
    routine writes are open, the irreversible one is not.
    """
    created = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload("Undeletable By Staff"),
        headers=staff_headers,
    )
    assert created.status_code == 201
    tenant_id = created.json()["id"]

    response = await client.delete(f"/api/v1/tenants/{tenant_id}", headers=staff_headers)

    assert response.status_code == 403


async def test_delete_tenant_success_by_admin(
    client: AsyncClient, staff_headers: dict, admin_headers: dict
):
    """An admin delete returns 204 and the tenant is genuinely gone afterwards.

    Created by staff, deleted by an admin — both halves of the split appear
    in a single test, mirroring how the endpoint is actually used.
    """
    created = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload("Deletable Tenant"),
        headers=staff_headers,
    )
    assert created.status_code == 201
    tenant_id = created.json()["id"]

    response = await client.delete(f"/api/v1/tenants/{tenant_id}", headers=admin_headers)

    assert response.status_code == 204

    # A 204 alone proves no error was raised — this second call proves the row
    # is actually gone.
    follow_up = await client.get(f"/api/v1/tenants/{tenant_id}", headers=admin_headers)
    assert follow_up.status_code == 404


# =============================================================================
# Not tested here — the 409 delete-conflict path
# =============================================================================
# TenantService.delete_tenant translates an IntegrityError into a 409 Conflict,
# the same shape as OwnerService.delete_owner (covered by
# test_delete_owner_with_units_conflict) and UnitService.delete_unit. It is
# deliberately NOT tested here: no table currently references Tenant through a
# RESTRICT foreign key, so there is no honest way to make PostgreSQL reject the
# delete. Faking it — mocking the repository to raise, or hand-writing a
# temporary FK — would test the mock rather than the behaviour. Add this test
# once Charge/Payment reference Tenant and the conflict becomes genuinely
# reachable.
