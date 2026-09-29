"""
API tests for the shared "My Profile" endpoints:
  GET /api/v1/auth/me             (reshaped — now carries company + role profile)
  GET /api/v1/auth/my-permissions (new)

Both are role-agnostic: every authenticated role must reach them.
"""

import uuid

import pytest


EXPECTED_TOTAL = 136
EXPECTED_GRANTS = {
    "superadmin": 25,
    "admin": 102,
    "hr": 78,
    "interviewer": 19,
    "candidate": 18,
}

# /auth/me is built field-by-field precisely so that credential columns on the
# same model can never leak. Assert the whole key set, not a spot check — a
# future refactor to `vars(user)` or a permissive schema would pass any
# "password_hash not in body" check that only looked for one name.
EXPECTED_ME_KEYS = {
    "id", "email", "role", "first_name", "last_name", "phone",
    "gender", "date_of_birth", "is_active", "email_verified",
    "created_at", "updated_at", "company_id", "company_name", "profile",
    "mfa_enabled",
}
FORBIDDEN_ME_KEYS = {
    "password_hash", "otp_code", "otp_expires_at", "otp_hash", "otp_purpose",
    "otp_attempts", "token_version", "mfa_secret", "mfa_recovery_codes",
    "mfa_last_step", "failed_login_count", "locked_until",
}


