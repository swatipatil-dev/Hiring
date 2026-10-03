"""Auth gaps found in the 2026-09-30 review, each pinned by a test that fails without the fix.

* Passwords — bcrypt reads only 72 bytes, so longer passwords are refused, not truncated.
* Sessions  — a signed file link dies with the session; "delete user" revokes tokens.
* OTP       — the reset/verify email is sent after the response, so timing can't
              reveal whether an account exists; 500s don't echo exception text.
* MFA       — changing the second factor needs the password and counts failures toward
              the lockout; recovery codes are keyed HMACs (old SHA-256 ones still work);
              an admin can reset a colleague's MFA, audited; an undecryptable secret is logged.
"""
import hashlib
import json
import logging

import pytest

from test.test_auth_hardening import PW, _company, _enrolled, _headers, _login, _user
from test.test_file_access import world  # noqa: F401  (fixture)


# ── passwords ────────────────────────────────────────────────────────────────

def test_password_longer_than_bcrypt_reads_is_refused():
    from fastapi import HTTPException
    from src.auth import validate_password_strength
    validate_password_strength("Aa1!" + "x" * 68)                  # exactly 72 bytes
    with pytest.raises(HTTPException, match="72 bytes"):
        validate_password_strength("Aa1!" + "x" * 69)              # 73 — would be truncated
    with pytest.raises(HTTPException, match="72 bytes"):
        validate_password_strength("Aa1!" + "é" * 40)              # 44 chars but 84 bytes


def test_two_passwords_sharing_72_bytes_cannot_both_be_set(client, db):
    """What the limit is for: before it, any suffix after byte 72 was ignored."""
    u = _user(db, "hr", _company(db).id)
    tok = _login(client, u.email).json()["access_token"]
    r = client.post("/api/v1/auth/change-password", headers=_headers(tok),
                    json={"current_password": PW, "new_password": "Aa1!" + "y" * 80})
    assert r.status_code == 400 and "72 bytes" in r.json()["detail"]


# ── sessions ─────────────────────────────────────────────────────────────────

def test_signed_file_link_dies_when_the_session_is_revoked(client, db, world):
    from src.auth import revoke_user_sessions
    from src.services.file_access import application_file_url
    url = application_file_url(world["app"], "resume", world["owner_hr"])
    assert client.get(url).status_code == 200
    revoke_user_sessions(world["owner_hr"])      # what logout / password change / deactivate do
    db.flush()
    assert client.get(url).status_code == 404, "a handed-out link outlived the session"


