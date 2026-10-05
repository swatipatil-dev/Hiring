"""Deliver one queued message.

``deliver_message(message_id, tenant_id)`` is the only code that sends API mail.
It runs in the RQ worker, and in-process for auth mail while Redis is down (and
under ``EMAIL_SYNC``). Every query names the company explicitly; the tenant
contextvar is never bound here (a long-lived worker would leak it from one job
into the next) and is bypassed when this runs inside a request, because a
platform-auth message legitimately uses the platform's sender.

The sequence, and why:

1. **Claim** the message row under a lock. Finished messages are left alone; a
   message another worker is sending is left alone; one whose sender lease ran
   out is marked ``unknown`` — the worker died mid-send, so the mail may or may
   not have gone, and only a person may decide to send it again.
2. **Revalidate** the frozen sender and connection. Anything changed or broken
   blocks the message; nothing falls back to another sender.
3. **Rate limit**; a full window defers to the next one.
4. Mark it ``sending`` with a lease and **commit**, so no lock is held while
   talking to a mail server.
5. **Decrypt** the credential immediately before the adapter call.
6. **Record** the outcome, guarded so a stale worker cannot overwrite a newer
   state, and settle the outbox row (done, or pending again with a time).
"""
from __future__ import annotations

import json
import logging
import os
import random
import socket
from datetime import datetime, timedelta
from typing import Optional

from . import constants as C
from . import crypto, limits
from . import db as db_mod
from .adapters import base as A
from .connections import mark_failed
from .senders import usable_reason

logger = logging.getLogger(__name__)

SEND_LEASE_S = int(os.getenv("EMAIL_SEND_LEASE_S", "180"))
PAUSE_RETRY_S = 900


def _email_enabled() -> bool:
    return os.getenv("EMAIL_ENABLED", "true").lower() == "true"


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def backoff_seconds(attempt: int, error: Optional[A.DeliveryError] = None) -> int:
    """Exponential backoff with jitter; throttling waits at least what was asked."""
    base = min(60 * (2 ** max(0, attempt - 1)), 3600)
    delay = int(base * random.uniform(0.5, 1.0))
    if error is not None and error.kind == A.THROTTLED:
        delay = max(delay, int(error.retry_after or 900))
    return max(delay, 1)


def deliver_message_job(payload: dict) -> dict:
    """RQ entry point. The payload carries ids only — see outbox._enqueue."""
    return deliver_message(str(payload["message_id"]), str(payload["tenant_id"]))


def deliver_message(message_id: str, tenant_id: str, worker_id: Optional[str] = None) -> dict:
    from src.tenancy import tenant_bypass
    session = db_mod.open_session()
    try:
        with tenant_bypass():
            return _deliver(session, message_id, tenant_id, worker_id or _worker_id())
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ── helpers ────────────────────────────────────────────────────────────────

def _load(session, message_id, tenant_id, lock=False):
    from src.models.models import EmailMessage
    q = session.query(EmailMessage).filter(EmailMessage.id == message_id,
                                           EmailMessage.company_id == tenant_id)
    return q.with_for_update().first() if lock else q.first()


def _outbox(session, message_id, tenant_id):
    from src.models.models import EmailOutbox
    return (session.query(EmailOutbox)
            .filter(EmailOutbox.message_id == message_id, EmailOutbox.company_id == tenant_id)
            .first())


def _settle_outbox(box, *, done: bool, at: Optional[datetime] = None) -> None:
    if box is None:
        return
    now = datetime.utcnow()
    if done:
        box.state = "done"
    else:
        box.state, box.available_at = "pending", at or now
    box.lease_owner = box.lease_expires_at = None
    box.updated_at = now


def _finish(msg, status: str, reason: Optional[str] = None, detail: Optional[str] = None) -> None:
    now = datetime.utcnow()
    msg.status, msg.status_reason = status, reason
    msg.status_detail = (detail or "")[:500] or None
    msg.lease_expires_at = None
    msg.updated_at = now
    if status in C.TERMINAL_STATES:
        msg.completed_at = now
        if status == C.ACCEPTED:
            msg.accepted_at = now
        if msg.is_sensitive and msg.content_enc is not None:
            # OTP and reset codes are not kept once they cannot be sent again.
            msg.content_enc = None
            msg.content_purged_at = now


