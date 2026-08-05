"""
Per-company email (SMTP) configuration tests.

Covers:
- src.security.encrypt/decrypt round-trip + tamper detection
- email_service.resolve_smtp_for_company DB lookup + decryption
- Admin endpoints: GET / PUT / DELETE /admin/email-config
- POST /admin/email/test with company-specific SMTP override
- Authorization isolation: non-admins blocked, cross-tenant isolation
"""
import uuid
import pytest
from unittest.mock import patch, MagicMock


# Rate limiter is disabled suite-wide via conftest._disable_rate_limiter_for_suite.


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_admin_directly(db, email: str, password: str = "Pass@1234",
                            company_id: str = None) -> str:
    """Self-registration is candidate-only, so admins are seeded directly.
    Returns the user_id."""
    return _create_user_directly(db, email, password, role="admin", company_id=company_id)


def _create_user_directly(db, email: str, password: str, role: str,
                          company_id: str = None) -> str:
    from src.models.models import User
    from src.auth import get_password_hash
    user = User(
        id=str(uuid.uuid4()),
        email=email,
        password_hash=get_password_hash(password),
        role=role,
        first_name=role.capitalize(),
        last_name="Tester",
        is_active=True,
        email_verified=True,
        company_id=company_id,
    )
    db.add(user)
    db.flush()
    return user.id


def _login(client, email: str, password: str = "Pass@1234") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_company(db, name: str = "Acme Corp") -> str:
    from src.models.models import Company
    company = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(company)
    db.flush()
    return company.id


@pytest.fixture()
def admin_with_company(client, db):
    """Admin tied to a company. Returns (headers, company_id)."""
    cid = _make_company(db, name="Admin Co")
    email = f"admin_{uuid.uuid4().hex[:8]}@test.com"
    _create_admin_directly(db, email, company_id=cid)
    return _login(client, email), cid


@pytest.fixture()
def admin_no_company(client, db):
    """Admin without any company linkage — used to assert the 400 path."""
    email = f"admin_noco_{uuid.uuid4().hex[:8]}@test.com"
    _create_admin_directly(db, email, company_id=None)
    return _login(client, email)


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — src.security
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_encrypt_decrypt_roundtrip():
    from src.security import encrypt, decrypt
    plain = "my-gmail-app-password-1234"
    cipher = encrypt(plain)
    assert cipher != plain
    assert decrypt(cipher) == plain


@pytest.mark.security
def test_encrypt_empty_returns_empty():
    from src.security import encrypt, decrypt
    assert encrypt("") == ""
    assert encrypt(None) == ""
    assert decrypt("") == ""


@pytest.mark.security
def test_decrypt_garbage_returns_empty():
    """Invalid/tampered ciphertext must NOT raise — returns empty string."""
    from src.security import decrypt
    assert decrypt("not-a-real-fernet-token") == ""
    assert decrypt("gAAAAABabcdef-tampered") == ""


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — resolve_smtp_for_company
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_resolve_smtp_returns_none_when_no_tenant_config(db):
    from src.services.email_service import resolve_smtp_for_company
    assert resolve_smtp_for_company(db, None) is None
    assert resolve_smtp_for_company(db, "nonexistent-company-id") is None


@pytest.mark.admin
def test_resolve_smtp_returns_decrypted_config(db):
    """Seeded TenantConfig with encrypted password must be returned decrypted."""
    from src.models.models import Company, TenantConfig
    from src.security import encrypt
    from src.services.email_service import resolve_smtp_for_company

    company = Company(id=str(uuid.uuid4()), name="Seeded Co", slug=f"seed-{uuid.uuid4().hex[:6]}")
    db.add(company)
    db.flush()
    tc = TenantConfig(
        company_id=company.id,
        smtp_host="smtp.gmail.com",
        smtp_port=587,
        smtp_user="hr@seeded.com",
        smtp_pass=encrypt("real-password"),
        smtp_from_email="hr@seeded.com",
    )
    db.add(tc)
    db.flush()

    cfg = resolve_smtp_for_company(db, company.id)
    assert cfg is not None
    assert cfg["host"] == "smtp.gmail.com"
    assert cfg["port"] == 587
    assert cfg["user"] == "hr@seeded.com"
    assert cfg["password"] == "real-password"  # decrypted in memory
    assert cfg["from_email"] == "hr@seeded.com"