def test_delete_user_revokes_tokens_even_after_reactivation(client, db, admin_headers):
    from src.models.models import User
    admin = db.query(User).filter(User.email == "admin_test@test.com").first()
    hr = _user(db, "hr", admin.company_id)
    h = _headers(_login(client, hr.email).json()["access_token"])
    assert client.delete(f"/api/v1/admin/users/{hr.id}", headers=admin_headers).status_code == 200
    assert client.put(f"/api/v1/admin/users/{hr.id}/status?is_active=true",
                      headers=admin_headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


# ── OTP email timing, error text ─────────────────────────────────────────────

def test_reset_email_is_sent_after_the_response(client, db, monkeypatch):
    """The send must not sit inside the request: it ran only for a real account, so the
    response time gave away which emails are registered."""
    from src.services import email_service
    u = _user(db, "candidate", _company(db).id)
    sent, in_request = [], []

    def fake_send(to, name, otp, smtp_config=None):
        sent.append(to)
        in_request.append(getattr(fake_send, "handler_running", False))

    monkeypatch.setattr(email_service, "send_password_reset_otp", fake_send)
    import routes.auth_routes as ar
    real = ar._send_auth_mail_later

    def spy(background_tasks, *a, **kw):
        fake_send.handler_running = True
        try:
            return real(background_tasks, *a, **kw)
        finally:
            fake_send.handler_running = False

    monkeypatch.setattr(ar, "_send_auth_mail_later", spy)
    hit = client.post("/api/v1/auth/forgot-password", json={"email": u.email})
    miss = client.post("/api/v1/auth/forgot-password", json={"email": "nobody@example.com"})
    assert hit.json() == miss.json()
    assert sent == [u.email]
    assert in_request == [False], "the email was sent while the request was still being handled"


def test_login_500_does_not_echo_the_exception(client, db, monkeypatch):
    import routes.auth_routes as ar

    def boom(*a, **kw):
        raise RuntimeError("(pymysql) Access denied for user 'root'@'10.0.0.5' secret-table")

    monkeypatch.setattr(ar, "login_user", boom, raising=False)
    monkeypatch.setattr(ar, "authenticate_user", boom, raising=False)
    r = client.post("/api/v1/auth/login", json={"email": "someone@example.com", "password": "whatever1A"})
    assert r.status_code == 500
    assert "pymysql" not in r.text and "secret-table" not in r.text


# ── MFA ──────────────────────────────────────────────────────────────────────

def _session(db, user):
    from src.auth import create_token_response
    return _headers(create_token_response(user, db, mfa=True).access_token)


def test_regenerating_recovery_codes_needs_the_password(client, db):
    from src import mfa
    u, secret, _ = _enrolled(db, role="hr")
    h = _session(db, u)
    assert client.post("/api/v1/auth/mfa/recovery-codes", headers=h,
                       json={"code": mfa.totp(secret)}).status_code == 422
    assert client.post("/api/v1/auth/mfa/recovery-codes", headers=h,
                       json={"password": "Wrong@1234", "code": mfa.totp(secret)}).status_code == 400
    ok = client.post("/api/v1/auth/mfa/recovery-codes", headers=h,
                     json={"password": PW, "code": mfa.totp(secret)})
    assert ok.status_code == 200 and len(ok.json()["recovery_codes"]) == 10


def test_wrong_password_at_mfa_disable_counts_toward_the_lockout(client, db):
    from src import mfa
    u, secret, _ = _enrolled(db, role="hr")
    h = _session(db, u)
    for _ in range(3):
        r = client.post("/api/v1/auth/mfa/disable", headers=h,
                        json={"password": "Wrong@1234", "code": mfa.totp(secret)})
        assert r.status_code == 400
    db.refresh(u)
    assert u.failed_login_count == 3


def test_account_locked_by_mfa_guesses_refuses_the_change(client, db, monkeypatch):
    from src import mfa
    from datetime import datetime, timedelta
    u, secret, _ = _enrolled(db, role="hr")
    h = _session(db, u)
    u.locked_until = datetime.utcnow() + timedelta(minutes=10)
    db.flush()
    r = client.post("/api/v1/auth/mfa/disable", headers=h, json={"password": PW, "code": mfa.totp(secret)})
    assert r.status_code != 200
    db.refresh(u)
    assert u.mfa_enabled, "a locked account turned MFA off"


def test_recovery_codes_are_keyed_and_old_ones_still_work():
    from src import mfa
    codes, stored = mfa.generate_recovery_codes()
    digests = json.loads(stored)
    plain = hashlib.sha256(codes[0].replace("-", "").encode()).hexdigest()
    assert all(d.startswith("h1:") for d in digests) and plain not in stored
    ok, remaining = mfa.consume_recovery_code(stored, codes[0])
    assert ok and len(json.loads(remaining)) == 9

    legacy = json.dumps([plain])                       # stored before the change
    ok, remaining = mfa.consume_recovery_code(legacy, codes[0])
    assert ok and json.loads(remaining) == []


def test_undecryptable_mfa_secret_is_logged(client, db, caplog):
    u, _, _ = _enrolled(db, role="hr")
    u.mfa_secret = "gAAAAA-not-a-valid-fernet-token"
    db.flush()
    tok = _login(client, u.email).json()["mfa_token"]
    with caplog.at_level(logging.ERROR):
        r = client.post("/api/v1/auth/login/mfa", json={"mfa_token": tok, "code": "123456"})
    assert r.status_code == 400
    assert any("cannot be decrypted" in rec.getMessage() for rec in caplog.records)


def test_admin_can_reset_a_colleagues_mfa(client, db, admin_headers):
    from src.models.models import AuditLog, User
    admin = db.query(User).filter(User.email == "admin_test@test.com").first()
    u, _, _ = _enrolled(db, role="hr")
    u.company_id = admin.company_id
    db.flush()
    before = u.token_version or 0

    listed = client.get("/api/v1/admin/users", headers=admin_headers).json()["users"]
    assert next(x for x in listed if x["id"] == u.id)["mfa_enabled"] is True

    r = client.post(f"/api/v1/admin/users/{u.id}/mfa/reset", headers=admin_headers)
    assert r.status_code == 200, r.text
    db.refresh(u)
    assert not u.mfa_enabled and u.mfa_secret is None and u.mfa_recovery_codes is None
    assert (u.token_version or 0) > before, "the reset must sign the user out"
    assert db.query(AuditLog).filter(AuditLog.action == "admin.mfa_reset",
                                     AuditLog.resource == f"users/{u.id}").count() == 1
    # Nothing left to reset.
    assert client.post(f"/api/v1/admin/users/{u.id}/mfa/reset", headers=admin_headers).status_code == 400


def test_mfa_reset_is_refused_for_self_other_companies_and_non_admins(client, db, admin_headers):
    from src.models.models import User
    admin = db.query(User).filter(User.email == "admin_test@test.com").first()
    assert client.post(f"/api/v1/admin/users/{admin.id}/mfa/reset",
                       headers=admin_headers).status_code == 400
    elsewhere, _, _ = _enrolled(db, role="hr")               # another company
    assert client.post(f"/api/v1/admin/users/{elsewhere.id}/mfa/reset",
                       headers=admin_headers).status_code == 404
    hr = _user(db, "hr", admin.company_id)
    target, _, _ = _enrolled(db, role="interviewer")
    target.company_id = admin.company_id
    db.flush()
    hr_h = _headers(_login(client, hr.email).json()["access_token"])
    assert client.post(f"/api/v1/admin/users/{target.id}/mfa/reset", headers=hr_h).status_code == 403
