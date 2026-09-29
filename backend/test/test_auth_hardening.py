"""
Auth hardening regression tests.

Each test pins one fix and fails against the code before it:

* OTP        — hashed at rest, single purpose, voided after 5 wrong guesses,
               NULL expiry is expired, never an arbitrary same-email row.
* Limits     — the rate-limit key ignores a client-supplied X-Forwarded-For;
               per-account lockout after repeated wrong passwords.
* Sessions   — logout, password change/reset, deactivation and company suspension
               revoke existing tokens; refresh stops at the absolute session age.
* Passwords  — one policy on every path that sets a password.
* MFA        — TOTP (RFC 6238 vectors), forced enrolment for admin roles, the
               second sign-in step, replay and recovery codes.
* Superadmin — every request audited, append-only log, client people data only
               under a company's support-access grant, raw SQL console off.
"""
import time
import uuid
from datetime import datetime, timedelta

import pytest

PW = "Pass@1234"


# ── helpers ──────────────────────────────────────────────────────────────────

def _company(db, name="Co", active=True):
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}",
                is_active=active)
    db.add(c)
    db.flush()
    return c


def _user(db, role, company_id, email=None, password=PW, **kw):
    from src.models.models import User
    from src.auth import get_password_hash
    fields = dict(first_name=role.title(), last_name="T", is_active=True, email_verified=True)
    fields.update(kw)
    u = User(id=str(uuid.uuid4()), company_id=company_id,
             email=email or f"{role}_{uuid.uuid4().hex[:6]}@t.com",
             password_hash=get_password_hash(password), role=role, **fields)
    db.add(u)
    db.flush()
    return u


def _login(client, email, password=PW, **extra):
    return client.post("/api/v1/auth/login", json={"email": email, "password": password, **extra})


def _headers(token):
    return {"Authorization": f"Bearer {token}"}


def _platform(db):
    from src.tenancy.platform_company import ensure_platform_company
    return ensure_platform_company(db)


def _superadmin(client, db):
    sa = _user(db, "superadmin", _platform(db).id)
    r = _login(client, sa.email)
    assert r.status_code == 200, r.text
    return sa, _headers(r.json()["access_token"])


def _set_otp(db, user, code, purpose, expires_in=10):
    from src.auth import _otp_digest
    user.otp_hash = _otp_digest(user, purpose, code)
    user.otp_purpose = purpose
    user.otp_attempts = 0
    user.otp_expires_at = (datetime.utcnow() + timedelta(minutes=expires_in)
                           if expires_in is not None else None)
    db.flush()


@pytest.fixture
def mfa_enforced(monkeypatch):
    monkeypatch.setenv("MFA_REQUIRED_ROLES", "superadmin,admin")


# ── OTP ──────────────────────────────────────────────────────────────────────

def test_otp_is_stored_only_as_a_hash(client, db):
    from src.models.models import User
    email = f"reg_{uuid.uuid4().hex[:6]}@t.com"
    r = client.post("/api/v1/auth/register", json={
        "email": email, "password": PW, "role": "candidate",
        "first_name": "A", "last_name": "B"})
    assert r.status_code == 201, r.text
    user = db.query(User).filter(User.email == email).first()
    assert user.otp_code is None
    assert len(user.otp_hash) == 64 and user.otp_purpose == "verify"


def test_generate_otp_uses_the_os_csprng(monkeypatch):
    import random
    import src.auth as auth
    monkeypatch.setattr(random, "choices", lambda *a, **k: list("000000"))
    codes = {auth.generate_otp() for _ in range(20)}
    assert len(codes) > 1 and all(len(c) == 6 and c.isdigit() for c in codes)


def test_otp_is_voided_after_five_wrong_guesses(client, db):
    co = _company(db)
    u = _user(db, "candidate", co.id, email_verified=False)
    _set_otp(db, u, "123456", "verify")
    for _ in range(5):
        r = client.post("/api/v1/auth/verify-otp", json={
            "email": u.email, "otp": "000000", "company_slug": co.slug})
        assert r.status_code == 400
    r = client.post("/api/v1/auth/verify-otp", json={
        "email": u.email, "otp": "123456", "company_slug": co.slug})
    assert r.status_code == 400, "the right code must not work after 5 wrong guesses"


