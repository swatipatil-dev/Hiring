"""Legacy RQ job: convert an old-format queued email into an email API message.

Before the email API, ``dispatch`` queued the whole email — sender host, user
and an encrypted password included — as one RQ job. Jobs enqueued by that
release may still be waiting when this one starts. Rather than send them the
old way, each is re-submitted through the sender policy: the company and step
it carried decide the sender, exactly as a new email would, and the queued
password is ignored. The content is kept, so nothing that was accepted is lost.

Kept on the worker's allow-list (src/mailq/safety.py) for one release, then
removed together with this module.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def _event_for(payload: dict) -> str:
    from src.email_api import constants as C
    step = payload.get("step_key")
    if step in C.EVENT_CATALOG:
        return step
    return C.EVENT_AUTH_VERIFY if payload.get("is_otp") else "custom_mail"


def send_email_job(payload: dict, attempt: int = 0, db=None) -> dict:
    """Re-submit one legacy job as an email API message. Never sends directly."""
    from src.email_api import db as email_db
    from src.email_api.compat import SenderRef
    from src.email_api.policy import resolve_sender
    from src.services.email_service import dispatch

    company_id = payload.get("company_id")
    to = payload.get("to")
    if not company_id or not to:
        logger.error("[EMAIL QUEUE] dropping legacy job with no company or recipient (to=%s)", to)
        return {"converted": False, "reason": "incomplete legacy payload"}
    session = db or email_db.open_session()
    try:
        decision = resolve_sender(session, company_id, _event_for(payload))
        result = dispatch(
            to, payload.get("subject") or "", payload.get("body") or "",
            reply_to=payload.get("reply_to"), smtp_config=SenderRef(decision),
            branding=payload.get("branding"), is_otp=bool(payload.get("is_otp")),
            cc=payload.get("cc"), db=session,
            reference_type="legacy_job", reference_id=payload.get("log_id"),
        )
        if db is None:
            session.commit()
        logger.info("[EMAIL QUEUE] legacy job to %s converted: %s", to, result.get("status"))
        return {"converted": True, **{k: result.get(k) for k in ("message_id", "status")}}
    except Exception:
        if db is None:
            session.rollback()
        raise
    finally:
        if db is None:
            session.close()
