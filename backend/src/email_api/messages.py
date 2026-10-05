"""Accept an email: validate it, apply idempotency, persist it with its outbox row.

``submit`` is the one entry point, for the hiring application's own mail and for
external API calls alike. It never talks to a mail server. It writes, in the
caller's transaction:

* the message, with the sender and connection the policy chose frozen onto it
  and the content exactly as rendered now (encrypted at rest);
* one row per recipient;
* an outbox row — unless the message is blocked, in which case it is recorded
  with its reason and nothing will try to send it.

The caller commits. Only after that commit does ``outbox`` hand the message to
the queue, so a rolled-back request can never leave a sent email behind.

Idempotency (spec §11): a repeated ``Idempotency-Key`` with the same request
returns the original message; with a different request it is a conflict. The
hash covers the *validated* request and the *requested* sender, never the
resolved one, so a routing change between two retries is not a conflict.
"""
from __future__ import annotations

import hashlib
import html as html_lib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy.exc import IntegrityError

from . import constants as C
from . import crypto
from .policy import Actor, SenderDecision, SYSTEM

logger = logging.getLogger(__name__)

MAX_RECIPIENTS = 50
MAX_BODY_BYTES = 1024 * 1024
MAX_SUBJECT = 998
MAX_METADATA_BYTES = 8 * 1024
DEFAULT_MAX_ATTEMPTS = int(os.getenv("EMAIL_MAX_RETRIES", "5")) + 1

_EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
_CRLF = re.compile(r"[\r\n]")
_VAR = re.compile(r"{{\s*([A-Za-z0-9_.-]+)\s*}}")


class SubmitError(ValueError):
    """The request cannot be accepted. ``code`` is machine-readable."""
    status_code = 422

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class IdempotencyConflict(SubmitError):
    status_code = 409


@dataclass
class SubmitResult:
    message: Any            # EmailMessage, or None when the route is disabled
    status: str             # the message status, or "disabled"
    replayed: bool = False

    @property
    def message_id(self) -> Optional[str]:
        return self.message.id if self.message is not None else None


# ── validation ───────────────────────────────────────────────────────────────

def _addr(item) -> Dict[str, Optional[str]]:
    if isinstance(item, str):
        email, name = item, None
    else:
        email, name = item.get("email"), item.get("name")
    email = (email or "").strip()
    if not _EMAIL_RE.match(email) or _CRLF.search(email):
        raise SubmitError("recipient_invalid", f"Not a valid email address: {email!r}")
    if name and _CRLF.search(name):
        raise SubmitError("header_invalid", "Line breaks are not allowed in names")
    return {"email": email, "name": (name or "").strip() or None}


def normalise_recipients(to: Iterable, cc: Iterable = (), bcc: Iterable = ()) -> Dict[str, List[dict]]:
    """Validate addresses and drop duplicates (case-insensitive), To first.

    The same rule the legacy sender applied to CC: an address already in To is
    not copied again.
    """
    seen, out = set(), {"to": [], "cc": [], "bcc": []}
    for kind, items in (("to", to or ()), ("cc", cc or ()), ("bcc", bcc or ())):
        for item in items:
            if not item:
                continue
            a = _addr(item)
            key = a["email"].lower()
            if key in seen:
                continue
            seen.add(key)
            out[kind].append(a)
    if not out["to"]:
        raise SubmitError("recipient_required", "At least one To recipient is required")
    if sum(len(v) for v in out.values()) > MAX_RECIPIENTS:
        raise SubmitError("too_many_recipients", f"At most {MAX_RECIPIENTS} recipients per message")
    return out


