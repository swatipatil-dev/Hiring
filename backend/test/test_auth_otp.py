"""
OTP / password-reset auth flow tests.
Covers: verify-otp, resend-otp, forgot-password, reset-password, token-refresh.
"""
import pytest
from datetime import datetime, timedelta


def _register_unverified(client, email: str):
    """Register a user and return without verifying email."""
    resp = client.post("/api/v1/auth/register", json={
        "email": email,
        "password": "Pass@1234",
        "role": "candidate",
        "first_name": "OTP",
        "last_name": "Tester",
    })
    assert resp.status_code in (200, 201), f"Register failed: {resp.text}"
    return resp.json()


def _inject_otp(db, email: str, otp: str = "123456", expired: bool = False):
    """Directly set an OTP on a user row for test-controlled verification."""
    from src.models.models import User
    user = db.query(User).filter(User.email == email).first()
    assert user, f"User {email} not found in DB"
    user.otp_code = otp
    user.otp_expires_at = (
        datetime.utcnow() - timedelta(minutes=1)  # already expired
        if expired
        else datetime.utcnow() + timedelta(minutes=10)
    )
    db.flush()
    return user


# ── verify-otp ─────────────────────────────────────────────────────────────

@pytest.mark.auth
def test_verify_otp_success(client, db):
    """Correct OTP returns an access token and marks email verified."""
    from src.models.models import User
    email = "otp_verify_ok@test.com"
    _register_unverified(client, email)
    _inject_otp(db, email, otp="654321")

    resp = client.post("/api/v1/auth/verify-otp", json={"email": email, "otp": "654321"})
    assert resp.status_code == 200, resp.text
    assert "access_token" in resp.json()

    user = db.query(User).filter(User.email == email).first()
    assert user.email_verified is True
    assert user.otp_code is None


@pytest.mark.auth
def test_verify_otp_wrong_code(client, db):
    """Wrong OTP returns 400."""
    email = "otp_wrong@test.com"
    _register_unverified(client, email)
    _inject_otp(db, email, otp="111111")

    resp = client.post("/api/v1/auth/verify-otp", json={"email": email, "otp": "999999"})
    assert resp.status_code == 400


@pytest.mark.auth
def test_verify_otp_expired(client, db):
    """Expired OTP returns 400."""
    email = "otp_expired@test.com"
    _register_unverified(client, email)
    _inject_otp(db, email, otp="777777", expired=True)

    resp = client.post("/api/v1/auth/verify-otp", json={"email": email, "otp": "777777"})
    assert resp.status_code == 400
    assert "expired" in resp.json().get("detail", "").lower()


@pytest.mark.auth
def test_verify_otp_already_verified(client, db):
    """Trying to verify an already-verified account returns 400."""
    from src.models.models import User
    email = "otp_already_verified@test.com"
    _register_unverified(client, email)
    user = db.query(User).filter(User.email == email).first()
    user.email_verified = True
    db.flush()

    resp = client.post("/api/v1/auth/verify-otp", json={"email": email, "otp": "000000"})
    assert resp.status_code == 400


@pytest.mark.auth
def test_verify_otp_unknown_user(client):
    """Verifying OTP for non-existent user returns 404."""
    resp = client.post("/api/v1/auth/verify-otp", json={
        "email": "ghost@nonexistent.com", "otp": "123456"
    })
    assert resp.status_code == 404


# ── resend-otp ─────────────────────────────────────────────────────────────

@pytest.mark.auth
def test_resend_otp_unverified_user(client):
    """Resending OTP for an unverified user returns 200."""
    email = "otp_resend@test.com"
    _register_unverified(client, email)

    resp = client.post("/api/v1/auth/resend-otp", json={"email": email})
    assert resp.status_code == 200
    assert "email" in resp.json()


@pytest.mark.auth
def test_resend_otp_already_verified(client, db):
    """Resending OTP for an already-verified user returns 400."""
    from src.models.models import User
    email = "otp_resend_verified@test.com"
    _register_unverified(client, email)
    user = db.query(User).filter(User.email == email).first()
    user.email_verified = True
    db.flush()

    resp = client.post("/api/v1/auth/resend-otp", json={"email": email})
    assert resp.status_code == 400


@pytest.mark.auth
def test_resend_otp_unknown_user(client):
    """Resending OTP for non-existent email returns 404."""
    resp = client.post("/api/v1/auth/resend-otp", json={"email": "nobody@nope.com"})
    assert resp.status_code == 404


# ── forgot-password ────────────────────────────────────────────────────────

@pytest.mark.auth
def test_forgot_password_known_email(client, db):
    """Forgot-password for a registered email returns 200 (never leaks existence)."""
    from src.models.models import User
    email = "forgotpw_known@test.com"
    _register_unverified(client, email)
    user = db.query(User).filter(User.email == email).first()
    user.email_verified = True
    db.flush()

    resp = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert resp.status_code == 200
    assert "message" in resp.json()


@pytest.mark.auth
def test_forgot_password_unknown_email(client):
    """Forgot-password for unknown email still returns 200 (anti-enumeration)."""
    resp = client.post("/api/v1/auth/forgot-password", json={"email": "unknown@nope.com"})
    assert resp.status_code == 200


# ── reset-password ─────────────────────────────────────────────────────────

@pytest.mark.auth
def test_reset_password_success(client, db):
    """Valid OTP + new password resets successfully and allows login."""
    from src.models.models import User
    email = "resetpw_ok@test.com"
    _register_unverified(client, email)
    user = db.query(User).filter(User.email == email).first()
    user.email_verified = True
    db.flush()
    _inject_otp(db, email, otp="RESET1")

    resp = client.post("/api/v1/auth/reset-password", json={
        "email": email,
        "otp": "RESET1",
        "new_password": "NewPass@9999",
    })
    assert resp.status_code == 200, resp.text
    assert "reset" in resp.json().get("message", "").lower()

    # New password should work for login
    login_resp = client.post("/api/v1/auth/login", json={
        "email": email, "password": "NewPass@9999"
    })
    assert login_resp.status_code == 200
    assert "access_token" in login_resp.json()


@pytest.mark.auth
def test_reset_password_wrong_otp(client, db):
    """Wrong OTP on reset-password returns 400."""
    email = "resetpw_wrong@test.com"
    _register_unverified(client, email)
    _inject_otp(db, email, otp="123456")  # otp_code column is String(6)

    resp = client.post("/api/v1/auth/reset-password", json={
        "email": email, "otp": "999999", "new_password": "NewPass@9999"
    })
    assert resp.status_code == 400


@pytest.mark.auth
def test_reset_password_too_short(client, db):
    """Password shorter than 8 chars returns 400."""
    email = "resetpw_short@test.com"
    _register_unverified(client, email)
    _inject_otp(db, email, otp="SHORT1")

    resp = client.post("/api/v1/auth/reset-password", json={
        "email": email, "otp": "SHORT1", "new_password": "abc"
    })
    assert resp.status_code == 400


# ── token refresh ──────────────────────────────────────────────────────────

@pytest.mark.auth
def test_token_refresh(client, candidate_headers):
    """Valid token can be refreshed and the new token also grants access."""
    resp = client.post("/api/v1/auth/refresh", headers=candidate_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data

    new_headers = {"Authorization": f"Bearer {data['access_token']}"}
    me_resp = client.get("/api/v1/auth/me", headers=new_headers)
    assert me_resp.status_code == 200


@pytest.mark.auth
def test_token_refresh_without_auth(client):
    """Token refresh without a valid token returns 401/403."""
    resp = client.post("/api/v1/auth/refresh")
    assert resp.status_code in (401, 403)
