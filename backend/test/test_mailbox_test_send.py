"""POST /api/v1/admin/mailboxes/{id}/test — prove a mailbox actually sends.

Saving a mailbox verifies its login; this sends a real message through it, now,
synchronously, and reports what the mail server said. A queued test would report
"accepted" and surface an authentication failure minutes later in a worker log.
The test is recorded like any other message (event ``connection.test``), and it
only goes to the mailbox's own address or an active user of the company.
"""
import uuid

import pytest

from src.email_api import constants as C
from src.email_api.adapters import base as A
from src.models.models import EmailAttempt, EmailConnection, EmailMessage
from test.email_factories import FakeAdapter, failing, make_smtp_sender


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
                  enabled=True, email="offers@tenant.com", conn_status=C.CONN_ACTIVE):
    sender, _conn = make_smtp_sender(db, company_id, name, email=email, password=password,
                                     enabled=enabled, conn_status=conn_status)
    return sender


@pytest.fixture()
def two_admins(client, db):
    out = {}
    for key, name in (("a", "Mailbox Co A"), ("b", "Mailbox Co B")):
        cid = _make_company(db, name)
        admin_email = f"admin_{key}_{uuid.uuid4().hex[:6]}@test.com"
        _make_user(db, admin_email, "admin", cid)
        out[key] = {"company_id": cid, "admin": _login(client, admin_email),
                    "admin_email": admin_email}
    hr_email = f"hr_{uuid.uuid4().hex[:6]}@test.com"
    _make_user(db, hr_email, "hr", out["a"]["company_id"])
    out["a"]["hr"] = _login(client, hr_email)
    return out


@pytest.fixture()
def adapter(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(A, "get_adapter", lambda conn, is_test=False: fake)
    return fake


def _url(mb):
    return f"/api/v1/admin/mailboxes/{mb.id}/test"


def _body(to):
    return {"to": to, "subject": "T", "body": "<p>x</p>"}


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.admin
def test_working_mailbox_reports_sent_and_leaks_no_password(client, db, two_admins, adapter):
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"])
    conn = db.query(EmailConnection).filter_by(id=mb.connection_id).one()

    resp = client.post(_url(mb), headers=a["admin"], json=_body(a["admin_email"]))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] is True, data
    assert data["mailbox"]["name"] == "Offers"
    # It must use THIS mailbox's credentials and identity, nothing else.
    call = adapter.calls[0]
    assert call["conn"] == conn.id and call["secret"] == "app-pass"
    assert call["snapshot"].from_email == "offers@tenant.com"
    assert data["config"]["smtp_host"] == "smtp.example.com"
    assert data["config"]["smtp_port"] == 465

    raw = resp.text
    assert "app-pass" not in raw
    assert conn.credential_enc not in raw, "stored ciphertext leaked"
    for banned in ("password", "smtp_pass", "password_enc", "credential"):
        assert banned not in raw.lower()


@pytest.mark.admin
def test_bad_password_reports_the_error_synchronously(client, db, two_admins, adapter):
    """The whole point: the failure comes back now, not from a worker log later."""
    adapter.respond = failing(C.OUT_BLOCKED, C.R_AUTH_FAILED, A.BLOCKED,
                              connection_status=C.CONN_FAILED)
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Careers", email="careers@tenant.com")

    resp = client.post(_url(mb), headers=a["admin"], json=_body("careers@tenant.com"))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] is False
    assert data["error_type"] == "auth"
    assert data["error_code"] == C.R_AUTH_FAILED

    # Recorded, so it is auditable after the fact; and the connection is marked
    # failed, so routed mail blocks instead of failing one message at a time.
    db.expire_all()
    msg = db.query(EmailMessage).filter_by(id=data["message_id"]).one()
    assert msg.event_type == C.EVENT_CONNECTION_TEST and msg.status == C.BLOCKED
    attempt = db.query(EmailAttempt).filter_by(message_id=msg.id).one()
    assert attempt.error_code == C.R_AUTH_FAILED
    conn = db.query(EmailConnection).filter_by(id=mb.connection_id).one()
    assert conn.status == C.CONN_FAILED


@pytest.mark.admin
def test_a_failed_connection_can_still_be_tested(client, db, two_admins, adapter):
    """That is how an admin proves a fix: a successful test re-verifies it."""
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Fixed", email="fixed@tenant.com",
                       conn_status=C.CONN_FAILED)

    resp = client.post(_url(mb), headers=a["admin"], json=_body("fixed@tenant.com"))

    assert resp.status_code == 200 and resp.json()["sent"] is True, resp.text
    db.expire_all()
    assert db.query(EmailConnection).filter_by(id=mb.connection_id).one().status == C.CONN_ACTIVE


@pytest.mark.admin
def test_passwordless_mailbox_is_reported_without_opening_a_connection(
        client, db, two_admins, adapter):
    """"No password set" is a config answer — don't dial the mail server for it."""
    a = two_admins["a"]
    created = client.post("/api/v1/admin/mailboxes", headers=a["admin"], json={
        "name": "Empty", "smtp_host": "mail.tenant.com", "smtp_port": 465,
        "smtp_user": "empty@tenant.com"})
    assert created.status_code == 200, created.text
    assert created.json()["has_password"] is False and created.json()["usable"] is False

    resp = client.post(f"/api/v1/admin/mailboxes/{created.json()['id']}/test",
                       headers=a["admin"], json=_body("empty@tenant.com"))

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["sent"] is False
    assert data["error_type"] == "unusable"
    assert "no password" in data["error"]
    assert data["config"] is None
    assert adapter.calls == []


@pytest.mark.admin
def test_disabled_mailbox_is_reported_as_unusable(client, db, two_admins, adapter):
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Retired", enabled=False,
                       email="retired@tenant.com")

    resp = client.post(_url(mb), headers=a["admin"], json=_body("retired@tenant.com"))
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["error_type"] == "unusable" and data["error_code"] == C.R_SENDER_DISABLED
    assert "disabled" in data["error"]
    assert adapter.calls == []


@pytest.mark.security
def test_test_mail_only_goes_to_authorised_recipients(client, db, two_admins, adapter):
    """A test send is not a way to mail arbitrary people from a company mailbox."""
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Guarded2")
    resp = client.post(_url(mb), headers=a["admin"], json=_body("stranger@example.com"))
    assert resp.status_code == 400, resp.text
    assert adapter.calls == []


@pytest.mark.security
def test_cannot_test_another_companys_mailbox(client, db, two_admins, adapter):
    """Guessing an id must not reach another tenant's credentials."""
    a, b = two_admins["a"], two_admins["b"]
    foreign = _make_mailbox(db, b["company_id"], name="TheirOffers", email="offers@b.example")

    resp = client.post(_url(foreign), headers=a["admin"], json=_body("offers@b.example"))
    assert resp.status_code == 404, resp.text
    assert adapter.calls == []


@pytest.mark.security
def test_non_admins_are_rejected(client, db, two_admins):
    a = two_admins["a"]
    mb = _make_mailbox(db, a["company_id"], name="Guarded")

    resp = client.post(_url(mb), headers=a["hr"], json=_body("offers@tenant.com"))
    assert resp.status_code == 403, resp.text

    resp = client.post(_url(mb), json=_body("offers@tenant.com"))
    assert resp.status_code in (401, 403), resp.text


@pytest.mark.admin
def test_unknown_mailbox_is_404(client, two_admins):
    resp = client.post(f"/api/v1/admin/mailboxes/{uuid.uuid4()}/test",
                       headers=two_admins["a"]["admin"], json=_body("x@tenant.com"))
    assert resp.status_code == 404
