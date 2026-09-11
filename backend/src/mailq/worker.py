"""RQ job that sends one queued email.

Honors the per-sender hourly limit (defers instead of failing when full) and
retries transient SMTP/network errors with exponential backoff. Auth/disabled
errors are not retried — they stay in RQ's failure registry for inspection.
Delayed re-enqueues need the RQ scheduler (run the worker with --with-scheduler).
"""
from __future__ import annotations

import os
import logging
from datetime import timedelta

logger = logging.getLogger(__name__)

MAX_RETRIES = int(os.getenv("EMAIL_MAX_RETRIES", "5"))
_QUEUE_NAME = os.getenv("EMAIL_QUEUE", "email")


def _decrypt_password(sender: dict) -> str:
    enc = sender.get("password_enc")
    if enc:
        try:
            from src.security import decrypt
            return decrypt(enc) or ""
        except Exception:
            return ""
    return sender.get("password", "") or ""


def _smtp_config_from(sender: dict):
    if not sender or not sender.get("user"):
        return None
    return {
        "host": sender.get("host"),
        "port": sender.get("port"),
        "user": sender.get("user"),
        "password": _decrypt_password(sender),
        "from_email": sender.get("from_email"),
    }


def _log(payload: dict, status: str, attempt: int, result: dict | None = None) -> None:
    """Update this message's delivery row. Opens its own session; never raises.

    company_id comes from the payload, not from the tenant contextvar: nothing is
    bound in the worker, so before_flush would leave it NULL and the row would be
    invisible to every tenant-scoped read. Deliberately does NOT bind the
    contextvar — an RQ SimpleWorker runs every job in one long-lived process, so
    a missed reset would leak one tenant's scope into the next job.
    """
    result = result or {}
    try:
        from src.services import email_log
        sender = payload.get("sender") or {}
        email_log.finalize(
            None, payload.get("log_id"), status=status,
            to_email=payload.get("to"), subject=payload.get("subject"),
            error=result.get("error"), error_type=result.get("error_type"),
            attempt=attempt, sender_user=sender.get("user"),
            company_id=payload.get("company_id"), step_key=payload.get("step_key"),
        )
    except Exception as e:
        logger.warning(f"[EMAIL QUEUE] delivery log write failed for {payload.get('to')}: {e}")


def _reschedule(payload: dict, attempt: int, delay: int, reason: str) -> None:
    from .queue import get_redis_raw, redis_available
    if not redis_available():
        return
    try:
        from rq import Queue
        q = Queue(_QUEUE_NAME, connection=get_redis_raw())
        q.enqueue_in(
            timedelta(seconds=int(delay)),
            "src.mailq.worker.send_email_job",
            payload, attempt,
            job_timeout=60, failure_ttl=86400,
        )
        logger.info(
            f"[EMAIL QUEUE] {reason}: to={payload.get('to')} "
            f"deferred {int(delay)}s (attempt {attempt})"
        )
    except Exception as e:
        logger.warning(f"[EMAIL QUEUE] reschedule failed for {payload.get('to')}: {e}")


def send_email_job(payload: dict, attempt: int = 0) -> dict:
    """Send one email, applying the hourly cap (defer) and retry-on-transient."""
    from . import ratelimit
    from src.services.email_service import _send

    sender = payload.get("sender") or {}
    account = sender.get("user") or "__default__"
    limit = payload.get("hourly_limit")

    # 1. Rate gate — defer (re-schedule) rather than fail when the window is full.
    if not ratelimit.reserve(account, limit):
        _reschedule(payload, attempt, ratelimit.seconds_to_next_window(), "rate-limited")
        _log(payload, "deferred", attempt)
        return {"deferred": True, "account": account}

    # 2. Send.
    result = _send(
        payload["to"], payload["subject"], payload["body"],
        reply_to=payload.get("reply_to"),
        smtp_config=_smtp_config_from(sender),
        branding=payload.get("branding"),
        is_otp=bool(payload.get("is_otp")),
        cc=payload.get("cc"),
    )
    if result.get("sent"):
        _log(payload, "sent", attempt, result)
        return {"sent": True, "account": account}

    # 3. Retry transient failures; give the reserved slot back first.
    etype = result.get("error_type")
    if etype in ("network", "smtp") and attempt < MAX_RETRIES:
        ratelimit.release(account)
        backoff = min((2 ** attempt) * 60, 3600)
        _reschedule(payload, attempt + 1, backoff, f"retry:{etype}")
        # Still 'queued' as far as delivery goes — a retry is pending, not a loss.
        _log(payload, "queued", attempt + 1, result)
        return {"retry": True, "attempt": attempt + 1, "error": result.get("error")}

    logger.error(
        f"[EMAIL QUEUE] giving up: to={payload.get('to')} "
        f"type={etype} err={result.get('error')}"
    )
    _log(payload, "failed", attempt, result)
    return {"sent": False, "error": result.get("error"), "error_type": etype}
