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
created_by audit trail, the fetch-then-act 404 paths, and the 409
delete-conflict path now that Charge references tenants.id.
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


async def test_delete_tenant_with_charges_conflict(
    client: AsyncClient, admin_headers: dict, staff_headers: dict
):
    """Deleting a tenant who still owes a Charge returns 409, not 500.

    This path was UNREACHABLE until Charge landed. TenantService.delete_tenant
    has translated IntegrityError into a 409 Conflict for weeks, but no table
    referenced tenants.id, so PostgreSQL had no reason to reject any delete and
    that except block never once executed. Charge.tenant_id is declared with no
    ondelete, so it falls back to RESTRICT — deleting a tenant with a charge
    against them is now genuinely refused by the database, and this is the
    first test that actually drives the translation.

    It is also the first real verification of the capture-tenant_id-before-
    delete fix. rollback() inside the repository expires the Tenant object, so
    reading tenant.id inside the except block would hit SQLAlchemy's
    synchronous lazy-load path and raise MissingGreenlet. That bug was found
    and fixed in OwnerService.delete_owner and applied preemptively here; with
    nothing to trigger the branch, "preemptively" meant "unproven". If the
    capture were missing, this test would fail with a 500 rather than the
    expected 409.

    The follow-up GET is not decoration: a 409 alone only proves an error was
    raised. Asserting the tenant still reads back with 200 proves the delete
    was genuinely rolled back rather than half-applied.
    """
    # A Charge cannot exist on its own: it needs a real unit, and a Unit in
    # turn needs a real owner. Both of those creations are admin-only, unlike
    # the tenant and the charge themselves.
    owner = await client.post(
        "/api/v1/owners",
        json={
            "name": "Amina Hassan",
            "phone": "0707234780",
            "email": "amina@example.com",
            "national_id": "12345678",
        },
        headers=admin_headers,
    )
    assert owner.status_code == 201

    # unit_type "shop" requires bedrooms to be absent entirely, so the body
    # carries no bedrooms key.
    unit = await client.post(
        "/api/v1/units",
        json={
            "unit_number": "TEST-TNT-CONFLICT",
            "floor": 0,
            "unit_type": "shop",
            "owner_id": owner.json()["id"],
        },
        headers=admin_headers,
    )
    assert unit.status_code == 201

    created = await client.post(
        "/api/v1/tenants",
        json=valid_tenant_payload("Charged Tenant"),
        headers=staff_headers,
    )
    assert created.status_code == 201
    tenant_id = created.json()["id"]

    # amount is sent as a STRING, never a JSON float — Numeric(12, 2) pairs
    # with Decimal, and a binary float cannot hold "30000.00" exactly.
    # period must be the first day of a month.
    charge = await client.post(
        "/api/v1/charges",
        json={
            "unit_id": unit.json()["id"],
            "category": "rent",
            "amount": "30000.00",
            "period": "2026-08-01",
            "tenant_id": tenant_id,
        },
        headers=staff_headers,
    )
    assert charge.status_code == 201

    response = await client.delete(f"/api/v1/tenants/{tenant_id}", headers=admin_headers)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail
    assert "cannot delete tenant" in detail.lower()

    # The critical half of this test. Without it, a delete that had somehow
    # succeeded while still returning 409 would go unnoticed.
    follow_up = await client.get(f"/api/v1/tenants/{tenant_id}", headers=admin_headers)
    assert follow_up.status_code == 200