def test_signup_code_cannot_reset_the_password(client, db):
    co = _company(db)
    u = _user(db, "hr", co.id)
    _set_otp(db, u, "123456", "verify")
    r = client.post("/api/v1/auth/reset-password", json={
        "email": u.email, "otp": "123456", "new_password": "NewPass@9999",
        "company_slug": co.slug})
    assert r.status_code == 400


def test_otp_with_no_expiry_is_expired(client, db):
    co = _company(db)
    u = _user(db, "candidate", co.id, email_verified=False)
    _set_otp(db, u, "123456", "verify", expires_in=None)
    r = client.post("/api/v1/auth/verify-otp", json={
        "email": u.email, "otp": "123456", "company_slug": co.slug})
    assert r.status_code == 400


def test_reset_without_company_never_picks_an_arbitrary_same_email_row(client, db):
    email = f"dup_{uuid.uuid4().hex[:6]}@t.com"
    a = _user(db, "candidate", _company(db).id, email=email)
    b = _user(db, "candidate", _company(db).id, email=email)
    r = client.post("/api/v1/auth/forgot-password", json={"email": email})
    assert r.status_code == 200
    db.refresh(a)
    db.refresh(b)
    assert a.otp_hash is None and b.otp_hash is None


def test_superadmin_cannot_be_reset_through_a_company_portal(client, db):
    email = f"boss_{uuid.uuid4().hex[:6]}@t.com"
    sa = _user(db, "superadmin", _platform(db).id, email=email)
    client_co = _company(db)
    client.post("/api/v1/auth/forgot-password", json={"email": email, "company_slug": client_co.slug})
    db.refresh(sa)
    assert sa.otp_hash is None


# ── rate limiting & lockout ─────────────────────────────────────────────────

def _request(peer, xff=None):
    from starlette.requests import Request
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    return Request({"type": "http", "headers": headers, "client": (peer, 1234),
                    "method": "GET", "path": "/"})


def test_rate_limit_key_ignores_client_supplied_forwarded_for():
    from src.rate_limit import client_ip_key
    # Behind our Apache (127.0.0.1): the client wrote "6.6.6.6", Apache appended the
    # real address. The real one is the right-most untrusted hop.
    assert client_ip_key(_request("127.0.0.1", "6.6.6.6, 203.0.113.9")) == "203.0.113.9"
    # A direct (unproxied) caller cannot pick its bucket at all.
    assert client_ip_key(_request("198.51.100.4", "6.6.6.6")) == "198.51.100.4"
    assert client_ip_key(_request("198.51.100.4")) == "198.51.100.4"


def test_refresh_and_mfa_step_are_rate_limited():
    from src.rate_limit import limiter
    limited = set(limiter._route_limits)
    assert "routes.auth_routes.refresh_token" in limited
    assert "routes.auth_routes.login_mfa" in limited


def test_account_locks_after_repeated_wrong_passwords(client, db):
    from src.auth import LOGIN_LOCKOUT_THRESHOLD
    u = _user(db, "hr", _company(db).id)
    for _ in range(LOGIN_LOCKOUT_THRESHOLD):
        assert _login(client, u.email, "Wrong@Pass1").status_code == 401
    r = _login(client, u.email)
    wrong = _login(client, u.email, "Wrong@Pass1")
    assert r.status_code == 401, "the right password must not work while locked"
    # The same reply as a wrong password: a distinct "locked" answer would tell anyone
    # who tries eleven passwords whether the email has an account.
    assert r.json() == wrong.json()


def test_login_does_not_reveal_an_account_at_another_company(client, db):
    email = f"elsewhere_{uuid.uuid4().hex[:6]}@t.com"
    _user(db, "candidate", _company(db).id, email=email)
    here = _company(db)
    r = _login(client, email, company_slug=here.slug)
    nobody = _login(client, "nobody@nowhere.com", company_slug=here.slug)
    assert r.status_code == nobody.status_code == 401
    assert r.json() == nobody.json()


# ── sessions ─────────────────────────────────────────────────────────────────

