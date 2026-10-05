"""The delivery worker: identity is frozen, failures block, ambiguity is never retried."""
from datetime import datetime, timedelta

import pytest

from src.email_api import constants as C
from src.email_api import limits, messages as M
from src.email_api.adapters import base as A
from src.email_api.worker import deliver_message, deliver_message_job
from src.models.models import (EmailAttempt, EmailOutbox, EmailRecipient, EmailRoute,
                               EmailSuppression)
from test.email_factories import (FakeAdapter, accepted, failing, make_company,
                                  make_smtp_sender, make_route, queue_message)


@pytest.fixture()
def co(db):
    c = make_company(db, "Client")
    c.sender, c.conn = make_smtp_sender(db, c.id, "HR", display_name="Client HR",
                                        reply_to="hr-replies@client.example")
    make_route(db, c.id, C.DEFAULT_ROUTE, c.sender.id)
    return c


@pytest.fixture()
def adapter(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(A, "get_adapter", lambda conn, is_test=False: fake)
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: False)
    # Real delivery mode, so the worker decrypts the credential as in production.
    monkeypatch.delenv("EMAIL_DELIVERY_MODE", raising=False)
    return fake


def _run(db, msg):
    out = deliver_message(msg.id, msg.company_id)
    db.expire_all()
    return out


def _box(db, msg):
    return db.query(EmailOutbox).filter_by(message_id=msg.id).one()


def test_accepted(db, co, adapter):
    msg = queue_message(db, co.id).message
    assert _run(db, msg)["status"] == C.ACCEPTED
    assert msg.status == C.ACCEPTED and msg.accepted_at and msg.attempt_count == 1
    snap = adapter.calls[0]["snapshot"]
    assert (snap.from_email, snap.from_name, snap.reply_to) == (
        co.sender.email, "Client HR", "hr-replies@client.example")
    assert adapter.calls[0]["secret"] == "app-pw"
    attempt = db.query(EmailAttempt).filter_by(message_id=msg.id).one()
    assert attempt.outcome == C.OUT_ACCEPTED and attempt.account_key == co.conn.account_key
    assert db.query(EmailRecipient).filter_by(message_id=msg.id).one().status == C.RCPT_ACCEPTED
    assert _box(db, msg).state == "done"
    assert co.conn.last_success_at is not None


def test_job_payload_is_ids_only(db, co, adapter):
    msg = queue_message(db, co.id).message
    assert deliver_message_job({"message_id": msg.id, "tenant_id": co.id})["status"] == C.ACCEPTED


def test_wrong_tenant_sends_nothing(db, co, adapter):
    other = make_company(db, "Other")
    msg = queue_message(db, co.id).message
    assert deliver_message(msg.id, other.id)["status"] == "not_found"
    assert adapter.calls == []


def test_route_change_after_queue_keeps_identity(db, co, adapter):
    msg = queue_message(db, co.id).message
    other, _ = make_smtp_sender(db, co.id, "Other")
    db.query(EmailRoute).filter_by(company_id=co.id, event_type=C.DEFAULT_ROUTE).one().sender_id = other.id
    db.flush()
    _run(db, msg)
    assert adapter.calls[0]["snapshot"].from_email == co.sender.email
    assert adapter.calls[0]["conn"] == co.conn.id


def test_sender_email_change_blocks(db, co, adapter):
    msg = queue_message(db, co.id).message
    co.sender.email = "someone-else@example.com"
    db.flush()
    assert _run(db, msg)["reason"] == C.R_SENDER_CHANGED
    assert msg.status == C.BLOCKED and adapter.calls == []
    assert _box(db, msg).state == "done"


def test_disabled_sender_blocks_without_fallback(db, co, adapter):
    msg = queue_message(db, co.id).message
    co.sender.enabled = False
    db.flush()
    _run(db, msg)
    assert msg.status == C.BLOCKED and msg.status_reason == C.R_SENDER_DISABLED
    assert adapter.calls == []


def test_auth_failure_blocks_and_fails_connection(db, co, adapter):
    adapter.respond = failing(C.OUT_BLOCKED, C.R_AUTH_FAILED, A.BLOCKED,
                              connection_status=C.CONN_FAILED)
    first = queue_message(db, co.id).message
    second = queue_message(db, co.id, subject="Another").message
    _run(db, first)
    assert first.status == C.BLOCKED and first.status_reason == C.R_AUTH_FAILED
    assert co.conn.status == C.CONN_FAILED and co.conn.error_code == C.R_AUTH_FAILED
    _run(db, second)
    # Never retried through any other sender: blocked before the adapter.
    assert second.status == C.BLOCKED and second.status_reason == C.R_CONNECTION_UNAVAILABLE
    assert len(adapter.calls) == 1