def render_template(db, company_id: str, template_id: str, variables: Optional[dict]):
    """Render a company's ``MailTemplate``: ``{{name}}`` placeholders.

    Values are HTML-escaped into the body; the subject is plain text with line
    breaks removed. Newlines in the stored body become ``<br>``, as in the HR
    compose dialog. Unknown placeholders are left as written.
    """
    from src.models.models import MailTemplate
    tpl = (db.query(MailTemplate)
           .filter(MailTemplate.company_id == company_id, MailTemplate.id == template_id,
                   MailTemplate.is_active.is_(True))
           .first())
    if tpl is None:
        raise SubmitError("template_not_found", "Template not found")
    values = {str(k): "" if v is None else str(v) for k, v in (variables or {}).items()}

    def fill(text: str, escape: bool) -> str:
        return _VAR.sub(lambda m: (html_lib.escape(values[m.group(1)]) if escape else values[m.group(1)])
                        if m.group(1) in values else m.group(0), text or "")

    subject = _CRLF.sub(" ", fill(tpl.subject or "", escape=False)).strip()
    body = fill(tpl.body or "", escape=True)
    if "<" not in body:
        body = body.replace("\n", "<br>")
    return subject, body


def _check_subject(subject: Optional[str]) -> str:
    subject = (subject or "").strip()
    if not subject:
        raise SubmitError("subject_required", "Subject is required")
    if _CRLF.search(subject):
        raise SubmitError("header_invalid", "Line breaks are not allowed in the subject")
    if len(subject) > MAX_SUBJECT:
        raise SubmitError("subject_too_long", f"Subject is longer than {MAX_SUBJECT} characters")
    return subject


def request_hash(payload: dict) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


# ── persistence ─────────────────────────────────────────────────────────────

def _existing(db, company_id: str, is_test: bool, key: str):
    from src.models.models import EmailMessage
    return (db.query(EmailMessage)
            .filter(EmailMessage.company_id == company_id, EmailMessage.is_test.is_(bool(is_test)),
                    EmailMessage.idempotency_key == key)
            .first())


def _replay(existing, digest: str) -> SubmitResult:
    if existing.request_hash != digest:
        raise IdempotencyConflict(
            "idempotency_conflict",
            "This Idempotency-Key was already used with a different request")
    return SubmitResult(existing, existing.status, replayed=True)


def msgid_domain() -> str:
    return os.getenv("EMAIL_MSGID_DOMAIN", "hireflow.io")