@pytest.mark.admin
def test_resolve_smtp_returns_none_when_no_password(db):
    """A TenantConfig row with no smtp_pass is treated as not configured."""
    from src.models.models import Company, TenantConfig
    from src.services.email_service import resolve_smtp_for_company

    company = Company(id=str(uuid.uuid4()), name="Half Co", slug=f"half-{uuid.uuid4().hex[:6]}")
    db.add(company)
    db.flush()
    db.add(TenantConfig(
        company_id=company.id,
        smtp_user="hr@half.com",
        smtp_pass=None,  # the discriminator
    ))
    db.flush()

    assert resolve_smtp_for_company(db, company.id) is None


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint tests — GET /admin/email-config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_get_email_config_defaults_when_none(client, admin_with_company):
    headers, _ = admin_with_company
    resp = client.get("/api/v1/admin/email-config", headers=headers)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["smtp_host"] == "smtp.gmail.com"
    assert data["smtp_port"] == 587
    assert data["smtp_user"] == ""
    assert data["has_password"] is False
    assert data["configured"] is False


@pytest.mark.admin
def test_get_email_config_requires_company(client, admin_no_company):
    """Admin without a company_id cannot read email config (400)."""
    resp = client.get("/api/v1/admin/email-config", headers=admin_no_company)
    assert resp.status_code == 400
    assert "company" in resp.json()["detail"].lower()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint tests — PUT /admin/email-config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_put_email_config_saves_and_encrypts(client, db, admin_with_company):
    headers, company_id = admin_with_company

    resp = client.put("/api/v1/admin/email-config", headers=headers, json={
        "smtp_host": "smtp.gmail.com",
        "smtp_port": 587,
        "smtp_user": "hr@mycompany.com",
        "smtp_pass": "the-app-password",
        "smtp_from_email": "hr@mycompany.com",
    })
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert body["configured"] is True
    assert body["has_password"] is True
    # The plaintext password must NEVER be echoed back
    assert "the-app-password" not in resp.text
    assert "smtp_pass" not in body  # field is intentionally omitted from response

    # And the row stored in DB must have an encrypted password (not plaintext)
    from src.models.models import TenantConfig
    db.expire_all()
    tc = db.query(TenantConfig).filter(TenantConfig.company_id == company_id).first()
    assert tc is not None
    assert tc.smtp_pass != "the-app-password"
    assert tc.smtp_pass != ""
    # Confirm encryption is reversible via the same key
    from src.security import decrypt
    assert decrypt(tc.smtp_pass) == "the-app-password"


@pytest.mark.admin
def test_put_email_config_blank_password_keeps_existing(client, db, admin_with_company):
    headers, company_id = admin_with_company
    # 1st save with a password
    client.put("/api/v1/admin/email-config", headers=headers, json={
        "smtp_user": "hr@mycompany.com",
        "smtp_pass": "first-password",
    })
    # 2nd save WITHOUT smtp_pass — must preserve the original
    resp = client.put("/api/v1/admin/email-config", headers=headers, json={
        "smtp_user": "hr@updated.com",
        # no smtp_pass field at all
    })
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_password"] is True

    from src.models.models import TenantConfig
    from src.security import decrypt
    db.expire_all()
    tc = db.query(TenantConfig).filter(TenantConfig.company_id == company_id).first()
    assert decrypt(tc.smtp_pass) == "first-password"  # unchanged
    assert tc.smtp_user == "hr@updated.com"           # but user field updated


@pytest.mark.admin
def test_get_after_put_never_returns_password(client, admin_with_company):
    headers, _ = admin_with_company
    client.put("/api/v1/admin/email-config", headers=headers, json={
        "smtp_user": "hr@mycompany.com",
        "smtp_pass": "supersecret-do-not-leak",
    })
    resp = client.get("/api/v1/admin/email-config", headers=headers)
    assert resp.status_code == 200
    assert "supersecret-do-not-leak" not in resp.text
    # And no key called smtp_pass in the response payload
    assert "smtp_pass" not in resp.json()


