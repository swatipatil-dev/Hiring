"""HR ad-hoc Compose Mail — POST /api/v1/hr/send-mail.

This endpoint replaced a button that popped `alert('Mail sent!')` and made no
request at all, so the tests here are mostly about the send actually happening,
and happening through the right path:

- it goes through email_service.dispatch (the RQ-queued, hourly-limit-respecting
  path), never the raw _send that bypasses the provider cap
- a mailbox_id belonging to another company is ignored rather than borrowed
- the step's configured CC is merged with the caller's CC
- the response reports queued-vs-sent honestly
"""
import json
import uuid
import pytest

from src.security import encrypt


def _make_company(db, name="Compose Co") -> str:
    from src.models.models import Company
    c = Company(id=str(uuid.uuid4()), name=name, slug=f"co-{uuid.uuid4().hex[:8]}")
    db.add(c)
    db.flush()
    return c.id


def _create_user(db, email, role="hr", company_id=None, password="Pass@1234"):
    from src.models.models import User
    from src.auth import get_password_hash
    u = User(
        id=str(uuid.uuid4()), email=email,
        password_hash=get_password_hash(password), role=role,
        first_name=role.upper(), last_name="Tester",
        is_active=True, email_verified=True, company_id=company_id,
    )
    db.add(u)
    db.flush()
    return u.id


def _login(client, email, password="Pass@1234"):
    r = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _mailbox(db, company_id, name, *, user="box@acme.com", password="pw"):
    from src.models.models import CompanyMailbox
    mb = CompanyMailbox(
        id=str(uuid.uuid4()), company_id=company_id, name=name,
        smtp_host="smtp.acme.com", smtp_port=587,
        smtp_user=user, smtp_pass=encrypt(password) if password else None,
        smtp_from_email=user, is_active=True,
    )
    db.add(mb)
    db.flush()
    return mb