def test_logout_revokes_the_token(client, db):
    u = _user(db, "hr", _company(db).id)
    h = _headers(_login(client, u.email).json()["access_token"])
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=h).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_password_change_signs_out_other_sessions(client, db):
    u = _user(db, "hr", _company(db).id)
    first = _headers(_login(client, u.email).json()["access_token"])
    other = _headers(_login(client, u.email).json()["access_token"])
    r = client.post("/api/v1/auth/change-password", headers=first,
                    json={"current_password": PW, "new_password": "BrandNew@123"})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/me", headers=other).status_code == 401
    fresh = _headers(r.json()["access_token"])
    assert client.get("/api/v1/auth/me", headers=fresh).status_code == 200


def test_password_reset_signs_out_every_session(client, db):
    co = _company(db)
    u = _user(db, "hr", co.id)
    h = _headers(_login(client, u.email).json()["access_token"])
    _set_otp(db, u, "123456", "reset")
    r = client.post("/api/v1/auth/reset-password", json={
        "email": u.email, "otp": "123456", "new_password": "NewPass@9999",
        "company_slug": co.slug})
    assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_deactivation_revokes_tokens_even_after_reactivation(client, db, admin_headers):
    from src.models.models import User
    admin = db.query(User).filter(User.email == "admin_test@test.com").first()
    hr = _user(db, "hr", admin.company_id)
    h = _headers(_login(client, hr.email).json()["access_token"])
    for active in ("false", "true"):
        r = client.put(f"/api/v1/admin/users/{hr.id}/status?is_active={active}",
                       headers=admin_headers)
        assert r.status_code == 200, r.text
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_company_suspension_ends_existing_sessions(client, db):
    co = _company(db)
    u = _user(db, "hr", co.id)
    h = _headers(_login(client, u.email).json()["access_token"])
    _, sa_h = _superadmin(client, db)
    r = client.put(f"/api/v1/superadmin/companies/{co.id}/toggle", headers=sa_h)
    assert r.status_code == 200 and r.json()["is_active"] is False
    assert client.get("/api/v1/auth/me", headers=h).status_code == 401


def test_refresh_is_refused_past_the_absolute_session_age(client, db):
    """A still-unexpired token whose sign-in is older than the ceiling (e.g. one minted
    with a long lifetime before this change) cannot be refreshed into a new session."""
    from src.auth import create_access_token, SESSION_MAX_HOURS
    u = _user(db, "hr", _company(db).id)
    signed_in = int(time.time() - SESSION_MAX_HOURS * 3600 - 60)
    token = create_access_token({"sub": u.id, "role": "hr", "type": "access", "tv": 0,
                                 "auth_time": signed_in}, expires_delta=timedelta(hours=1))
    assert client.get("/api/v1/auth/me", headers=_headers(token)).status_code == 200
    r = client.post("/api/v1/auth/refresh", headers=_headers(token))
    assert r.status_code == 401


def test_access_token_never_outlives_the_session_ceiling(db):
    from jose import jwt
    from src.auth import create_token_response, SECRET_KEY, ALGORITHM, SESSION_MAX_HOURS
    u = _user(db, "hr", _company(db).id)
    auth_time = int(time.time() - SESSION_MAX_HOURS * 3600 + 300)   # 5 minutes left
    token = create_token_response(u, db, auth_time=auth_time).access_token
    exp = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])["exp"]
    assert exp <= auth_time + SESSION_MAX_HOURS * 3600 + 1


def test_mfa_step_token_opens_no_route(client, db):
    from src.auth import create_mfa_token
    u = _user(db, "hr", _company(db).id)
    for kind in ("mfa", "mfa_enroll"):
        r = client.get("/api/v1/auth/me", headers=_headers(create_mfa_token(u, kind)))
        assert r.status_code == 401


# ── password policy ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("weak", ["short1!", "alllowercaseletters", "12345678", "password1"])
def test_registration_enforces_the_password_policy(client, weak):
    r = client.post("/api/v1/auth/register", json={
        "email": f"weak_{uuid.uuid4().hex[:6]}@t.com", "password": weak,
        "role": "candidate", "first_name": "A", "last_name": "B"})
    assert r.status_code == 400, r.text


def test_admin_cannot_create_a_user_with_a_weak_password(client, admin_headers):
    r = client.post("/api/v1/admin/users/hr", headers=admin_headers, json={
        "email": f"hrweak_{uuid.uuid4().hex[:6]}@t.com", "password": "password1",
        "first_name": "H", "last_name": "R"})
    assert r.status_code == 400, r.text