def _defer(msg, box, delay_s: int, reason: str, detail: Optional[str] = None) -> None:
    at = datetime.utcnow() + timedelta(seconds=delay_s)
    _finish(msg, C.DEFERRED, reason, detail)
    msg.next_attempt_at = at
    _settle_outbox(box, done=False, at=at)


def _recipients(session, msg):
    from src.models.models import EmailRecipient
    return (session.query(EmailRecipient)
            .filter(EmailRecipient.message_id == msg.id, EmailRecipient.company_id == msg.company_id)
            .order_by(EmailRecipient.kind)
            .all())


def _suppressed(session, company_id, emails) -> set:
    from src.models.models import EmailSuppression
    if not emails:
        return set()
    rows = (session.query(EmailSuppression.email)
            .filter(EmailSuppression.company_id == company_id,
                    EmailSuppression.email.in_([e.lower() for e in emails]))
            .all())
    return {r[0] for r in rows}


def _snapshot(msg, rcpts) -> A.Snapshot:
    content = json.loads(crypto.decrypt_content(msg.content_enc))
    pick = lambda kind: tuple(r.email for r in rcpts if r.kind == kind)  # noqa: E731
    return A.Snapshot(
        from_email=msg.from_email, from_name=msg.from_name, subject=msg.subject,
        html=content.get("html") or "", text=content.get("text"),
        to=pick("to"), cc=pick("cc"), bcc=pick("bcc"), reply_to=msg.reply_to,
        message_id_header=msg.message_id_header,
        auto_submitted=msg.priority == C.PRIORITY_AUTH,
        unsubscribe_to=content.get("unsubscribe_to"),
    )


# ── the job ────────────────────────────────────────────────────────────────