def test_temporary_failure_defers_then_gives_up(db, co, adapter):
    adapter.respond = failing(C.OUT_TEMP, "network_error", A.TEMPORARY)
    msg = queue_message(db, co.id).message
    _run(db, msg)
    assert msg.status == C.DEFERRED and msg.next_attempt_at > datetime.utcnow()
    box = _box(db, msg)
    assert box.state == "pending" and box.available_at == msg.next_attempt_at
    # Not due yet: nothing is sent.
    _run(db, msg)
    assert len(adapter.calls) == 1
    for _ in range(msg.max_attempts):
        msg.next_attempt_at = datetime.utcnow() - timedelta(seconds=1)
        db.flush()
        _run(db, msg)
    assert msg.status == C.FAILED and msg.status_reason == C.R_RETRIES_EXHAUSTED
    assert len(adapter.calls) == msg.max_attempts
    assert _box(db, msg).state == "done"


def test_throttling_waits_at_least_retry_after(db, co, adapter):
    adapter.respond = failing(C.OUT_TEMP, "throttled", A.THROTTLED, retry_after=900)
    msg = queue_message(db, co.id).message
    _run(db, msg)
    assert msg.status == C.DEFERRED
    assert msg.next_attempt_at >= datetime.utcnow() + timedelta(seconds=890)


def test_unknown_outcome_is_not_retried(db, co, adapter):
    adapter.respond = failing(C.OUT_UNKNOWN, "connection_lost_after_data", A.UNKNOWN)
    msg = queue_message(db, co.id).message
    _run(db, msg)
    assert msg.status == C.UNKNOWN and _box(db, msg).state == "done"
    _run(db, msg)
    assert len(adapter.calls) == 1
    with pytest.raises(M.StateError) as e:
        M.retry(db, msg)
    assert e.value.code == "duplicate_risk"
    M.retry(db, msg, confirm_duplicate_risk=True)
    db.flush()
    assert msg.status == C.QUEUED and _box(db, msg).state == "pending"


def test_expired_send_lease_becomes_unknown(db, co, adapter):
    msg = queue_message(db, co.id).message
    msg.status, msg.attempt_count = C.SENDING, 1
    msg.lease_expires_at = datetime.utcnow() - timedelta(seconds=5)
    db.flush()
    assert _run(db, msg)["status"] == C.UNKNOWN
    assert msg.status_reason == C.R_WORKER_LOST and adapter.calls == []


def test_live_send_lease_is_left_alone(db, co, adapter):
    msg = queue_message(db, co.id).message
    msg.status, msg.lease_expires_at = C.SENDING, datetime.utcnow() + timedelta(minutes=2)
    db.flush()
    assert _run(db, msg)["status"] == "in_progress" and adapter.calls == []


def test_rate_limit_defers_to_next_window(db, co, adapter, monkeypatch):
    monkeypatch.setattr(limits, "reserve", lambda *a, **k: None)
    msg = queue_message(db, co.id).message
    _run(db, msg)
    assert msg.status == C.DEFERRED and msg.status_reason == C.R_RATE_LIMITED
    assert adapter.calls == [] and _box(db, msg).state == "pending"


def test_db_rate_limit_fallback_counts_attempts(db, co, adapter):
    co.conn.hourly_limit = 5
    db.flush()
    msgs = [queue_message(db, co.id, subject=f"m{i}").message for i in range(5)]
    for m in msgs:
        _run(db, m)
    # Without Redis the hour's attempts are counted from the database. 5 minus
    # the 20% OTP headroom leaves four normal sends.
    assert [m.status for m in msgs] == [C.ACCEPTED] * 4 + [C.DEFERRED]
    assert msgs[-1].status_reason == C.R_RATE_LIMITED


def test_auth_priority_uses_headroom(db, co, adapter):
    co.conn.hourly_limit = 5
    db.flush()
    normal = [queue_message(db, co.id, subject=f"m{i}").message for i in range(4)]
    for m in normal:
        _run(db, m)
    otp = queue_message(db, co.id, subject="Code", priority=C.PRIORITY_AUTH, is_sensitive=True).message
    late = queue_message(db, co.id, subject="late").message
    _run(db, otp)
    _run(db, late)
    assert otp.status == C.ACCEPTED and late.status == C.DEFERRED


