"""Delivery is recorded, and endpoints stop claiming sends that did not happen.

The send helpers report failure by return value rather than by raising, so the
prevailing `try/except Exception` around a discarded return caught nothing that
actually occurred — a failed offer letter produced no row, no log line, and a
response saying it had been sent. These tests pin both halves of the fix: the
email_log row, and the honest response.
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
# The writer
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.security
def test_failed_inline_send_is_recorded(db):
    """A send that fails must leave a row saying so."""
    from src.services import email_service as es

    to = f"fail_{uuid.uuid4().hex[:6]}@example.com"
    with patch.object(es, "EMAIL_ENABLED", True), \
         patch.object(es, "_deliver", return_value={
             "sent": False, "error": "535 auth", "error_type": "auth"}):
        result = es._send(to, "Subject", "<p>b</p>", smtp_config=_cfg(), db=db)

    assert result["sent"] is False
    rows = _rows(db, to)
    assert len(rows) == 1
    assert rows[0].status == email_log.FAILED
    assert rows[0].error_type == "auth"
    assert rows[0].sender_user == "mailbox@tenant.com"


@pytest.mark.security
def test_successful_inline_send_is_recorded_with_context(db):
    from src.services import email_service as es

    to = f"ok_{uuid.uuid4().hex[:6]}@example.com"
    cid = _make_company(db, "Ctx Co")
    with patch.object(es, "EMAIL_ENABLED", True), \
         patch.object(es, "_deliver", return_value={
             "sent": True, "error": None, "error_type": None}):
        es._send(to, "Subject", "<p>b</p>",
                 smtp_config=_cfg(company_id=cid, step_key="offer_letter"), db=db)

    row = _rows(db, to)[0]
    assert row.status == email_log.SENT
    # company_id/step_key ride in smtp_config; without them the row would be
    # invisible to the tenant-scoped listing endpoint.
    assert row.company_id == cid
    assert row.step_key == "offer_letter"


@pytest.mark.security
def test_queued_row_is_written_before_the_worker_runs(db):
    """A worker that never runs must still leave evidence of the attempt."""
    from src.services import email_service as es

    to = f"queued_{uuid.uuid4().hex[:6]}@example.com"
    cid = _make_company(db, "Queued Co")
    with patch("src.mailq.queue.enqueue_email",
               return_value={"mode": "async", "job_id": "job-1"}):
        result = es.dispatch(to, "Subject", "<p>b</p>",
                             smtp_config=_cfg(company_id=cid, step_key="offer_letter"),
                             db=db)

    assert result["mode"] == "async"
    row = _rows(db, to)[0]
    assert row.status == email_log.QUEUED
    assert row.company_id == cid


@pytest.mark.security
def test_inline_fallback_updates_the_queued_row_on_the_callers_session(db):
    """With Redis down the job runs inside the request that flushed the queued
    row. A second session would block on that uncommitted insert until
    innodb_lock_wait_timeout — 50s per email — and then drop the outcome."""
    from src.services import email_service as es
    from src.mailq import queue as mq, ratelimit

    to = f"inline_{uuid.uuid4().hex[:6]}@example.com"
    cid = _make_company(db, "Inline Co")
    with patch.object(mq, "redis_available", return_value=False), \
         patch.object(ratelimit, "get_redis", return_value=None), \
         patch.object(es, "_deliver", return_value={
             "sent": True, "error": None, "error_type": None}), \
         patch.object(email_log, "_open_session",
                      side_effect=AssertionError("inline send opened its own session")):
        result = es.dispatch(to, "Subject", "<p>b</p>",
                             smtp_config=_cfg(company_id=cid), db=db)

    assert result["mode"] == "sync"
    rows = _rows(db, to)
    assert len(rows) == 1, "inline send added a second row"
    assert rows[0].status == email_log.SENT


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


@pytest.mark.security
def test_a_logging_failure_cannot_fail_a_send(db):
    """Bookkeeping must never be the reason a message fails to send."""
    from src.services import email_service as es

    to = f"boom_{uuid.uuid4().hex[:6]}@example.com"
    with patch.object(es, "EMAIL_ENABLED", True), \
         patch.object(es, "_deliver", return_value={
             "sent": True, "error": None, "error_type": None}), \
         patch.object(email_log, "finalize", side_effect=RuntimeError("db down")):
        result = es._send(to, "Subject", "<p>b</p>", smtp_config=_cfg(), db=db)

    assert result["sent"] is True, "a logging error broke the send"


@pytest.mark.security
def test_no_session_and_no_log_id_writes_nothing(db):
    """_send must not open a private session — it would escape the caller's
    transaction and, locally, target a different database entirely."""
    from src.services import email_service as es

    to = f"nodb_{uuid.uuid4().hex[:6]}@example.com"
    with patch.object(es, "EMAIL_ENABLED", True), \
         patch.object(es, "_deliver", return_value={"sent": True, "error": None,
                                                    "error_type": None}), \
         patch.object(email_log, "_open_session",
                      side_effect=AssertionError("must not open its own session")):
        es._send(to, "Subject", "<p>b</p>", smtp_config=_cfg())

    assert _rows(db, to) == []


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

@pytest.mark.security
def test_email_log_endpoint_is_company_scoped(client, db, two_companies_email_log):
    """Company A must not see company B's delivery history."""
    a, b = two_companies_email_log["a"], two_companies_email_log["b"]

    email_log.record(db, to_email="a-side@example.com", subject="A",
                     status=email_log.SENT, company_id=a["company_id"], commit=False)
    email_log.record(db, to_email="b-side@example.com", subject="B",
                     status=email_log.SENT, company_id=b["company_id"], commit=False)
    db.flush()

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

    email_log.record(db, to_email=target, subject="Offer", status=email_log.FAILED,
                     error_type="auth", step_key="offer_letter",
                     company_id=a["company_id"], commit=False)
    email_log.record(db, to_email="someone-else@example.com", subject="Other",
                     status=email_log.SENT, company_id=a["company_id"], commit=False)
    db.flush()

    resp = client.get("/api/v1/hr/email-log", headers=a["hr"],
                      params={"to_email": target})
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert len(entries) == 1
    assert entries[0]["status"] == "failed"
    assert entries[0]["error_type"] == "auth"
    assert entries[0]["step_key"] == "offer_letter"

    resp = client.get("/api/v1/hr/email-log", headers=a["hr"],
                      params={"status": "sent"})
    assert all(e["status"] == "sent" for e in resp.json()["entries"])

    resp = client.get("/api/v1/hr/email-log", headers=a["hr"],
                      params={"since": "not-a-date"})
    assert resp.status_code == 400


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