def _deliver(session, message_id: str, tenant_id: str, worker_id: str) -> dict:
    from src.models.models import EmailAttempt
    from .senders import load as load_sender
    now = datetime.utcnow()

    # 1. Claim.
    msg = _load(session, message_id, tenant_id, lock=True)
    if msg is None:
        logger.warning("[EMAIL WORKER] no message %s for company %s", message_id, tenant_id)
        session.rollback()
        return {"status": "not_found"}
    box = _outbox(session, message_id, tenant_id)
    if msg.status in C.TERMINAL_STATES:
        _settle_outbox(box, done=True)
        session.commit()
        return {"status": msg.status}
    if msg.status == C.SENDING:
        if msg.lease_expires_at and msg.lease_expires_at > now:
            session.rollback()
            return {"status": "in_progress"}
        _finish(msg, C.UNKNOWN, C.R_WORKER_LOST,
                "The worker stopped while sending; the server may have accepted it")
        for r in _recipients(session, msg):
            if r.status == C.RCPT_PENDING:
                r.status = C.RCPT_UNKNOWN
        _settle_outbox(box, done=True)
        session.commit()
        return {"status": C.UNKNOWN}
    if msg.next_attempt_at and msg.next_attempt_at > now:
        _settle_outbox(box, done=False, at=msg.next_attempt_at)
        session.commit()
        return {"status": msg.status}

    # 2. Revalidate the identity frozen at submit.
    sender, conn = load_sender(session, msg.sender_company_id, msg.sender_id)
    reason = usable_reason(sender, conn)
    if reason is None and (sender.email != msg.from_email or conn.id != msg.connection_id):
        reason = C.R_SENDER_CHANGED
    if reason is None and msg.sender_company_id != msg.company_id \
            and msg.route_source != C.SRC_PLATFORM_AUTH:
        reason = C.R_SENDER_NOT_FOUND
    if reason:
        _finish(msg, C.BLOCKED, reason)
        _settle_outbox(box, done=True)
        session.commit()
        return {"status": C.BLOCKED, "reason": reason}
    if not _email_enabled():
        _defer(msg, box, PAUSE_RETRY_S, C.R_DELIVERY_PAUSED, "EMAIL_ENABLED is false")
        session.commit()
        return {"status": C.DEFERRED, "reason": C.R_DELIVERY_PAUSED}

    rcpts = [r for r in _recipients(session, msg) if r.status in (C.RCPT_PENDING, C.RCPT_DEFERRED)]
    blocked = _suppressed(session, msg.company_id, [r.email for r in rcpts])
    for r in rcpts:
        if r.email.lower() in blocked:
            r.status = C.RCPT_SUPPRESSED
    rcpts = [r for r in rcpts if r.status != C.RCPT_SUPPRESSED]
    if not rcpts or not any(r.kind == "to" for r in rcpts):
        _finish(msg, C.BLOCKED, C.R_ALL_SUPPRESSED)
        _settle_outbox(box, done=True)
        session.commit()
        return {"status": C.BLOCKED, "reason": C.R_ALL_SUPPRESSED}

    # 3. Rate limit.
    claimed = limits.reserve(session, conn.account_key, sender.hourly_limit or conn.hourly_limit,
                             company_id=msg.company_id, priority=msg.priority == C.PRIORITY_AUTH,
                             shared=bool(conn.is_shared))
    if claimed is None:
        delay = limits.seconds_to_next_window() + random.randint(5, 120)
        _defer(msg, box, delay, C.R_RATE_LIMITED)
        session.commit()
        return {"status": C.DEFERRED, "reason": C.R_RATE_LIMITED}

    # 4. Lease and commit.
    attempt_no = (msg.attempt_count or 0) + 1
    msg.status, msg.status_reason, msg.status_detail = C.SENDING, None, None
    msg.attempt_count = attempt_no
    msg.lease_expires_at = now + timedelta(seconds=SEND_LEASE_S)
    msg.next_attempt_at = None
    msg.updated_at = now
    if box is not None:
        box.lease_expires_at = msg.lease_expires_at
    attempt = EmailAttempt(company_id=msg.company_id, message_id=msg.id, attempt_no=attempt_no,
                           connection_id=conn.id, account_key=conn.account_key,
                           worker_id=worker_id[:100], started_at=now)
    session.add(attempt)
    snapshot_rcpts = list(rcpts)
    session.commit()

    # 5. Send.
    result = _send(conn, msg, snapshot_rcpts)
    if result.outcome not in (C.OUT_ACCEPTED, C.OUT_UNKNOWN):
        limits.release(claimed)

    # 6. Record.
    return _record(session, message_id, tenant_id, attempt_no, result)


def _send(conn, msg, rcpts) -> A.SubmissionResult:
    try:
        snapshot = _snapshot(msg, rcpts)
    except crypto.CredentialUnavailable as e:
        return A.SubmissionResult(C.OUT_BLOCKED, "connect", error=A.DeliveryError(
            "content_unreadable", A.BLOCKED, str(e)))
    try:
        adapter = A.get_adapter(conn, msg.is_test)
    except A.UnsupportedConnection:
        return A.SubmissionResult(C.OUT_BLOCKED, "connect", error=A.DeliveryError(
            C.R_CONNECTION_TYPE_UNSUPPORTED, A.BLOCKED, conn.type))
    secret = ""
    if not msg.is_test and A.delivery_mode() != "null":
        try:
            secret = crypto.decrypt_secret(conn.credential_enc, conn.credential_key_version)
        except crypto.CredentialUnavailable as e:
            return A.SubmissionResult(C.OUT_BLOCKED, "connect", error=A.DeliveryError(
                C.R_CREDENTIAL_UNREADABLE, A.BLOCKED, str(e),
                connection_status=C.CONN_NEEDS_ATTENTION))
    try:
        return adapter.send(conn, snapshot, secret)
    except Exception as e:  # noqa: BLE001 — an adapter bug; the mail may have gone
        logger.exception("[EMAIL WORKER] adapter raised for %s", msg.id)
        return A.SubmissionResult(C.OUT_UNKNOWN, "unknown", error=A.DeliveryError(
            "adapter_error", A.UNKNOWN, f"{type(e).__name__}: {e}"[:500]))
    finally:
        secret = None  # noqa: F841 — drop the reference as soon as possible