def test_suppressed_recipients_are_skipped(db, co, adapter):
    db.add(EmailSuppression(company_id=co.id, email="gone@example.net", reason="hard_bounce",
                            source="smtp_5xx"))
    db.flush()
    msg = queue_message(db, co.id, to=["cand@example.net"], cc=["gone@example.net"]).message
    _run(db, msg)
    assert adapter.calls[0]["snapshot"].envelope_recipients == ["cand@example.net"]
    statuses = {r.email: r.status for r in db.query(EmailRecipient).filter_by(message_id=msg.id)}
    assert statuses == {"cand@example.net": C.RCPT_ACCEPTED, "gone@example.net": C.RCPT_SUPPRESSED}
    only = queue_message(db, co.id, to=["gone@example.net"], subject="x").message
    _run(db, only)
    assert only.status == C.BLOCKED and only.status_reason == C.R_ALL_SUPPRESSED


def test_suppressions_are_per_company(db, co, adapter):
    other = make_company(db, "Other")
    db.add(EmailSuppression(company_id=other.id, email="cand@example.net", reason="complaint",
                            source="admin"))
    db.flush()
    msg = queue_message(db, co.id).message
    _run(db, msg)
    assert msg.status == C.ACCEPTED


def test_hard_bounce_adds_suppression(db, co, adapter):
    from src.email_api.adapters.base import SubmissionResult, RecipientResult

    def respond(snapshot):
        return SubmissionResult(C.OUT_ACCEPTED, "done", [
            RecipientResult("cand@example.net", C.RCPT_ACCEPTED, 250, "OK"),
            RecipientResult("gone@example.net", C.RCPT_FAILED, 550, "5.1.1 unknown", suppress=True)])
    adapter.respond = respond
    msg = queue_message(db, co.id, to=["cand@example.net", "gone@example.net"]).message
    _run(db, msg)
    assert msg.status == C.ACCEPTED
    assert db.query(EmailSuppression).filter_by(company_id=co.id, email="gone@example.net").count() == 1


def test_otp_content_purged_when_finished(db, co, adapter):
    msg = queue_message(db, co.id, subject="Your code", html="<p>123456</p>",
                        priority=C.PRIORITY_AUTH, is_sensitive=True).message
    _run(db, msg)
    assert adapter.calls[0]["snapshot"].auto_submitted
    assert msg.content_enc is None and msg.content_purged_at is not None


def test_delivery_paused_defers(db, co, adapter, monkeypatch):
    monkeypatch.setenv("EMAIL_ENABLED", "false")
    msg = queue_message(db, co.id).message
    _run(db, msg)
    assert msg.status == C.DEFERRED and msg.status_reason == C.R_DELIVERY_PAUSED
    assert adapter.calls == []


def test_unreadable_credential_blocks(db, co, adapter):
    co.conn.credential_enc = "k1$not-a-token"
    db.flush()
    msg = queue_message(db, co.id).message
    _run(db, msg)
    assert msg.status == C.BLOCKED and msg.status_reason == C.R_CREDENTIAL_UNREADABLE
    assert co.conn.status == C.CONN_NEEDS_ATTENTION and adapter.calls == []


def test_test_mode_never_reaches_a_server(db, co, monkeypatch):
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: False)
    calls = []
    monkeypatch.setattr("src.email_api.adapters.smtp.SMTPAdapter.send",
                        lambda *a, **k: calls.append(a))
    msg = queue_message(db, co.id, is_test=True, to=["fail+x@example.net"]).message
    _run(db, msg)
    assert calls == [] and msg.status == C.FAILED


def test_cancel(db, co, adapter):
    msg = queue_message(db, co.id).message
    M.cancel(db, msg)
    assert msg.status == C.CANCELLED and _box(db, msg).state == "done"
    _run(db, msg)
    assert adapter.calls == []
    sent = queue_message(db, co.id, subject="x").message
    _run(db, sent)
    with pytest.raises(M.StateError):
        M.cancel(db, sent)


def test_retry_blocked_message_after_fixing_configuration(db, adapter):
    c = make_company(db, "Late")
    msg = queue_message(db, c.id).message
    assert msg.status == C.BLOCKED
    with pytest.raises(M.StateError):
        M.retry(db, msg)  # still nothing configured
    s, _ = make_smtp_sender(db, c.id)
    make_route(db, c.id, C.DEFAULT_ROUTE, s.id)
    M.retry(db, msg)
    db.flush()
    assert msg.status == C.QUEUED and msg.sender_id == s.id
    _run(db, msg)
    assert msg.status == C.ACCEPTED
