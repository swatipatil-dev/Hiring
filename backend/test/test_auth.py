"""
Authentication tests — register, login, token validation, inactive user.
"""
import pytest


@pytest.mark.auth
def test_register_success(client):
    resp = client.post("/api/v1/auth/register", json={
        "email": "new_user@test.com",
        "password": "Pass@1234",
        "role": "candidate",
        "first_name": "Jane",
        "last_name": "Doe",
    })
    assert resp.status_code in (200, 201)
    data = resp.json()
    assert data.get("email") == "new_user@test.com"


@pytest.mark.auth
def test_register_duplicate_email(client):
    payload = {
        "email": "dup@test.com",
        "password": "Pass@1234",
        "role": "candidate",
        "first_name": "A",
        "last_name": "B",
    }
    client.post("/api/v1/auth/register", json=payload)
    resp = client.post("/api/v1/auth/register", json=payload)
    assert resp.status_code == 400
    assert "already" in resp.json().get("detail", "").lower()


@pytest.mark.auth
def test_login_success(client, db):
    from src.models.models import User
    client.post("/api/v1/auth/register", json={
        "email": "login_ok@test.com",
        "password": "Pass@1234",
        "role": "candidate",
        "first_name": "A",
        "last_name": "B",
    })
    # Verify email so login passes the email-verification gate
    user = db.query(User).filter(User.email == "login_ok@test.com").first()
    user.email_verified = True
    db.flush()
    resp = client.post("/api/v1/auth/login", json={
        "email": "login_ok@test.com",
        "password": "Pass@1234",
    })
    assert resp.status_code == 200
    assert "access_token" in resp.json()


@pytest.mark.auth
def test_login_wrong_password(client):
    client.post("/api/v1/auth/register", json={
        "email": "wrongpw@test.com",
        "password": "Pass@1234",
        "role": "candidate",
        "first_name": "A",
        "last_name": "B",
    })
    resp = client.post("/api/v1/auth/login", json={
        "email": "wrongpw@test.com",
        "password": "BadPassword",
    })
    assert resp.status_code in (400, 401)


@pytest.mark.auth
def test_login_inactive_user(client, db):
    from src.models.models import User
    client.post("/api/v1/auth/register", json={
        "email": "inactive@test.com",
        "password": "Pass@1234",
        "role": "candidate",
        "first_name": "A",
        "last_name": "B",
    })
    # Verify email + deactivate the user so we test the inactive-user path
    user = db.query(User).filter(User.email == "inactive@test.com").first()
    user.email_verified = True
    user.is_active = False
    db.commit()

    resp = client.post("/api/v1/auth/login", json={
        "email": "inactive@test.com",
        "password": "Pass@1234",
    })
    assert resp.status_code == 400
    assert "inactive" in resp.json().get("detail", "").lower()


@pytest.mark.auth
def test_get_me_with_valid_token(client, candidate_headers):
    resp = client.get("/api/v1/auth/me", headers=candidate_headers)
    assert resp.status_code == 200
    assert "email" in resp.json()


@pytest.mark.auth
def test_get_me_without_token(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)


@pytest.mark.auth
def test_logout(client, candidate_headers):
    resp = client.post("/api/v1/auth/logout", headers=candidate_headers)
    assert resp.status_code == 200