# ─────────────────────────────────────────────────────────────────────────────
# The worker path
#
# The worker opens its own session, so these assert the wiring rather than
# standing up a second real one (same approach as test_queue_redis_serialization).
# ─────────────────────────────────────────────────────────────────────────────

def _payload(**over):
    p = {"to": "w@example.com", "subject": "S", "body": "<p>b</p>",
         "sender": {"user": "mailbox@tenant.com", "host": "h", "port": 587,
                    "from_email": "mailbox@tenant.com", "password_enc": ""},
         "log_id": "log-1", "company_id": "co-1", "step_key": "offer_letter",
         "hourly_limit": None}
    p.update(over)
    return p


@pytest.mark.security
def test_worker_records_company_id_explicitly():
    """Nothing binds the tenant contextvar in the worker.

    before_flush therefore stamps nothing, so a row written without an explicit
    company_id lands NULL and becomes invisible to every tenant-scoped read —
    including the email-log endpoint that exists to answer "did X get it?".
    """
    from src.mailq import worker

    seen = {}
    with patch.object(email_log, "finalize",
                      side_effect=lambda db, log_id, **kw: seen.update(
                          db=db, log_id=log_id, **kw)):
        worker._log(_payload(), "sent", 0, {"error": None, "error_type": None})

    assert seen["company_id"] == "co-1", "worker wrote a NULL-company row"
    assert seen["step_key"] == "offer_letter"
    assert seen["log_id"] == "log-1"
    assert seen["db"] is None, "worker must open its own session, not borrow one"
    assert seen["sender_user"] == "mailbox@tenant.com"


@pytest.mark.security
def test_worker_log_failure_never_propagates():
    from src.mailq import worker

    with patch.object(email_log, "finalize", side_effect=RuntimeError("db down")):
        worker._log(_payload(), "sent", 0, {})   # must not raise


@pytest.mark.security
def test_worker_marks_a_rate_limit_hold_as_deferred(monkeypatch):
    """A deferral is a hold, not a delivery and not a loss."""
    from src.mailq import worker, ratelimit

    monkeypatch.setattr(ratelimit, "reserve", lambda *a, **k: False)
    monkeypatch.setattr(worker, "_reschedule", lambda *a, **k: None)

    statuses = []
    monkeypatch.setattr(worker, "_log",
                        lambda payload, status, attempt, result=None, db=None:
                        statuses.append(status))

    out = worker.send_email_job(_payload())
    assert out.get("deferred") is True
    assert statuses == ["deferred"]