def _record(session, message_id, tenant_id, attempt_no, result: A.SubmissionResult) -> dict:
    from src.models.models import EmailAttempt, EmailSuppression, EmailConnection
    now = datetime.utcnow()
    err = result.error
    attempt = (session.query(EmailAttempt)
               .filter(EmailAttempt.message_id == message_id, EmailAttempt.company_id == tenant_id,
                       EmailAttempt.attempt_no == attempt_no)
               .first())
    if attempt is not None:
        attempt.finished_at = now
        attempt.duration_ms = int((now - attempt.started_at).total_seconds() * 1000) \
            if attempt.started_at else None
        attempt.phase_reached = (result.phase_reached or "")[:10] or None
        attempt.outcome = result.outcome
        attempt.error_code = err.code if err else None
        attempt.smtp_code = result.smtp_code or (err.smtp_code if err else None)
        attempt.provider_response = ((result.provider_response or (err.detail if err else "")) or "")[:1000] or None

    msg = _load(session, message_id, tenant_id, lock=True)
    if msg is None or msg.status != C.SENDING or msg.attempt_count != attempt_no:
        # Someone else already settled this message (e.g. a lease expiry marked
        # it unknown). Keep the attempt record; do not overwrite their state.
        session.commit()
        return {"status": msg.status if msg else "not_found", "stale": True}

    by_email = {r.email.lower(): r for r in result.recipients}
    for r in _recipients(session, msg):
        res = by_email.get(r.email.lower())
        if res is None:
            continue
        r.status, r.smtp_code = res.status, res.smtp_code
        r.smtp_response = (res.smtp_response or "")[:500] or None
        r.updated_at = now
        if res.suppress:
            exists = (session.query(EmailSuppression.id)
                      .filter(EmailSuppression.company_id == msg.company_id,
                              EmailSuppression.email == r.email.lower())
                      .first())
            if not exists:
                session.add(EmailSuppression(company_id=msg.company_id, email=r.email.lower(),
                                             reason="hard_bounce", source="smtp_5xx"))

    if err is not None and err.connection_status:
        conn = (session.query(EmailConnection)
                .filter(EmailConnection.company_id == msg.sender_company_id,
                        EmailConnection.id == msg.connection_id)
                .first())
        if conn is not None:
            mark_failed(conn, err, now)
            logger.error("[EMAIL WORKER] connection %s marked %s: %s", conn.id, conn.status, err.code)

    box = _outbox(session, message_id, tenant_id)
    outcome = result.outcome
    if outcome == C.OUT_ACCEPTED:
        _finish(msg, C.ACCEPTED)
        _settle_outbox(box, done=True)
        conn = (session.query(EmailConnection)
                .filter(EmailConnection.company_id == msg.sender_company_id,
                        EmailConnection.id == msg.connection_id)
                .first())
        if conn is not None:
            conn.last_success_at = now
    elif outcome == C.OUT_UNKNOWN:
        _finish(msg, C.UNKNOWN, err.code if err else C.UNKNOWN, err.detail if err else None)
        _settle_outbox(box, done=True)
    elif outcome == C.OUT_BLOCKED:
        _finish(msg, C.BLOCKED, err.code if err else C.BLOCKED, err.detail if err else None)
        _settle_outbox(box, done=True)
    elif outcome == C.OUT_PERM:
        _finish(msg, C.FAILED, err.code if err else C.FAILED, err.detail if err else None)
        _settle_outbox(box, done=True)
    elif msg.attempt_count >= (msg.max_attempts or 1):
        _finish(msg, C.FAILED, C.R_RETRIES_EXHAUSTED, err.detail if err else None)
        _settle_outbox(box, done=True)
    else:
        _defer(msg, box, backoff_seconds(msg.attempt_count, err),
               err.code if err else "temporary_failure", err.detail if err else None)
        for r in _recipients(session, msg):
            if r.status == C.RCPT_DEFERRED:
                r.status = C.RCPT_PENDING
    session.commit()
    logger.info("[EMAIL WORKER] %s -> %s (%s)", message_id, msg.status, msg.status_reason or "-")
    return {"status": msg.status, "reason": msg.status_reason}
