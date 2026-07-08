"""
End-to-end tests for the auth endpoints (/api/v1/auth/*).

Mirrors the manual Swagger flow — register, login, me — plus the failure
cases that matter for a security-sensitive surface: duplicate emails,
invalid input, bad credentials, and missing/invalid tokens.
"""

from httpx import AsyncClient

VALID_USER = {
    "email": "nouh@damal.com",
    "password": "secret123",
    "full_name": "Nouh Jama",
}


# =============================================================================
# Registration
# =============================================================================
async def test_register_success(client: AsyncClient):
    """A valid payload creates a user and never returns password fields."""
    response = await client.post("/api/v1/auth/register", json=VALID_USER)

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == VALID_USER["email"]
    assert "id" in body
    assert "hashed_password" not in body
    assert "password" not in body


async def test_register_duplicate_email(client: AsyncClient):
    """Registering the same email twice rejects the second attempt with 400."""
    first = await client.post("/api/v1/auth/register", json=VALID_USER)
    assert first.status_code == 201

    second = await client.post("/api/v1/auth/register", json=VALID_USER)
    assert second.status_code == 400


async def test_register_invalid_email(client: AsyncClient):
    """A malformed email fails Pydantic validation before the service runs."""
    payload = {**VALID_USER, "email": "notanemail"}
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422


async def test_register_short_password(client: AsyncClient):
    """A password under the 8-character minimum fails validation."""
    payload = {**VALID_USER, "password": "short"}
    response = await client.post("/api/v1/auth/register", json=payload)

    assert response.status_code == 422


# =============================================================================
# Login
# =============================================================================
async def test_login_success(client: AsyncClient):
    """Correct credentials return a bearer access token."""
    await client.post("/api/v1/auth/register", json=VALID_USER)

    # OAuth2PasswordRequestForm is form data, not JSON — the field is
    # "username" even though we log in with an email.
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": VALID_USER["email"], "password": VALID_USER["password"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert body["token_type"] == "bearer"


async def test_login_wrong_password(client: AsyncClient):
    """An existing user with the wrong password is rejected with 401."""
    await client.post("/api/v1/auth/register", json=VALID_USER)

    response = await client.post(
        "/api/v1/auth/login",
        data={"username": VALID_USER["email"], "password": "wrong-password"},
    )

    assert response.status_code == 401


async def test_login_nonexistent_user(client: AsyncClient):
    """A never-registered email gets the same 401 as a wrong password.

    Identical status/detail for "no such user" and "wrong password" is
    intentional — it prevents attackers from using login to enumerate
    which emails are registered.
    """
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "ghost@damal.com", "password": "whatever123"},
    )

    assert response.status_code == 401


# =============================================================================
# Me
# =============================================================================
async def test_me_with_valid_token(client: AsyncClient):
    """A valid bearer token returns the authenticated user's profile."""
    await client.post("/api/v1/auth/register", json=VALID_USER)
    login_response = await client.post(
        "/api/v1/auth/login",
        data={"username": VALID_USER["email"], "password": VALID_USER["password"]},
    )
    token = login_response.json()["access_token"]

    response = await client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == VALID_USER["email"]
    assert "hashed_password" not in body


async def test_me_without_token(client: AsyncClient):
    """No Authorization header is rejected with 401."""
    response = await client.get("/api/v1/auth/me")

    assert response.status_code == 401


async def test_me_with_invalid_token(client: AsyncClient):
    """A malformed/unsigned token is rejected with 401."""
    response = await client.get(
        "/api/v1/auth/me", headers={"Authorization": "Bearer not-a-real-token"}
    )

    assert response.status_code == 401