def submit(db, *, tenant_id: str, decision: SenderDecision, event_type: str,
           to: Iterable, cc: Iterable = (), bcc: Iterable = (),
           subject: Optional[str] = None, html: Optional[str] = None,
           text: Optional[str] = None, template_id: Optional[str] = None,
           variables: Optional[dict] = None, actor: Actor = SYSTEM,
           idempotency_key: Optional[str] = None, external_reference: Optional[str] = None,
           metadata: Optional[dict] = None, reference_type: Optional[str] = None,
           reference_id: Optional[str] = None, step_key: Optional[str] = None,
           is_test: bool = False, priority: str = C.PRIORITY_NORMAL,
           is_sensitive: bool = False, requested_sender_id: Optional[str] = None,
           from_name: Optional[str] = None, unsubscribe_to: Optional[str] = None,
           defer_inline: bool = False) -> SubmitResult:
    """Persist one email. See the module docstring. The caller commits."""
    from src.models.models import EmailMessage, EmailRecipient, EmailOutbox

    if decision.status == "disabled":
        return SubmitResult(None, "disabled")
    if decision.company_id != tenant_id or decision.event_type != event_type:
        raise SubmitError("decision_mismatch", "Sender decision is for a different email")
    if decision.ok and decision.sender_company_id != tenant_id \
            and decision.route_source != C.SRC_PLATFORM_AUTH:
        raise SubmitError(C.R_SENDER_NOT_FOUND, "Sender belongs to another company")

    rcpts = normalise_recipients(to, cc, bcc)
    if template_id:
        if subject or html or text:
            raise SubmitError("content_conflict", "Send either a template or subject/html/text, not both")
        subject, html = render_template(db, tenant_id, template_id, variables)
    subject = _check_subject(subject)
    if not (html or text):
        raise SubmitError("content_required", "A message body is required")
    if len((html or "").encode()) + len((text or "").encode()) > MAX_BODY_BYTES:
        raise SubmitError("body_too_large", "Message body is larger than 1 MB")
    if metadata is not None and len(json.dumps(metadata, default=str)) > MAX_METADATA_BYTES:
        raise SubmitError("metadata_too_large", "Metadata is larger than 8 KB")
    if idempotency_key is not None:
        idempotency_key = idempotency_key.strip()
        if not idempotency_key or len(idempotency_key) > 255:
            raise SubmitError("idempotency_key_invalid", "Idempotency-Key must be 1-255 characters")

    digest = request_hash({
        "event_type": event_type, "recipients": rcpts, "subject": subject, "html": html,
        "text": text, "template_id": template_id, "variables": variables,
        "requested_sender_id": requested_sender_id, "external_reference": external_reference,
        "metadata": metadata, "is_test": bool(is_test),
    })
    if idempotency_key:
        existing = _existing(db, tenant_id, is_test, idempotency_key)
        if existing is not None:
            return _replay(existing, digest)

    content = json.dumps({"html": html, "text": text, "unsubscribe_to": unsubscribe_to})
    content_enc, content_version = crypto.encrypt_content(content)
    now = datetime.utcnow()
    blocked = decision.status == "blocked"
    msg = EmailMessage(
        company_id=tenant_id, is_test=bool(is_test), event_type=event_type, priority=priority,
        status=C.BLOCKED if blocked else C.QUEUED,
        status_reason=decision.reason_code if blocked else None,
        status_detail=(decision.reason or "")[:500] if blocked else None,
        sender_id=None if blocked else decision.sender_id,
        sender_company_id=None if blocked else decision.sender_company_id,
        connection_id=None if blocked else decision.connection_id,
        route_source=decision.route_source, policy_version=decision.policy_version,
        from_email=decision.from_email, from_name=from_name or decision.from_name,
        reply_to=decision.reply_to, subject=subject,
        content_enc=content_enc, content_key_version=content_version,
        content_sha256=hashlib.sha256(content.encode()).hexdigest(),
        is_sensitive=bool(is_sensitive), template_id=template_id,
        actor_type=actor.type, actor_user_id=actor.user_id, api_key_id=actor.api_key_id,
        external_actor_reference=actor.external_reference,
        external_reference=external_reference, metadata_json=metadata,
        reference_type=reference_type, reference_id=reference_id, step_key=step_key,
        idempotency_key=idempotency_key or None, request_hash=digest,
        attempt_count=0, max_attempts=DEFAULT_MAX_ATTEMPTS,
        completed_at=now if blocked else None, created_at=now, updated_at=now,
    )
    savepoint = db.begin_nested()
    try:
        db.add(msg)
        db.flush()
        msg.message_id_header = f"<{msg.id}@{msgid_domain()}>"
        for kind in ("to", "cc", "bcc"):
            for a in rcpts[kind]:
                db.add(EmailRecipient(company_id=tenant_id, message_id=msg.id, email=a["email"],
                                      name=a["name"], kind=kind, status=C.RCPT_PENDING))
        if not blocked:
            db.add(EmailOutbox(company_id=tenant_id, message_id=msg.id, state="pending",
                               available_at=now, created_at=now, updated_at=now))
        db.flush()
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        if idempotency_key:
            existing = _existing(db, tenant_id, is_test, idempotency_key)
            if existing is not None:
                return _replay(existing, digest)
        raise

    if blocked:
        logger.warning("[EMAIL] blocked %s for company %s: %s", event_type, tenant_id,
                       decision.reason_code)
    else:
        from . import outbox
        outbox.note_pending(db, msg.id, tenant_id, priority, defer_inline=defer_inline)
    return SubmitResult(msg, msg.status)


# ── after acceptance ────────────────────────────────────────────────────────

class StateError(SubmitError):
    status_code = 409


