"""POST /api/v1/admin/mailboxes/{id}/test — prove a mailbox's credentials work.

Creating or updating a mailbox encrypts whatever password was typed without ever
attempting a connection, so a typo used to surface only when a candidate-facing
send failed — and until then the step silently fell back to the company default.
This endpoint is the one place that actually verifies the credential.
"""
import uuid
from unittest.mock import MagicMock, patch

import pytest

from src.services import email_log


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

def _make_company(db, name):
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(c); db.flush()
    return c.id


def _make_user(db, email, role, company_id):
    from src.models.models import User
    from src.auth import get_password_hash
    u = User(id=str(uuid.uuid4()), email=email,
             password_hash=get_password_hash("Pass@1234"), role=role,
             first_name=role.capitalize(), last_name="Tester",
             is_active=True, email_verified=True, company_id=company_id)
    db.add(u); db.flush()
    return u.id


def _login(client, email):
    r = client.post("/api/v1/auth/login",
                    json={"email": email, "password": "Pass@1234"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _make_mailbox(db, company_id, name="Offers", *, password="app-pass",
                  is_active=True, smtp_user="offers@tenant.com"):
    from src.models.models import CompanyMailbox
    from src.security import encrypt
    mb = CompanyMailbox(
        id=str(uuid.uuid4()), company_id=company_id, name=name,
        smtp_host="mail.tenant.com", smtp_port=465,
        smtp_user=smtp_user, smtp_from_email=smtp_user,
        smtp_pass=encrypt(password) if password else None,
        is_active=is_active,
    )
    db.add(mb); db.flush()
    return mb


@pytest.fixture()
def two_admins(client, db):
    out = {}
    for key, name in (("a", "Mailbox Co A"), ("b", "Mailbox Co B")):
        cid = _make_company(db, name)
        admin_email = f"admin_{key}_{uuid.uuid4().hex[:6]}@test.com"
        _make_user(db, admin_email, "admin", cid)
        out[key] = {"company_id": cid, "admin": _login(client, admin_email)}
    hr_email = f"hr_{uuid.uuid4().hex[:6]}@test.com"
    _make_user(db, hr_email, "hr", out["a"]["company_id"])
    out["a"]["hr"] = _login(client, hr_email)
    return out


def _url(mb):
    return f"/api/v1/admin/mailboxes/{mb.id}/test"


BODY = {"to": "check@example.com", "subject": "T", "body": "<p>x</p>"}


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_working_mailbox_reports_sent_and_leaks_no_password(client, db, two_admins):
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"])

    from src.services import email_service as es
    with patch.object(es, "EMAIL_ENABLED", True), \
         patch("src.services.email_service.smtplib.SMTP_SSL") as MockSSL:
        instance = MagicMock()
        MockSSL.return_value.__enter__.return_value = instance
        resp = client.post(_url(mb), headers=a["admin"], json=BODY)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] is True, data
    assert data["mailbox"]["name"] == "Offers"
    # It must use THIS mailbox's credentials, not the company/env default.
    instance.login.assert_called_once_with("offers@tenant.com", "app-pass")
    assert data["config"]["smtp_host"] == "mail.tenant.com"
    assert data["config"]["smtp_port"] == 465

    raw = resp.text
    assert "app-pass" not in raw
    assert mb.smtp_pass not in raw, "stored ciphertext leaked"
    for banned in ("password", "smtp_pass", "password_enc"):
        assert banned not in raw.lower()


@pytest.mark.admin
def test_bad_password_reports_the_smtp_error_synchronously(client, db, two_admins):
    """The whole point: the failure comes back now, not from a worker log later."""
    import smtplib
    from src.services import email_service as es

    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Careers")

    with patch.object(es, "EMAIL_ENABLED", True), \
         patch("src.services.email_service.smtplib.SMTP_SSL") as MockSSL:
        instance = MagicMock()
        instance.login.side_effect = smtplib.SMTPAuthenticationError(
            535, b"5.7.8 Username and Password not accepted")
        MockSSL.return_value.__enter__.return_value = instance
        resp = client.post(_url(mb), headers=a["admin"], json=BODY)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] is False
    assert data["error_type"] == "auth"
    assert "535" in data["error"]

    # And the attempt is recorded, so it is auditable after the fact.
    from src.models.models import EmailLog
    row = (db.query(EmailLog)
           .filter(EmailLog.to_email == "check@example.com")
           .order_by(EmailLog.created_at.desc()).first())
    assert row is not None and row.status == email_log.FAILED
    assert row.error_type == "auth"


@pytest.mark.admin
def test_passwordless_mailbox_is_reported_without_opening_a_connection(
        client, db, two_admins):
    """"No password set" is a config answer — don't dial the mail server for it."""
    from src.services import email_service as es

    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Empty", password=None)

    with patch.object(es, "EMAIL_ENABLED", True), \
         patch("src.services.email_service.smtplib.SMTP_SSL") as MockSSL, \
         patch("src.services.email_service.smtplib.SMTP") as MockSMTP:
        resp = client.post(_url(mb), headers=a["admin"], json=BODY)

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] is False
    assert data["error_type"] == "unusable"
    assert "no password set" in data["error"]
    MockSSL.assert_not_called()
    MockSMTP.assert_not_called()


@pytest.mark.admin
def test_inactive_mailbox_is_reported_as_unusable(client, db, two_admins):
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Retired", is_active=False)

    resp = client.post(_url(mb), headers=a["admin"], json=BODY)
    assert resp.status_code == 200, resp.text
    assert resp.json()["error"] == "mailbox is inactive"


@pytest.mark.security
def test_cannot_test_another_companys_mailbox(client, db, two_admins):
    """Guessing an id must not reach another tenant's SMTP credentials."""
    a, b = two_admins["a"], two_admins["b"]
    foreign = _make_mailbox(db, b["company_id"], name="TheirOffers")

    resp = client.post(_url(foreign), headers=a["admin"], json=BODY)
    assert resp.status_code == 404, resp.text


@pytest.mark.security
def test_non_admins_are_rejected(client, db, two_admins):
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Guarded")

    resp = client.post(_url(mb), headers=a["hr"], json=BODY)
    assert resp.status_code == 403, resp.text

    resp = client.post(_url(mb), json=BODY)
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.admin
def test_unknown_mailbox_is_404(client, two_admins):
    resp = client.post(f"/api/v1/admin/mailboxes/{uuid.uuid4()}/test",
                       headers=two_admins["a"]["admin"], json=BODY)
    assert resp.status_code == 404