def _make_superadmin_headers(client, db):
    """No superadmin_headers fixture exists in conftest — mirror the helper in
    test_superadmin_onboarding.py."""
    from src.models.models import Company, User
    from src.auth import get_password_hash
    from routes.superadmin_routes import PLATFORM_SLUG

    company = db.query(Company).filter(Company.slug == PLATFORM_SLUG).first()
    if not company:
        company = Company(id=str(uuid.uuid4()), name="HireFlow Platform", slug=PLATFORM_SLUG)
        db.add(company)
        db.flush()

    email = f"sa_{uuid.uuid4().hex[:6]}@hireflow.com"
    db.add(User(
        id=str(uuid.uuid4()), company_id=company.id, email=email,
        password_hash=get_password_hash("Pass@1234"), role="superadmin",
        first_name="Super", last_name="Admin", is_active=True, email_verified=True,
    ))
    db.flush()

    resp = client.post("/api/v1/auth/login", json={"email": email, "password": "Pass@1234"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


# ── /auth/my-permissions ──────────────────────────────────────────────────────

def test_my_permissions_requires_authentication(client):
    resp = client.get("/api/v1/auth/my-permissions")
    assert resp.status_code in (401, 403)


@pytest.mark.parametrize("fixture_name,role", [
    ("admin_headers", "admin"),
    ("hr_headers", "hr"),
    ("interviewer_headers", "interviewer"),
    ("candidate_headers", "candidate"),
])
def test_my_permissions_per_role(client, request, fixture_name, role):
    headers = request.getfixturevalue(fixture_name)
    resp = client.get("/api/v1/auth/my-permissions", headers=headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["role"] == role
    assert body["known_role"] is True
    assert body["total"] == EXPECTED_TOTAL
    assert body["granted_count"] == EXPECTED_GRANTS[role]
    assert body["group_count"] == 18
    assert len(body["groups"]) == 18


def test_my_permissions_for_superadmin(client, db):
    headers = _make_superadmin_headers(client, db)
    resp = client.get("/api/v1/auth/my-permissions", headers=headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["role"] == "superadmin"
    assert body["granted_count"] == EXPECTED_GRANTS["superadmin"]
    # Superadmin is a platform-operations role, not a recruiting one. All 18
    # groups still come back so the UI can show the 14 empty ones explicitly.
    assert body["group_count"] == 18
    assert body["granted_group_count"] == 4


def test_my_permissions_never_leaks_generator_exhaust(client, hr_headers):
    body = client.get("/api/v1/auth/my-permissions", headers=hr_headers).json()
    for group in body["groups"]:
        for perm in group["permissions"]:
            assert set(perm) <= {"slug", "label", "granted", "endpoints", "conflict", "current_gate"}
            assert "description" not in perm


def test_my_permissions_marks_conflicting_rows(client, hr_headers):
    body = client.get("/api/v1/auth/my-permissions", headers=hr_headers).json()
    conflicted = [
        p for g in body["groups"] for p in g["permissions"] if p.get("conflict")
    ]
    assert len(conflicted) == 15
    assert all(p["current_gate"] for p in conflicted)


def test_my_permissions_group_counts_match_rows(client, admin_headers):
    body = client.get("/api/v1/auth/my-permissions", headers=admin_headers).json()
    assert sum(g["granted_count"] for g in body["groups"]) == body["granted_count"]
    assert sum(g["total"] for g in body["groups"]) == body["total"]


# ── /auth/me ──────────────────────────────────────────────────────────────────

def test_me_requires_authentication(client):
    resp = client.get("/api/v1/auth/me")
    assert resp.status_code in (401, 403)


@pytest.mark.parametrize("fixture_name,role", [
    ("admin_headers", "admin"),
    ("hr_headers", "hr"),
    ("interviewer_headers", "interviewer"),
    ("candidate_headers", "candidate"),
])
def test_me_shape_and_no_credential_leak(client, request, fixture_name, role):
    headers = request.getfixturevalue(fixture_name)
    resp = client.get("/api/v1/auth/me", headers=headers)
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert set(body) == EXPECTED_ME_KEYS
    assert not (set(body) & FORBIDDEN_ME_KEYS)
    assert body["role"] == role
    assert body["email"]


def test_me_includes_company_name_for_hr(client, hr_headers):
    body = client.get("/api/v1/auth/me", headers=hr_headers).json()
    assert body["company_id"]
    assert body["company_name"] == "Test Company"


def test_me_returns_hr_profile_after_it_exists(client, db, hr_headers):
    """HRProfile is auto-created lazily by the HR routes, so /auth/me returns
    null until something creates it — then it must populate."""
    from src.models.models import HRProfile, User, Department

    user = db.query(User).filter(User.email == "hr_test@test.com").first()
    department = db.query(Department).filter(Department.company_id == user.company_id).first()
    db.add(HRProfile(
        id=str(uuid.uuid4()), company_id=user.company_id, user_id=user.id,
        department_id=department.id if department else None, employee_id="HR-TEST01",
    ))
    db.flush()

    body = client.get("/api/v1/auth/me", headers=hr_headers).json()
    assert body["profile"] is not None
    assert body["profile"]["kind"] == "hr"
    assert body["profile"]["employee_id"] == "HR-TEST01"
    assert body["profile"]["department_name"] == "Engineering"


def test_me_parses_interviewer_expertise_as_a_list(client, db, interviewer_headers):
    from src.models.models import InterviewerProfile, User

    user = db.query(User).filter(User.email == "interviewer_test@test.com").first()
    db.add(InterviewerProfile(
        id=str(uuid.uuid4()), company_id=user.company_id, user_id=user.id,
        employee_id="IV-TEST01", expertise_areas='["Python", "System Design"]',
        years_experience=7,
    ))
    db.flush()

    body = client.get("/api/v1/auth/me", headers=interviewer_headers).json()
    assert body["profile"]["kind"] == "interviewer"
    assert body["profile"]["expertise_areas"] == ["Python", "System Design"]
    assert body["profile"]["years_experience"] == 7


def test_me_survives_malformed_expertise_json(client, db, interviewer_headers):
    """Legacy rows hold comma-separated text rather than JSON — that must
    degrade to a list, not a 500."""
    from src.models.models import InterviewerProfile, User

    user = db.query(User).filter(User.email == "interviewer_test@test.com").first()
    db.add(InterviewerProfile(
        id=str(uuid.uuid4()), company_id=user.company_id, user_id=user.id,
        employee_id="IV-TEST02", expertise_areas="Python, Django",
    ))
    db.flush()

    resp = client.get("/api/v1/auth/me", headers=interviewer_headers)
    assert resp.status_code == 200
    assert resp.json()["profile"]["expertise_areas"] == ["Python", "Django"]


def test_me_does_not_leak_another_tenants_profile_to_superadmin(client, db):
    """
    The tenant trap.

    src/auth.py binds no company for a superadmin, so the tenancy auto-filter
    is a no-op for them. A profile lookup written as db.query(HRProfile).first()
    would hand a superadmin an arbitrary HR profile from an arbitrary company.
    In dev there is usually only one profile row, which is exactly why this
    bug is invisible without a test that seeds a second tenant.
    """
    from src.models.models import Company, Department, HRProfile, User
    from src.auth import get_password_hash

    other = Company(id=str(uuid.uuid4()), name="Unrelated Co", slug=f"other-{uuid.uuid4().hex[:8]}")
    db.add(other)
    db.flush()
    dept = Department(id=str(uuid.uuid4()), company_id=other.id, name="Recruiting")
    db.add(dept)
    db.flush()
    other_hr = User(
        id=str(uuid.uuid4()), company_id=other.id, email=f"hr_{uuid.uuid4().hex[:6]}@other.com",
        password_hash=get_password_hash("Pass@1234"), role="hr",
        first_name="Other", last_name="HR", is_active=True, email_verified=True,
    )
    db.add(other_hr)
    db.flush()
    db.add(HRProfile(
        id=str(uuid.uuid4()), company_id=other.id, user_id=other_hr.id,
        department_id=dept.id, employee_id="HR-OTHER",
    ))
    db.flush()

    headers = _make_superadmin_headers(client, db)
    body = client.get("/api/v1/auth/me", headers=headers).json()

    assert body["role"] == "superadmin"
    assert body["profile"] is None, "superadmin picked up an unrelated tenant's HR profile"


# ── PUT /auth/me ──────────────────────────────────────────────────────────────

def test_update_me_changes_name_and_phone(client, hr_headers):
    resp = client.put("/api/v1/auth/me", headers=hr_headers,
                      json={"first_name": "Renamed", "last_name": "Person", "phone": "+91 98765 43210"})
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert set(body) == EXPECTED_ME_KEYS, "PUT must return the same shape as GET"
    assert not (set(body) & FORBIDDEN_ME_KEYS)
    assert body["first_name"] == "Renamed"
    assert body["last_name"] == "Person"
    assert body["phone"] == "+91 98765 43210"

    assert client.get("/api/v1/auth/me", headers=hr_headers).json()["first_name"] == "Renamed"


@pytest.mark.parametrize("field,evil", [
    ("role", "admin"),
    ("email", "attacker@evil.com"),
    ("is_active", False),
    ("company_id", "00000000-0000-0000-0000-000000000000"),
    ("email_verified", True),
    ("password", "hunter2hunter2"),
    ("password_hash", "not-a-real-hash"),
    ("id", "00000000-0000-0000-0000-000000000000"),
])
def test_update_me_rejects_privilege_escalation(client, db, hr_headers, field, evil):
    """
    The escalation regression test.

    extra="forbid" must reject the WHOLE request, so the legitimate first_name
    sent in the same body must not be applied either. A partial apply would mean
    the handler ran before validation did.
    """
    from src.models.models import User

    before = client.get("/api/v1/auth/me", headers=hr_headers).json()

    resp = client.put("/api/v1/auth/me", headers=hr_headers,
                      json={"first_name": "Escalated", field: evil})
    assert resp.status_code == 422, f"{field} was not rejected: {resp.text}"

    after = client.get("/api/v1/auth/me", headers=hr_headers).json()
    assert after["first_name"] == before["first_name"], "request was partially applied"
    if field in after:
        assert after[field] == before[field], f"{field} changed"

    # Belt and braces: the columns the API never exposes, checked on the row.
    row = db.query(User).filter(User.email == "hr_test@test.com").first()
    assert row.role == "hr"
    assert row.password_hash != "not-a-real-hash"


@pytest.mark.parametrize("payload", [
    {"first_name": ""},
    {"first_name": "   "},
    {"last_name": ""},
])
def test_update_me_rejects_blank_name(client, hr_headers, payload):
    assert client.put("/api/v1/auth/me", headers=hr_headers, json=payload).status_code == 422


def test_update_me_rejects_overlong_phone(client, hr_headers):
    """phone is String(20); an over-long value must 422, never 500 from MySQL."""
    resp = client.put("/api/v1/auth/me", headers=hr_headers, json={"phone": "1" * 21})
    assert resp.status_code == 422, resp.text


def test_update_me_rejects_garbage_phone(client, hr_headers):
    resp = client.put("/api/v1/auth/me", headers=hr_headers, json={"phone": "call me maybe!"})
    assert resp.status_code == 422


def test_update_me_allows_clearing_phone(client, hr_headers):
    client.put("/api/v1/auth/me", headers=hr_headers, json={"phone": "9876543210"})
    resp = client.put("/api/v1/auth/me", headers=hr_headers, json={"phone": ""})
    assert resp.status_code == 200
    assert resp.json()["phone"] is None


def test_update_me_rejects_empty_body(client, hr_headers):
    resp = client.put("/api/v1/auth/me", headers=hr_headers, json={})
    assert resp.status_code == 400
    assert "No changes" in resp.json()["detail"]


def test_update_me_requires_authentication(client):
    assert client.put("/api/v1/auth/me", json={"first_name": "X"}).status_code in (401, 403)


@pytest.mark.parametrize("fixture_name", [
    "admin_headers", "hr_headers", "interviewer_headers", "candidate_headers",
])
def test_update_me_reachable_by_every_role(client, request, fixture_name):
    headers = request.getfixturevalue(fixture_name)
    resp = client.put("/api/v1/auth/me", headers=headers, json={"first_name": "Updated"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["first_name"] == "Updated"


def test_update_me_does_not_touch_another_user(client, db, hr_headers, candidate_headers):
    from src.models.models import User

    before = db.query(User).filter(User.email == "candidate_test@test.com").first().first_name
    client.put("/api/v1/auth/me", headers=hr_headers, json={"first_name": "OnlyMine"})
    after = db.query(User).filter(User.email == "candidate_test@test.com").first().first_name
    assert after == before


# ── POST /auth/change-password ────────────────────────────────────────────────

def test_change_password_rejects_wrong_current(client, hr_headers):
    """
    Must be 400, NOT 401.

    frontend/src/api/client.js treats any 401 as a dead session: it clears
    localStorage and hard-redirects to /login. Returning 401 here would sign the
    user out for mistyping their own password and discard the form.
    """
    resp = client.post("/api/v1/auth/change-password", headers=hr_headers,
                       json={"current_password": "WrongPass@1", "new_password": "BrandNew@123"})
    assert resp.status_code == 400, resp.text
    assert resp.status_code != 401


def test_change_password_succeeds_and_rotates_credentials(client, hr_headers):
    resp = client.post("/api/v1/auth/change-password", headers=hr_headers,
                       json={"current_password": "Pass@1234", "new_password": "BrandNew@123"})
    assert resp.status_code == 200, resp.text

    ok = client.post("/api/v1/auth/login",
                     json={"email": "hr_test@test.com", "password": "BrandNew@123"})
    assert ok.status_code == 200, ok.text

    stale = client.post("/api/v1/auth/login",
                        json={"email": "hr_test@test.com", "password": "Pass@1234"})
    assert stale.status_code == 401


def test_change_password_rejects_same_as_current(client, hr_headers):
    resp = client.post("/api/v1/auth/change-password", headers=hr_headers,
                       json={"current_password": "Pass@1234", "new_password": "Pass@1234"})
    assert resp.status_code == 400
    assert "different" in resp.json()["detail"].lower()


def test_change_password_rejects_short_password(client, hr_headers):
    resp = client.post("/api/v1/auth/change-password", headers=hr_headers,
                       json={"current_password": "Pass@1234", "new_password": "short"})
    assert resp.status_code == 422


def test_change_password_rejects_extra_fields(client, hr_headers):
    resp = client.post("/api/v1/auth/change-password", headers=hr_headers,
                       json={"current_password": "Pass@1234", "new_password": "BrandNew@123",
                             "email": "attacker@evil.com"})
    assert resp.status_code == 422


def test_change_password_requires_authentication(client):
    resp = client.post("/api/v1/auth/change-password",
                       json={"current_password": "Pass@1234", "new_password": "BrandNew@123"})
    assert resp.status_code in (401, 403)


def test_change_password_clears_pending_reset_otp(client, db, hr_headers):
    """A reset OTP already in flight must not outlive an authenticated change."""
    from datetime import datetime, timedelta

    from src.models.models import User

    from src.auth import _otp_digest
    user = db.query(User).filter(User.email == "hr_test@test.com").first()
    user.otp_hash = _otp_digest(user, "reset", "123456")
    user.otp_purpose = "reset"
    user.otp_expires_at = datetime.utcnow() + timedelta(minutes=10)
    db.flush()

    resp = client.post("/api/v1/auth/change-password", headers=hr_headers,
                       json={"current_password": "Pass@1234", "new_password": "BrandNew@123"})
    assert resp.status_code == 200, resp.text

    db.refresh(user)
    assert user.otp_hash is None
    assert user.otp_expires_at is None
