"""
Per-company email settings ("Email Settings" tab) on top of the email API.

Covers:
- src.security encrypt/decrypt (still used for MFA secrets and legacy reads)
- GET / PUT / DELETE /admin/email-config: the company default sender, verified
  on save, password encrypted under the email keyring and never echoed
- POST /admin/email/test: a controlled, synchronous send through the company
  sender — never the platform's — to an authorised recipient only
- Authorization and cross-tenant isolation
- Message bodies (OTP codes above all) never reach the log

TLS verification itself is pinned in test_email_ssrf.py and
test_email_smtp_adapter.py.
"""
import logging
import uuid

import pytest

from src.email_api import constants as C, crypto
from src.models.models import EmailConnection, EmailMessage
from test.email_factories import FakeAdapter, make_company, make_route, make_smtp_sender


# Rate limiter is disabled suite-wide via conftest._disable_rate_limiter_for_suite.


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

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


def _create_admin_directly(db, email: str, password: str = "Pass@1234",
                           company_id: str = None) -> str:
    """Self-registration is candidate-only, so admins are seeded directly."""
    return _create_user_directly(db, email, password, role="admin", company_id=company_id)


def _login(client, email: str, password: str = "Pass@1234") -> dict:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_company(db, name: str = "Acme Corp") -> str:
    return make_company(db, name.replace(" ", "")).id


@pytest.fixture()
def admin_with_company(client, db):
    """Admin tied to a company. Returns (headers, company_id, admin_email)."""
    cid = _make_company(db, name="Admin Co")
    email = f"admin_{uuid.uuid4().hex[:8]}@test.com"
    _create_admin_directly(db, email, company_id=cid)
    return _login(client, email), cid, email


@pytest.fixture()
def admin_no_company(client, db):
    """Admin without any company linkage — used to assert the 400 path."""
    email = f"admin_noco_{uuid.uuid4().hex[:8]}@test.com"
    _create_admin_directly(db, email, company_id=None)
    return _login(client, email)


@pytest.fixture()
def adapter(monkeypatch):
    """Record every verification and send; nothing touches the network."""
    fake = FakeAdapter()
    monkeypatch.setattr("src.email_api.adapters.base.get_adapter",
                        lambda conn, is_test=False: fake)
    return fake


def _default_connection(db, company_id):
    from src.email_api import facade
    db.expire_all()
    return facade.default_sender(db, company_id)


CONFIG = {
    "smtp_host": "smtp.gmail.com",
    "smtp_port": 587,
    "smtp_user": "hr@mycompany.com",
    "smtp_pass": "the-app-password",
    "smtp_from_email": "hr@mycompany.com",
}


# ─────────────────────────────────────────────────────────────────────────────
# Unit tests — src.security (legacy key: MFA secrets, migrated credentials)
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
# GET /admin/email-config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_get_email_config_defaults_when_none(client, admin_with_company):
    headers, _, _ = admin_with_company
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
# PUT /admin/email-config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_put_email_config_saves_verifies_and_encrypts(client, db, admin_with_company, adapter):
    headers, company_id, _ = admin_with_company

    resp = client.put("/api/v1/admin/email-config", headers=headers, json=CONFIG)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["saved"] is True
    assert body["configured"] is True
    assert body["has_password"] is True
    assert body["status"] == C.CONN_ACTIVE
    assert body["security"] == C.SECURITY_STARTTLS
    # The plaintext password must NEVER be echoed back
    assert "the-app-password" not in resp.text
    assert "smtp_pass" not in body

    # It became the company's default sender, with the password encrypted
    # under the email keyring (not plaintext, not the JWT-derived key).
    sender, conn = _default_connection(db, company_id)
    assert sender.email == "hr@mycompany.com" and conn.username == "hr@mycompany.com"
    assert "the-app-password" not in conn.credential_enc
    assert conn.credential_enc not in resp.text
    assert crypto.decrypt_secret(conn.credential_enc, conn.credential_key_version) == "the-app-password"