def _step(db, company_id, step_key, mailbox_id=None, extra_emails=None):
    from src.models.models import PipelineStepConfig
    row = PipelineStepConfig(
        id=str(uuid.uuid4()), company_id=company_id, step_key=step_key,
        enabled=True, extra_emails=json.dumps(extra_emails or []),
        notify_roles=json.dumps([]), as_cc=True, mailbox_id=mailbox_id,
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture()
def captured(monkeypatch):
    """Capture dispatch() calls instead of sending. Also poisons _send so a
    regression that reverts to the un-queued path fails loudly."""
    calls = []

    def fake_dispatch(to, subject, body, **kw):
        calls.append({"to": to, "subject": subject, "body": body, **kw})
        return {"mode": "async", "job_id": "job-123"}

    def poisoned_send(*a, **kw):
        raise AssertionError("send-mail must use dispatch(), not _send()")

    import src.services.email_service as es
    monkeypatch.setattr(es, "dispatch", fake_dispatch)
    monkeypatch.setattr(es, "_send", poisoned_send)
    return calls


BODY = {"to": "someone@example.com", "subject": "Hello", "body": "Line one\nLine two"}


def test_send_mail_uses_dispatch_not_send(client, db, captured):
    cid = _make_company(db)
    _create_user(db, "hr_compose@test.com", "hr", cid)
    headers = _login(client, "hr_compose@test.com")

    r = client.post("/api/v1/hr/send-mail", json=BODY, headers=headers)
    assert r.status_code == 200, r.text
    assert len(captured) == 1
    assert captured[0]["to"] == "someone@example.com"
    # Newlines become <br> so the plain-text textarea renders as HTML mail.
    assert "Line one<br>Line two" in captured[0]["body"]


def test_response_reports_queued_not_sent(client, db, captured):
    """The UI must be able to distinguish 'handed to the worker' from 'delivered'."""
    cid = _make_company(db)
    _create_user(db, "hr_queued@test.com", "hr", cid)
    headers = _login(client, "hr_queued@test.com")

    r = client.post("/api/v1/hr/send-mail", json=BODY, headers=headers)
    assert r.status_code == 200
    assert r.json()["mode"] == "async"


def test_foreign_mailbox_id_is_ignored(client, db, captured):
    """A guessed mailbox id from another tenant must not lend its SMTP credentials."""
    mine = _make_company(db, "Mine Co")
    theirs = _make_company(db, "Theirs Co")
    their_box = _mailbox(db, theirs, "Their Box", user="secret@theirs.com")

    _create_user(db, "hr_scope@test.com", "hr", mine)
    headers = _login(client, "hr_scope@test.com")

    r = client.post("/api/v1/hr/send-mail",
                    json={**BODY, "mailbox_id": their_box.id}, headers=headers)
    assert r.status_code == 200, r.text

    smtp = captured[0].get("smtp_config") or {}
    assert smtp.get("user") != "secret@theirs.com"
    assert smtp.get("from_email") != "secret@theirs.com"


def test_own_mailbox_id_is_honoured(client, db, captured):
    cid = _make_company(db, "Own Co")
    box = _mailbox(db, cid, "My Box", user="hello@own.com")
    _create_user(db, "hr_own@test.com", "hr", cid)
    headers = _login(client, "hr_own@test.com")

    r = client.post("/api/v1/hr/send-mail",
                    json={**BODY, "mailbox_id": box.id}, headers=headers)
    assert r.status_code == 200, r.text
    smtp = captured[0].get("smtp_config") or {}
    assert smtp.get("from_email") == "hello@own.com"


def test_step_cc_merges_with_request_cc(client, db, captured):
    cid = _make_company(db, "CC Co")
    _step(db, cid, "custom_mail", extra_emails=["ops@cc.com"])
    _create_user(db, "hr_cc@test.com", "hr", cid)
    headers = _login(client, "hr_cc@test.com")

    r = client.post("/api/v1/hr/send-mail",
                    json={**BODY, "cc": ["manager@cc.com"]}, headers=headers)
    assert r.status_code == 200, r.text
    cc = captured[0].get("cc") or []
    assert "ops@cc.com" in cc and "manager@cc.com" in cc


def test_offer_resend_reuses_endpoint_with_offer_step(client, db, captured):
    """The offer-preview dialog posts step_key=offer_letter so it resolves the
    sender HR configured for that stage, not the ad-hoc default."""
    cid = _make_company(db, "Offer Co")
    box = _mailbox(db, cid, "Offers", user="offers@co.com")
    _step(db, cid, "offer_letter", mailbox_id=box.id)
    _create_user(db, "hr_offer@test.com", "hr", cid)
    headers = _login(client, "hr_offer@test.com")

    r = client.post("/api/v1/hr/send-mail",
                    json={**BODY, "step_key": "offer_letter"}, headers=headers)
    assert r.status_code == 200, r.text
    assert (captured[0].get("smtp_config") or {}).get("from_email") == "offers@co.com"


def test_unknown_step_key_rejected(client, db, captured):
    cid = _make_company(db)
    _create_user(db, "hr_badstep@test.com", "hr", cid)
    headers = _login(client, "hr_badstep@test.com")

    r = client.post("/api/v1/hr/send-mail",
                    json={**BODY, "step_key": "no_such_step"}, headers=headers)
    assert r.status_code == 400
    assert not captured


def test_company_less_hr_rejected(client, db, captured):
    _create_user(db, "hr_nocompany@test.com", "hr", None)
    headers = _login(client, "hr_nocompany@test.com")

    r = client.post("/api/v1/hr/send-mail", json=BODY, headers=headers)
    assert r.status_code == 400
    assert not captured


def test_candidate_cannot_send(client, db, captured):
    cid = _make_company(db)
    _create_user(db, "cand_send@test.com", "candidate", cid)
    headers = _login(client, "cand_send@test.com")

    r = client.post("/api/v1/hr/send-mail", json=BODY, headers=headers)
    assert r.status_code == 403
    assert not captured


def test_blank_subject_or_body_rejected(client, db, captured):
    cid = _make_company(db)
    _create_user(db, "hr_blank@test.com", "hr", cid)
    headers = _login(client, "hr_blank@test.com")

    assert client.post("/api/v1/hr/send-mail",
                       json={**BODY, "subject": "   "}, headers=headers).status_code == 400
    assert client.post("/api/v1/hr/send-mail",
                       json={**BODY, "body": "  "}, headers=headers).status_code == 400
    assert not captured
