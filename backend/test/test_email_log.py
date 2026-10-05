"""Delivery is recorded, and endpoints stop claiming sends that did not happen.

The send helpers report failure by return value rather than by raising, so the
prevailing `try/except Exception` around a discarded return caught nothing that
actually occurred — a failed offer letter produced no row, no log line, and a
response saying it had been sent. Every email is now an ``email_messages`` row
from the moment it is accepted (or blocked); these tests pin that record, the
honest responses, and the history endpoint built on it. ``email_log`` itself is
kept read-only for one release (``/hr/email-log?source=legacy``).
"""
import uuid
from unittest.mock import patch

import pytest

from src.services import email_log


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _rows(db, to_email):
    from src.models.models import EmailLog
    return (db.query(EmailLog)
            .filter(EmailLog.to_email == to_email)
            .order_by(EmailLog.created_at.asc()).all())


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
    resp = client.post("/api/v1/auth/login",
                       json={"email": email, "password": "Pass@1234"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.fixture()
def two_companies_email_log(client, db):
    """Two tenants, each with an HR user, plus a candidate in the first."""
    out = {}
    for key, name in (("a", "Log Co A"), ("b", "Log Co B")):
        cid = _make_company(db, name)
        hr_email = f"hr_{key}_{uuid.uuid4().hex[:6]}@test.com"
        _make_user(db, hr_email, "hr", cid)
        out[key] = {"company_id": cid, "hr": _login(client, hr_email)}
    cand_email = f"cand_{uuid.uuid4().hex[:6]}@test.com"
    _make_user(db, cand_email, "candidate", out["a"]["company_id"])
    out["a"]["candidate"] = _login(client, cand_email)
    return out


def _cfg(company_id=None, step_key=None):
    cfg = {"host": "smtp.example.com", "port": 587, "user": "mailbox@tenant.com",
           "password": "pw", "from_email": "mailbox@tenant.com"}
    if company_id:
        cfg["company_id"] = company_id
    if step_key:
        cfg["step_key"] = step_key
    return cfg


# ─────────────────────────────────────────────────────────────────────────────
# The record: every accepted (or refused) email is a row from the start
# ─────────────────────────────────────────────────────────────────────────────

def _sender(db, company_id):
    from src.email_api import constants as C
    from test.email_factories import make_route, make_smtp_sender
    s, _ = make_smtp_sender(db, company_id, "HR", email="mailbox@tenant.example")
    make_route(db, company_id, C.DEFAULT_ROUTE, s.id)
    return s


def _messages(db, to_email):
    from src.models.models import EmailMessage, EmailRecipient
    ids = db.query(EmailRecipient.message_id).filter(EmailRecipient.email == to_email)
    return db.query(EmailMessage).filter(EmailMessage.id.in_(ids)).all()


@pytest.mark.security
def test_queued_message_is_recorded_before_the_worker_runs(db):
    """A worker that never runs must still leave evidence of the attempt."""
    from src.services import email_service as es

    to = f"queued_{uuid.uuid4().hex[:6]}@example.com"
    cid = _make_company(db, "Queued Co")
    _sender(db, cid)
    result = es.dispatch(to, "Subject", "<p>b</p>",
                         smtp_config=es.resolve_sender(db, cid, event_type="offer_letter"), db=db)

    assert result["status"] == "queued" and es.delivery_outcome(result)["status"] == "queued"
    [row] = _messages(db, to)
    assert (row.company_id, row.event_type, row.from_email) == (cid, "offer_letter",
                                                                "mailbox@tenant.example")
    assert row.id == result["message_id"]


@pytest.mark.security
def test_blocked_email_is_recorded_with_its_reason(db):
    """A company with no sender used to send from the platform's account. Now the
    email is kept, marked blocked, and the caller is told — not told "sent"."""
    from src.services import email_service as es

    to = f"blocked_{uuid.uuid4().hex[:6]}@example.com"
    cid = _make_company(db, "Bare Co")
    result = es.dispatch(to, "Subject", "<p>b</p>",
                         smtp_config=es.resolve_sender(db, cid, event_type="offer_letter"), db=db)

    outcome = es.delivery_outcome(result)
    assert outcome["status"] == "blocked" and outcome["ok"] is False
    [row] = _messages(db, to)
    assert row.status == "blocked" and row.status_reason == "no_sender_configured"


@pytest.mark.security
def test_disabled_step_records_nothing_and_says_so(db):
    from src.services import email_service as es
    from test.email_factories import make_route

    to = f"off_{uuid.uuid4().hex[:6]}@example.com"
    cid = _make_company(db, "Off Co")
    _sender(db, cid)
    make_route(db, cid, "candidate_rejected", enabled=False)
    result = es.dispatch(to, "S", "<p>b</p>",
                         smtp_config=es.resolve_sender(db, cid, event_type="candidate_rejected"),
                         db=db)
    assert es.delivery_outcome(result)["status"] == "disabled"
    assert _messages(db, to) == []


@pytest.mark.security
def test_a_hand_built_smtp_config_is_refused(db):
    """Only a sender decision is accepted. A dict of SMTP settings — the old way —
    would otherwise be a way around the sender policy."""
    from src.services import email_service as es

    to = f"raw_{uuid.uuid4().hex[:6]}@example.com"
    raw = {"host": "smtp.example.com", "port": 587, "user": "x@y.z", "password": "pw",
           "from_email": "x@y.z"}
    result = es.dispatch(to, "S", "<p>b</p>", smtp_config=raw, db=db)
    assert es.delivery_outcome(result)["status"] == "blocked"
    assert _messages(db, to) == []


@pytest.mark.security
def test_dispatch_without_a_session_commits_its_own(db):
    """Notification helpers call dispatch(db=None): the message must still be
    recorded (and committed) rather than lost with no session to write to."""
    from src.services import email_service as es

    to = f"nodb_{uuid.uuid4().hex[:6]}@example.com"
    cid = _make_company(db, "NoDb Co")
    _sender(db, cid)
    db.commit()
    result = es.dispatch(to, "S", "<p>b</p>",
                         smtp_config=es.resolve_sender(db, cid, event_type="offer_letter"))
    db.expire_all()
    [row] = _messages(db, to)
    assert row.id == result["message_id"] and row.status == "queued"


@pytest.mark.security
def test_worker_updates_the_queued_row_instead_of_adding_a_second(db):
    """finalize() on an existing id must UPDATE, not INSERT."""
    to = f"upd_{uuid.uuid4().hex[:6]}@example.com"
    log_id = email_log.new_log_id()

    email_log.record(db, log_id=log_id, to_email=to, subject="S",
                     status=email_log.QUEUED, commit=False)
    email_log.finalize(db, log_id, status=email_log.SENT, to_email=to,
                       attempt=2, commit=False)

    rows = _rows(db, to)
    assert len(rows) == 1, "worker inserted a duplicate instead of updating"
    assert rows[0].status == email_log.SENT
    assert rows[0].attempt == 2


@pytest.mark.security
def test_finalize_recreates_a_missing_row_rather_than_losing_the_outcome(db):
    to = f"gone_{uuid.uuid4().hex[:6]}@example.com"
    email_log.finalize(db, email_log.new_log_id(), status=email_log.FAILED,
                       to_email=to, error_type="tls", commit=False)

    rows = _rows(db, to)
    assert len(rows) == 1
    assert rows[0].status == email_log.FAILED


# ─────────────────────────────────────────────────────────────────────────────
# notify_candidate_rejected — the None-means-success trap
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_rejection_wrapper_reports_failure_instead_of_none(db):
    """It used to return None, and delivery_outcome(None) means "sent".

    So a caller that started reading the return would have been told every
    rejection succeeded — including the ones that never left.
    """
    from src.services import email_service as es

    class _Cand:
        id = "c1"
        email = "rejected@example.com"
        first_name, last_name = "Re", "Jected"
        company_id = None
        job_id = None

    with patch.object(es, "send_candidate_rejected",
                      return_value={"sent": False, "error": "nope",
                                    "error_type": "auth"}):
        outcome = es.notify_candidate_rejected(db, _Cand())

    assert outcome["ok"] is False
    assert outcome["status"] == "failed"

    # And a candidate with no address is reported as skipped, not as sent.
    class _NoEmail:
        id, email, company_id, job_id = "c2", None, None, None
    assert es.notify_candidate_rejected(db, _NoEmail())["ok"] is False


# ─────────────────────────────────────────────────────────────────────────────
# The read endpoint
# ─────────────────────────────────────────────────────────────────────────────

def _message(db, company_id, to, *, status="accepted", event="offer_letter", reason=None):
    from src.models.models import EmailMessage, EmailRecipient
    m = EmailMessage(company_id=company_id, event_type=event, status=status, subject="S",
                     status_reason=reason, from_email="mailbox@tenant.example")
    db.add(m)
    db.flush()
    db.add(EmailRecipient(company_id=company_id, message_id=m.id, email=to, kind="to"))
    db.flush()
    return m


@pytest.mark.security
def test_email_log_endpoint_is_company_scoped(client, db, two_companies_email_log):
    """Company A must not see company B's delivery history."""
    a, b = two_companies_email_log["a"], two_companies_email_log["b"]
    _message(db, a["company_id"], "a-side@example.com")
    _message(db, b["company_id"], "b-side@example.com")

    resp = client.get("/api/v1/hr/email-log", headers=a["hr"])
    assert resp.status_code == 200, resp.text
    addresses = {e["to_email"] for e in resp.json()["entries"]}
    assert "a-side@example.com" in addresses
    assert "b-side@example.com" not in addresses, "cross-tenant delivery history leaked"


@pytest.mark.security
def test_email_log_endpoint_filters_by_recipient_and_status(client, db,
                                                            two_companies_email_log):
    """The "did candidate X get their offer?" query."""
    a = two_companies_email_log["a"]
    target = f"target_{uuid.uuid4().hex[:6]}@example.com"
    _message(db, a["company_id"], target, status="blocked", reason="no_sender_configured")
    _message(db, a["company_id"], "someone-else@example.com")

    resp = client.get("/api/v1/hr/email-log", headers=a["hr"], params={"to_email": target})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["status"] == "blocked"
    assert entries[0]["error_type"] == "no_sender_configured"
    assert entries[0]["step_key"] == "offer_letter"
    assert entries[0]["sender_user"] == "mailbox@tenant.example"

    resp = client.get("/api/v1/hr/email-log", headers=a["hr"], params={"status": "accepted"})
    assert resp.json()["entries"] and all(e["status"] == "accepted"
                                          for e in resp.json()["entries"])

    resp = client.get("/api/v1/hr/email-log", headers=a["hr"], params={"since": "not-a-date"})
    assert resp.status_code == 400


@pytest.mark.security
def test_legacy_email_log_is_still_readable(client, db, two_companies_email_log):
    """For one release, history written before the upgrade stays reachable."""
    a, b = two_companies_email_log["a"], two_companies_email_log["b"]
    email_log.record(db, to_email="old-a@example.com", subject="A", status=email_log.SENT,
                     company_id=a["company_id"], commit=False)
    email_log.record(db, to_email="old-b@example.com", subject="B", status=email_log.SENT,
                     company_id=b["company_id"], commit=False)
    db.flush()
    resp = client.get("/api/v1/hr/email-log", headers=a["hr"], params={"source": "legacy"})
    addresses = {e["to_email"] for e in resp.json()["entries"]}
    assert addresses == {"old-a@example.com"}


@pytest.mark.security
def test_email_log_endpoint_rejects_candidates(client, two_companies_email_log):
    resp = client.get("/api/v1/hr/email-log",
                      headers=two_companies_email_log["a"]["candidate"])
    assert resp.status_code in (401, 403), resp.text


# ─────────────────────────────────────────────────────────────────────────────
# C4b — endpoints stop claiming a send that did not happen
# ─────────────────────────────────────────────────────────────────────────────

def _offer_ready_candidate(db, company_id):
    """A candidate on a job, far enough along that an offer can be created."""
    from src.models.models import Candidate, JobPosting
    job = JobPosting(id=str(uuid.uuid4()), company_id=company_id, title="Dev",
                     description="d", requirements="r", location="Remote",
                     employment_type="full_time", experience_years=0, status="active")
    db.add(job); db.flush()
    cand = Candidate(id=str(uuid.uuid4()), company_id=company_id, job_id=job.id,
                     email=f"offeree_{uuid.uuid4().hex[:6]}@example.com",
                     first_name="Off", last_name="Eree", current_stage="selected")
    db.add(cand); db.flush()
    return cand


@pytest.mark.security
def test_offer_endpoint_reports_a_failed_email_instead_of_success(
        client, db, two_companies_email_log):
    """It used to answer "Offer created and sent successfully" regardless.

    The send helpers report failure by return value, so the try/except around the
    discarded call never fired — a failed offer letter produced no log line at
    all, and the API said it had been sent.
    """
    from src.services import email_service as es

    a = two_companies_email_log["a"]
    cand = _offer_ready_candidate(db, a["company_id"])

    with patch.object(es, "send_offer_letter",
                      return_value={"sent": False, "error": "535 auth",
                                    "error_type": "auth"}):
        resp = client.post("/api/v1/hr/offers", headers=a["hr"], json={
            "candidate_id": cand.id, "offer_type": "full_time", "salary": 100000,
            "bonus": 0, "start_date": "2026-11-01", "expiry_date": "2026-10-01",
            "terms": "standard",
        })

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email_sent"] is False, "endpoint still claims the email was sent"
    assert data["email_status"] == "failed"
    assert "WARNING" in data["message"]
    # The offer itself still exists — only the notification failed.
    assert data["offer_id"]


@pytest.mark.security
def test_offer_endpoint_reports_queued_when_the_worker_will_send(
        client, db, two_companies_email_log):
    """Queued is not the same claim as sent, and must not be reported as one."""
    from src.services import email_service as es

    a = two_companies_email_log["a"]
    cand = _offer_ready_candidate(db, a["company_id"])

    with patch.object(es, "send_offer_letter",
                      return_value={"mode": "async", "job_id": "j1"}):
        resp = client.post("/api/v1/hr/offers", headers=a["hr"], json={
            "candidate_id": cand.id, "offer_type": "full_time", "salary": 100000,
            "bonus": 0, "start_date": "2026-11-01", "expiry_date": "2026-10-01",
            "terms": "standard",
        })

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["email_status"] == "queued"
    assert data["email_sent"] is True          # accepted for delivery
    assert "queued" in data["message"]