@pytest.mark.admin
def test_put_email_config_blank_password_keeps_existing(client, db, admin_with_company, adapter):
    headers, company_id, _ = admin_with_company
    # 1st save with a password
    client.put("/api/v1/admin/email-config", headers=headers,
               json={"smtp_user": "hr@mycompany.com", "smtp_pass": "first-password"})
    # 2nd save WITHOUT smtp_pass — must preserve the original
    resp = client.put("/api/v1/admin/email-config", headers=headers,
                      json={"smtp_user": "hr@updated.com"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["has_password"] is True

    sender, conn = _default_connection(db, company_id)
    assert crypto.decrypt_secret(conn.credential_enc, conn.credential_key_version) == "first-password"
    assert conn.username == "hr@updated.com"      # but the login was updated
    assert sender.email == "hr@updated.com"


@pytest.mark.admin
def test_put_email_config_failed_login_is_saved_with_the_reason(client, admin_with_company):
    """A reachable mailbox that rejects the login is saved as failed — the screen
    says what to fix instead of looking configured."""
    headers, _, _ = admin_with_company
    resp = client.put("/api/v1/admin/email-config", headers=headers,
                      json={**CONFIG, "smtp_user": "fail@mycompany.com",
                            "smtp_from_email": "fail@mycompany.com"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == C.CONN_FAILED
    assert body["error_code"] == C.R_AUTH_FAILED
    assert body["configured"] is False


@pytest.mark.admin
@pytest.mark.parametrize("change,fragment", [
    ({"smtp_port": 25}, "465"),
    ({"smtp_host": "127.0.0.1"}, "hostname"),
    ({"smtp_from_email": "ceo@another-domain.com"}, "same domain"),
])
def test_put_email_config_rejects_unsafe_settings(client, db, admin_with_company, change, fragment):
    """Port 25, IP literals and spoofed From addresses are refused, nothing stored."""
    headers, company_id, _ = admin_with_company
    resp = client.put("/api/v1/admin/email-config", headers=headers, json={**CONFIG, **change})
    assert resp.status_code == 400, resp.text
    assert fragment in resp.json()["detail"]
    db.rollback()
    assert db.query(EmailConnection).filter_by(company_id=company_id).count() == 0


@pytest.mark.security
@pytest.mark.admin
def test_put_email_config_refuses_private_destinations(client, db, admin_with_company, monkeypatch):
    from src.email_api import ssrf
    monkeypatch.setattr(ssrf, "_system_resolve", lambda host, port: ["10.0.0.5"])
    headers, _, _ = admin_with_company
    resp = client.put("/api/v1/admin/email-config", headers=headers, json=CONFIG)
    assert resp.status_code == 400
    assert "non-public" in resp.json()["detail"]


@pytest.mark.admin
def test_get_after_put_never_returns_password(client, db, admin_with_company):
    headers, company_id, _ = admin_with_company
    client.put("/api/v1/admin/email-config", headers=headers,
               json={"smtp_user": "hr@mycompany.com", "smtp_pass": "supersecret-do-not-leak"})
    resp = client.get("/api/v1/admin/email-config", headers=headers)
    assert resp.status_code == 200
    assert "supersecret-do-not-leak" not in resp.text
    assert "smtp_pass" not in resp.json()
    _, conn = _default_connection(db, company_id)
    assert conn.credential_enc not in resp.text, "stored ciphertext leaked"


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /admin/email-config
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_delete_email_config_disconnects_and_blocks(client, db, admin_with_company):
    headers, company_id, _ = admin_with_company
    client.put("/api/v1/admin/email-config", headers=headers,
               json={"smtp_user": "hr@mycompany.com", "smtp_pass": "to-be-cleared"})
    _, conn = _default_connection(db, company_id)
    resp = client.delete("/api/v1/admin/email-config", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["cleared"] is True

    after = client.get("/api/v1/admin/email-config", headers=headers).json()
    assert after["configured"] is False
    assert after["has_password"] is False

    # The credential is destroyed, and company mail now blocks — it is never
    # sent from the platform's account instead.
    db.expire_all()
    conn = db.query(EmailConnection).filter_by(id=conn.id).one()
    assert conn.status == C.CONN_DISCONNECTED and conn.credential_enc is None
    from src.email_api.policy import resolve_sender
    d = resolve_sender(db, company_id, "offer_letter")
    assert d.status == "blocked" and d.reason_code == C.R_NO_SENDER


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
    assert "companya.com" not in str(b_view).lower()


# ─────────────────────────────────────────────────────────────────────────────
# Test-email endpoint — the company's own sender, controlled recipients
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_send_test_email_uses_company_sender(client, db, admin_with_company, adapter):
    """It logs in with the company's credentials, synchronously, and records it."""
    headers, company_id, admin_email = admin_with_company
    client.put("/api/v1/admin/email-config", headers=headers,
               json={"smtp_user": "hr@tenant.com", "smtp_pass": "tenant-app-pass"})

    resp = client.post("/api/v1/admin/email/test", headers=headers, json={
        "to": admin_email, "subject": "T", "body": "<p>x</p>",
    })

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["using_company_smtp"] is True
    assert data["sent"] is True
    assert data["config"]["smtp_user"] == "hr@tenant.com"
    assert "tenant-app-pass" not in resp.text
    # The company's credentials — never the platform's.
    call = adapter.calls[-1]
    assert call["secret"] == "tenant-app-pass"
    assert call["snapshot"].from_email == "hr@tenant.com"
    assert call["snapshot"].to == (admin_email,)
    db.expire_all()
    msg = db.query(EmailMessage).filter_by(id=data["message_id"]).one()
    assert (msg.company_id, msg.event_type, msg.status) == (company_id, C.EVENT_CONNECTION_TEST, C.ACCEPTED)


@pytest.mark.admin
def test_send_test_email_only_to_authorised_recipients(client, admin_with_company, adapter):
    headers, _, _ = admin_with_company
    client.put("/api/v1/admin/email-config", headers=headers,
               json={"smtp_user": "hr@tenant.com", "smtp_pass": "tenant-app-pass"})
    resp = client.post("/api/v1/admin/email/test", headers=headers, json={
        "to": "stranger@example.com", "subject": "T", "body": "<p>x</p>",
    })
    assert resp.status_code == 400, resp.text
    assert adapter.calls == []
    # The sender's own address is always allowed.
    ok = client.post("/api/v1/admin/email/test", headers=headers, json={
        "to": "hr@tenant.com", "subject": "T", "body": "<p>x</p>",
    })
    assert ok.status_code == 200 and ok.json()["sent"] is True


@pytest.mark.admin
def test_send_test_email_refuses_when_not_configured(client, admin_with_company, adapter):
    """With no company sender, /email/test refuses rather than sending as the platform.

    It used to fall back to the platform's env SMTP and echo that host and account back
    to the tenant.
    """
    headers, _, admin_email = admin_with_company
    resp = client.post("/api/v1/admin/email/test", headers=headers, json={
        "to": admin_email, "subject": "T", "body": "<p>x</p>",
    })
    assert resp.status_code == 400, resp.text
    assert adapter.calls == []
    assert "smtp" not in {k.lower() for k in resp.json()}


# ─────────────────────────────────────────────────────────────────────────────
# Message bodies must not reach the log
#
# _send used to log the full body at INFO on every send. OTP and password-reset
# codes are rendered into that body, so live authentication material landed in
# journald and everything downstream of it.
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def configured(db, monkeypatch):
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: False)
    co = make_company(db, "Logco")
    sender, _ = make_smtp_sender(db, co.id, "HR", email="hr@logco.example")
    make_route(db, co.id, C.DEFAULT_ROUTE, sender.id)
    return co.id


def _send_otp(es, db, company_id, otp="381927"):
    ref = es.resolve_auth_sender(db, company_id, C.EVENT_AUTH_RESET)
    return es.send_password_reset_otp("victim@example.com", "Vic", otp, smtp_config=ref, db=db)


def _send_plain(es, db, company_id):
    ref = es.resolve_sender(db, company_id, event_type="custom_mail")
    return es.dispatch("c@example.com", "S", "<p>marker-body-text</p>", smtp_config=ref, db=db)


@pytest.mark.security
def test_otp_code_is_never_logged_by_default(db, configured, caplog, monkeypatch):
    from src.services import email_service as es
    monkeypatch.setattr(es, "EMAIL_LOG_BODIES", False)
    caplog.set_level(logging.DEBUG, logger="src.services.email_service")
    _send_otp(es, db, configured)

    assert "381927" not in caplog.text, "OTP code written to the log"
    assert "[EMAIL BODY]" not in caplog.text
    # The envelope line stays — it is the send trace.
    assert "victim@example.com" in caplog.text


@pytest.mark.security
def test_otp_body_stays_unlogged_even_with_body_logging_enabled(db, configured, caplog, monkeypatch):
    """EMAIL_LOG_BODIES is an escape hatch for debugging, not for OTP mail."""
    from src.services import email_service as es
    monkeypatch.setattr(es, "EMAIL_LOG_BODIES", True)
    caplog.set_level(logging.DEBUG, logger="src.services.email_service")
    _send_otp(es, db, configured)

    assert "381927" not in caplog.text, (
        "OTP code logged when EMAIL_LOG_BODIES was enabled — is_otp must win"
    )
    assert "[EMAIL BODY]" not in caplog.text


@pytest.mark.security
def test_non_otp_body_logs_only_with_optin_and_debug(db, configured, caplog, monkeypatch):
    """Both conditions are required, so a global DEBUG flip alone logs nothing."""
    from src.services import email_service as es

    # Opt-in off, DEBUG on -> no body.
    monkeypatch.setattr(es, "EMAIL_LOG_BODIES", False)
    caplog.set_level(logging.DEBUG, logger="src.services.email_service")
    _send_plain(es, db, configured)
    assert "marker-body-text" not in caplog.text

    # Opt-in on, level raised above DEBUG -> still no body.
    caplog.clear()
    monkeypatch.setattr(es, "EMAIL_LOG_BODIES", True)
    caplog.set_level(logging.INFO, logger="src.services.email_service")
    _send_plain(es, db, configured)
    assert "marker-body-text" not in caplog.text

    # Both -> body is logged, at DEBUG.
    caplog.clear()
    caplog.set_level(logging.DEBUG, logger="src.services.email_service")
    _send_plain(es, db, configured)
    assert "marker-body-text" in caplog.text
    body_records = [r for r in caplog.records if "[EMAIL BODY]" in r.getMessage()]
    assert body_records and all(r.levelno == logging.DEBUG for r in body_records)