def cancel(db, msg) -> None:
    """Best effort, before anything reached a server. Accepted mail cannot be recalled."""
    from src.models.models import EmailMessage, EmailOutbox
    if msg.status not in (C.QUEUED, C.DEFERRED):
        raise StateError("not_cancellable", f"A {msg.status} message cannot be cancelled")
    now = datetime.utcnow()
    n = (db.query(EmailMessage)
         .filter(EmailMessage.id == msg.id, EmailMessage.company_id == msg.company_id,
                 EmailMessage.status.in_((C.QUEUED, C.DEFERRED)))
         .update({"status": C.CANCELLED, "cancelled_at": now, "completed_at": now,
                  "updated_at": now}, synchronize_session=False))
    if not n:
        raise StateError("not_cancellable", "The message is already being sent")
    (db.query(EmailOutbox)
     .filter(EmailOutbox.message_id == msg.id, EmailOutbox.company_id == msg.company_id)
     .update({"state": "done", "updated_at": now}, synchronize_session=False))
    db.refresh(msg)


def retry(db, msg, *, confirm_duplicate_risk: bool = False, actor: Actor = SYSTEM) -> None:
    """Send a failed, blocked or unknown message again.

    The identity chosen when the message was accepted is kept. A message that
    was blocked before any sender was chosen is resolved again under today's
    routing — unless it named a sender explicitly, which only the caller can
    correct by resubmitting. ``unknown`` may already have been delivered, so it
    needs ``confirm_duplicate_risk``.
    """
    from src.models.models import EmailRecipient, EmailOutbox
    from .policy import resolve_sender
    if msg.status not in (C.FAILED, C.BLOCKED, C.UNKNOWN):
        raise StateError("not_retryable", f"A {msg.status} message cannot be retried")
    if msg.status == C.UNKNOWN and not confirm_duplicate_risk:
        raise StateError("duplicate_risk",
                         "The server may already have accepted this message. Retry with "
                         "confirm_duplicate_risk=true to send it again anyway.")
    if msg.content_enc is None:
        raise StateError("content_purged", "This message's content is no longer stored")
    if msg.sender_id is None:
        if msg.route_source == C.SRC_REQUESTED:
            raise StateError("resubmit_required",
                             "This message named a sender that could not be used; submit it again")
        decision = resolve_sender(db, msg.company_id, msg.event_type, actor=actor)
        if not decision.ok:
            raise StateError(decision.reason_code or "blocked", decision.reason or "Still blocked")
        msg.sender_id, msg.sender_company_id = decision.sender_id, decision.sender_company_id
        msg.connection_id, msg.route_source = decision.connection_id, decision.route_source
        msg.policy_version, msg.from_email = decision.policy_version, decision.from_email
        msg.from_name = msg.from_name or decision.from_name
        msg.reply_to = decision.reply_to
    now = datetime.utcnow()
    msg.status, msg.status_reason, msg.status_detail = C.QUEUED, None, None
    msg.completed_at = msg.next_attempt_at = msg.lease_expires_at = None
    msg.max_attempts = (msg.attempt_count or 0) + DEFAULT_MAX_ATTEMPTS
    msg.updated_at = now
    for r in (db.query(EmailRecipient)
              .filter(EmailRecipient.message_id == msg.id, EmailRecipient.company_id == msg.company_id)
              .all()):
        if r.status != C.RCPT_ACCEPTED and r.status != C.RCPT_SUPPRESSED:
            r.status = C.RCPT_PENDING
    box = (db.query(EmailOutbox)
           .filter(EmailOutbox.message_id == msg.id, EmailOutbox.company_id == msg.company_id)
           .first())
    if box is None:
        db.add(EmailOutbox(company_id=msg.company_id, message_id=msg.id, state="pending",
                           available_at=now, created_at=now, updated_at=now))
    else:
        box.state, box.available_at, box.lease_owner, box.lease_expires_at = "pending", now, None, None
    db.flush()
    from . import outbox
    outbox.note_pending(db, msg.id, msg.company_id, msg.priority)