def test_superadmin_cannot_seed_a_company_admin_with_a_weak_password(client, db):
    _, sa_h = _superadmin(client, db)
    r = client.post("/api/v1/superadmin/companies", headers=sa_h, json={
        "company_name": "Weak Co", "slug": f"weak-{uuid.uuid4().hex[:6]}",
        "admin_email": f"a_{uuid.uuid4().hex[:6]}@t.com", "admin_password": "12345678",
        "admin_first_name": "A", "admin_last_name": "B"})
    assert r.status_code == 400, r.text


# ── MFA ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("t,expected", [
    (59, "94287082"), (1111111109, "07081804"), (1111111111, "14050471"),
    (1234567890, "89005924"), (2000000000, "69279037"),
])
def test_totp_matches_rfc6238_test_vectors(t, expected):
    import base64
    from src import mfa
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert mfa.hotp(secret, int(t // 30), digits=8) == expected


def test_admin_is_forced_through_enrolment(client, db, mfa_enforced):
    from src import mfa
    admin = _user(db, "admin", _company(db).id)
    r = _login(client, admin.email)
    body = r.json()
    assert r.status_code == 200 and body.get("mfa_enrollment_required") is True
    assert "access_token" not in body

    start = client.post("/api/v1/auth/mfa/enroll/start", json={"mfa_token": body["mfa_token"]})
    assert start.status_code == 200, start.text
    code = mfa.totp(start.json()["secret"])
    done = client.post("/api/v1/auth/mfa/enroll/confirm",
                       json={"mfa_token": body["mfa_token"], "code": code})
    assert done.status_code == 200, done.text
    assert len(done.json()["recovery_codes"]) == 10
    h = _headers(done.json()["access_token"])
    assert client.get("/api/v1/auth/me", headers=h).status_code == 200
    # The enrolment token is spent: confirming bumped token_version.
    again = client.post("/api/v1/auth/mfa/enroll/start", json={"mfa_token": body["mfa_token"]})
    assert again.status_code == 400


def _enrolled(db, role="admin"):
    from src import mfa
    from src.security import encrypt
    secret = mfa.generate_secret()
    codes, stored = mfa.generate_recovery_codes()
    u = _user(db, role, _company(db).id, mfa_enabled=True, mfa_secret=encrypt(secret),
              mfa_recovery_codes=stored)
    return u, secret, codes


def test_mfa_second_step_and_replay_guard(client, db):
    from src import mfa
    u, secret, _ = _enrolled(db, role="hr")
    first = _login(client, u.email).json()
    assert first.get("mfa_required") is True and "access_token" not in first
    bad = client.post("/api/v1/auth/login/mfa", json={"mfa_token": first["mfa_token"], "code": "000000"})
    assert bad.status_code == 400
    code = mfa.totp(secret)
    ok = client.post("/api/v1/auth/login/mfa", json={"mfa_token": first["mfa_token"], "code": code})
    assert ok.status_code == 200, ok.text
    second = _login(client, u.email).json()
    replay = client.post("/api/v1/auth/login/mfa", json={"mfa_token": second["mfa_token"], "code": code})
    assert replay.status_code == 400, "a TOTP code must not be accepted twice"


def test_recovery_code_works_once(client, db):
    u, _, codes = _enrolled(db, role="hr")
    tok = _login(client, u.email).json()["mfa_token"]
    assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": tok, "code": codes[0]}).status_code == 200
    tok = _login(client, u.email).json()["mfa_token"]
    assert client.post("/api/v1/auth/login/mfa", json={"mfa_token": tok, "code": codes[0]}).status_code == 400


def test_token_without_mfa_is_refused_once_mfa_is_required(client, db, mfa_enforced):
    from src.auth import create_token_response
    admin = _user(db, "admin", _company(db).id)
    legacy = create_token_response(admin, db).access_token      # no `mfa` claim
    assert client.get("/api/v1/auth/me", headers=_headers(legacy)).status_code == 401


def test_required_role_cannot_turn_mfa_off(client, db, mfa_enforced):
    from src import mfa
    from src.auth import create_token_response
    admin, secret, _ = _enrolled(db, role="admin")
    h = _headers(create_token_response(admin, db, mfa=True).access_token)
    r = client.post("/api/v1/auth/mfa/disable", headers=h,
                    json={"password": PW, "code": mfa.totp(secret)})
    assert r.status_code == 400


# ── superadmin: audit & support access ──────────────────────────────────────

def test_every_superadmin_request_is_audited(client, db):
    from src.models.models import AuditLog
    sa, h = _superadmin(client, db)
    assert client.get("/api/v1/superadmin/companies", headers=h).status_code == 200
    row = db.query(AuditLog).filter(AuditLog.actor_user_id == sa.id).first()
    assert row is not None and row.action == "superadmin.request"
    assert row.path == "/api/v1/superadmin/companies"


def test_audit_log_is_append_only(db):
    from src.services.audit_log import record_audit
    from src.models.models import AuditLog
    record_audit(db, action="test.event", resource="x")
    row = db.query(AuditLog).filter(AuditLog.action == "test.event").first()
    row.resource = "tampered"
    with pytest.raises(RuntimeError):
        db.flush()
    db.rollback()


def test_superadmin_sees_client_people_only_under_a_grant(client, db):
    from src.models.models import AuditLog
    co = _company(db)
    admin = _user(db, "admin", co.id)
    hr = _user(db, "hr", co.id)
    _, sa_h = _superadmin(client, db)

    listed = {u["id"] for u in client.get(f"/api/v1/superadmin/companies/{co.id}", headers=sa_h).json()["users"]}
    assert admin.id in listed and hr.id not in listed

    admin_h = _headers(_login(client, admin.email).json()["access_token"])
    g = client.post("/api/v1/support/access-grants", headers=admin_h,
                    json={"reason": "Ticket 42", "hours": 2})
    assert g.status_code == 201, g.text
    body = client.get(f"/api/v1/superadmin/companies/{co.id}", headers=sa_h).json()
    assert hr.id in {u["id"] for u in body["users"]} and body["support_access"]["active"]

    assert client.delete(f"/api/v1/support/access-grants/{g.json()['id']}", headers=admin_h).status_code == 200
    listed = {u["id"] for u in client.get(f"/api/v1/superadmin/companies/{co.id}", headers=sa_h).json()["users"]}
    assert hr.id not in listed

    log = client.get("/api/v1/support/access-log", headers=admin_h).json()["entries"]
    actions = {e["action"] for e in log}
    assert {"support_access.granted", "support_access.revoked", "superadmin.read"} <= actions
    assert db.query(AuditLog).filter(AuditLog.company_id == co.id).count() >= 3


def test_superadmin_interview_read_needs_a_grant(client, db):
    from src.models.models import Interview
    co = _company(db)
    admin = _user(db, "admin", co.id)
    iv = Interview(company_id=co.id, title="T", status="DRAFT")
    db.add(iv)
    db.flush()
    _, sa_h = _superadmin(client, db)
    url = f"/api/v1/mcq/interviews/{iv.id}/questions"
    assert client.get(url, headers=sa_h).status_code == 403
    admin_h = _headers(_login(client, admin.email).json()["access_token"])
    client.post("/api/v1/support/access-grants", headers=admin_h, json={"reason": "debug test"})
    assert client.get(url, headers=sa_h).status_code == 200


def test_company_admin_cannot_read_another_companys_grants_or_log(client, db):
    a, b = _company(db), _company(db)
    a_admin, b_admin = _user(db, "admin", a.id), _user(db, "admin", b.id)
    a_h = _headers(_login(client, a_admin.email).json()["access_token"])
    b_h = _headers(_login(client, b_admin.email).json()["access_token"])
    g = client.post("/api/v1/support/access-grants", headers=a_h, json={"reason": "mine only"}).json()
    assert client.get("/api/v1/support/access-grants", headers=b_h).json() == []
    assert client.delete(f"/api/v1/support/access-grants/{g['id']}", headers=b_h).status_code == 404


# ── database console ────────────────────────────────────────────────────────

def test_raw_sql_console_is_off_by_default(client, db, monkeypatch):
    monkeypatch.delenv("ENABLE_RAW_SQL_CONSOLE", raising=False)
    _, h = _superadmin(client, db)
    r = client.post("/api/v1/database/query", headers=h, json={"query": "SELECT 1"})
    assert r.status_code == 404


@pytest.mark.parametrize("query", [
    "SELECT password_hash FROM users",
    "SELECT * FROM users",
    "SELECT email FROM users INTO OUTFILE '/tmp/x'",
    "SELECT LOAD_FILE('/etc/passwd')",
    "SELECT SLEEP(10)",
    "SELECT 1; DROP TABLE users",
    "SELECT api_key FROM companies",
])
def test_console_rejects_dangerous_queries(query):
    from fastapi import HTTPException
    from routes.database_management_routes import check_console_query
    with pytest.raises(HTTPException):
        check_console_query(query)


def test_console_allows_a_plain_read():
    from routes.database_management_routes import check_console_query
    check_console_query("SELECT COUNT(*) FROM users WHERE role = 'hr'")


def test_backup_path_cannot_be_chosen_by_the_client(client, db):
    _, h = _superadmin(client, db)
    r = client.post("/api/v1/database/backup", headers=h, json={"backup_path": "/var/www/html/dump.sql"})
    assert r.status_code == 422


# ── fixes from the security review ───────────────────────────────────────────

def test_correct_password_does_not_reset_the_mfa_code_counter(client, db):
    """Phished password + guessed codes: re-entering the password between batches of
    wrong codes used to zero the counter, so the lockout never fired."""
    from src.auth import LOGIN_LOCKOUT_THRESHOLD
    u, _, _ = _enrolled(db, role="hr")
    for _ in range(LOGIN_LOCKOUT_THRESHOLD):
        tok = _login(client, u.email).json()["mfa_token"]
        client.post("/api/v1/auth/login/mfa", json={"mfa_token": tok, "code": "000000"})
    assert _login(client, u.email).status_code == 401, "locked after the threshold"


def test_reissued_codes_share_one_failure_budget(db):
    """Requesting a fresh code must not buy fresh guesses."""
    from src.auth import (OTP_MAX_RECENT_FAILURES, OTP_MAX_ATTEMPTS, set_user_otp,
                          check_user_otp)
    u = _user(db, "candidate", _company(db).id)
    for _ in range(OTP_MAX_RECENT_FAILURES // OTP_MAX_ATTEMPTS):
        set_user_otp(u, db, purpose="reset")
        for _ in range(OTP_MAX_ATTEMPTS):
            assert not check_user_otp(u, db, "not-it", "reset")
    code = set_user_otp(u, db, purpose="reset")
    assert not check_user_otp(u, db, code, "reset"), "budget spent: even the right code fails"

    u.otp_failures_since = datetime.utcnow() - timedelta(hours=2)
    db.flush()
    assert check_user_otp(u, db, code, "reset"), "the budget renews after the window"


def test_token_minted_before_the_deploy_is_refused(client, db):
    """Pre-deploy tokens carry no `tv`; the migration starts everyone at 1."""
    from jose import jwt
    from src.auth import SECRET_KEY, ALGORITHM
    u = _user(db, "hr", _company(db).id, token_version=1)
    old = jwt.encode({"sub": u.id, "exp": datetime.utcnow() + timedelta(hours=1)},
                     SECRET_KEY, algorithm=ALGORITHM)
    assert client.get("/api/v1/auth/me", headers=_headers(old)).status_code == 401


def test_judge_token_routes_refuse_revoked_and_mfa_step_tokens(client, db, monkeypatch):
    """/judge/reap decoded the JWT itself and skipped revocation and the MFA step."""
    import src.judge.provisioner as provisioner
    monkeypatch.setattr(provisioner, "reap_if_idle", lambda _db: {"stopped": False})
    admin = _user(db, "admin", _company(db).id)
    live = _headers(_login(client, admin.email).json()["access_token"])
    assert client.post("/api/v1/judge/reap", headers=live).status_code == 200
    client.post("/api/v1/auth/logout", headers=live)
    assert client.post("/api/v1/judge/reap", headers=live).status_code == 401

    u, _, _ = _enrolled(db, role="hr")
    step = _login(client, u.email).json()["mfa_token"]
    assert client.post("/api/v1/judge/reap", headers=_headers(step)).status_code == 401