# ─────────────────────────────────────────────────────────────────────────────
# Endpoint tests — DELETE /admin/email-config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_delete_email_config_clears_smtp_fields(client, db, admin_with_company):
    headers, company_id = admin_with_company
    client.put("/api/v1/admin/email-config", headers=headers, json={
        "smtp_user": "hr@mycompany.com",
        "smtp_pass": "to-be-cleared",
    })
    resp = client.delete("/api/v1/admin/email-config", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["cleared"] is True

    # GET should now reflect "not configured"
    after = client.get("/api/v1/admin/email-config", headers=headers).json()
    assert after["configured"] is False
    assert after["has_password"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Authorization & tenant isolation
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_non_admin_cannot_read_email_config(client, db):
    """Only admins may access /admin/email-config endpoints."""
    hr_email = f"hr_{uuid.uuid4().hex[:6]}@test.com"
    cand_email = f"cand_{uuid.uuid4().hex[:6]}@test.com"
    _create_user_directly(db, hr_email, "Pass@1234", role="hr")
    _create_user_directly(db, cand_email, "Pass@1234", role="candidate")

    hr_headers   = _login(client, hr_email)
    cand_headers = _login(client, cand_email)

    assert client.get("/api/v1/admin/email-config", headers=hr_headers).status_code == 403
    assert client.get("/api/v1/admin/email-config", headers=cand_headers).status_code == 403


@pytest.mark.security
def test_non_admin_cannot_write_email_config(client, db):
    hr_email = f"hr_{uuid.uuid4().hex[:6]}@test.com"
    _create_user_directly(db, hr_email, "Pass@1234", role="hr")
    hr_headers = _login(client, hr_email)
    resp = client.put("/api/v1/admin/email-config", headers=hr_headers, json={
        "smtp_user": "evil@hacker.com",
        "smtp_pass": "stealth",
    })
    assert resp.status_code == 403


@pytest.mark.security
def test_admin_cannot_see_other_companys_config(client, db):
    """Two separately-scoped admins must never see each other's SMTP config."""
    company_a = _make_company(db, name="Company A")
    company_b = _make_company(db, name="Company B")
    email_a = f"admin_a_{uuid.uuid4().hex[:6]}@test.com"
    email_b = f"admin_b_{uuid.uuid4().hex[:6]}@test.com"
    _create_admin_directly(db, email_a, company_id=company_a)
    _create_admin_directly(db, email_b, company_id=company_b)

    a_headers = _login(client, email_a)
    b_headers = _login(client, email_b)

    client.put("/api/v1/admin/email-config", headers=a_headers, json={
        "smtp_user": "a@companyA.com",
        "smtp_pass": "secret-A",
    })

    # Admin B reads — must see their own empty config, not A's
    b_view = client.get("/api/v1/admin/email-config", headers=b_headers).json()
    assert b_view["smtp_user"] == ""
    assert b_view["configured"] is False
    assert "a@companyA.com" not in str(b_view)


# ─────────────────────────────────────────────────────────────────────────────
# Test-email endpoint — uses company SMTP when configured
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_send_test_email_uses_company_smtp_when_configured(client, admin_with_company):
    """When the company has its own SMTP, /email/test must log in with that user."""
    headers, _ = admin_with_company
    client.put("/api/v1/admin/email-config", headers=headers, json={
        "smtp_user": "hr@tenant.com",
        "smtp_pass": "tenant-app-pass",
    })

    # Patch smtplib so no real network call is made
    with patch("src.services.email_service.smtplib.SMTP") as MockSMTP:
        instance = MagicMock()
        MockSMTP.return_value.__enter__.return_value = instance

        resp = client.post("/api/v1/admin/email/test", headers=headers, json={
            "to": "candidate@example.com",
            "subject": "T",
            "body": "<p>x</p>",
        })

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["using_company_smtp"] is True
    assert data["sent"] is True
    assert data["config"]["smtp_user"] == "hr@tenant.com"
    # The mocked SMTP must have been logged in using the company credentials,
    # NOT the global env-var defaults.
    instance.login.assert_called_once_with("hr@tenant.com", "tenant-app-pass")


@pytest.mark.admin
def test_send_test_email_falls_back_to_global_when_not_configured(client, admin_with_company):
    """When the company has no SMTP override, the response indicates the fallback."""
    headers, _ = admin_with_company

    with patch("src.services.email_service.smtplib.SMTP") as MockSMTP:
        MockSMTP.return_value.__enter__.return_value = MagicMock()
        resp = client.post("/api/v1/admin/email/test", headers=headers, json={
            "to": "candidate@example.com",
            "subject": "T",
            "body": "<p>x</p>",
        })

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["using_company_smtp"] is False  # global env-var path taken
