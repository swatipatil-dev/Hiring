"""Sender identities: who a message may say it is from.

``usable_reason`` is the single answer to "can this sender send right now?",
shared by the sender policy, the worker's pre-send revalidation, the routing
view and the test endpoints, so they cannot disagree.

Connecting a mailbox does not authorise every From address its server would
accept. A sender is either the connection's own login address (``primary``) or
an address on the same domain an administrator approved (``approved_alias``).
Rows migrated from the old tables are ``legacy_unverified``: kept working,
marked for review.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from . import constants as C

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_CRLF = re.compile(r"[\r\n]")


class SenderError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def usable_reason(sender, conn) -> Optional[str]:
    """``None`` when the sender can send now, else a reason code."""
    if sender is None or sender.deleted_at is not None:
        return C.R_SENDER_NOT_FOUND
    if not sender.enabled:
        return C.R_SENDER_DISABLED
    if conn is None or conn.status != C.CONN_ACTIVE or not conn.credential_enc:
        return C.R_CONNECTION_UNAVAILABLE
    if conn.id != sender.connection_id:
        return C.R_SENDER_CHANGED
    return None


def describe_reason(code: Optional[str], conn=None) -> Optional[str]:
    """One line an administrator can act on."""
    if code is None:
        return None
    if code == C.R_CONNECTION_UNAVAILABLE and conn is not None:
        if conn.status == C.CONN_ACTIVE and not conn.credential_enc:
            return "The mailbox has no password stored. Re-enter it in Email Settings."
        detail = f" ({conn.error_code})" if conn.error_code else ""
        return f"The mailbox connection is {conn.status.replace('_', ' ')}{detail}. Reconnect it in Email Settings."
    return {
        C.R_SENDER_NOT_FOUND: "The selected sender does not exist in this company.",
        C.R_SENDER_DISABLED: "The selected sender is disabled.",
        C.R_CONNECTION_UNAVAILABLE: "The mailbox connection is not active.",
        C.R_SENDER_CHANGED: "The sender was moved to a different mailbox after this message was queued.",
        C.R_NO_SENDER: "No sender is configured for this company. Connect a mailbox in Email Settings.",
        C.R_ROUTE_SENDER_UNAVAILABLE: "The sender chosen for this email cannot send.",
        C.R_ROUTE_DISABLED: "This email is switched off in Pipeline Notifications.",
        C.R_EVENT_NOT_ALLOWED: "This kind of email cannot be sent by this company.",
        C.R_MIGRATION_PENDING: "Email settings are being migrated. Mail will send once the migration has run.",
    }.get(code, code.replace("_", " "))


def classify_address(email: str, username: Optional[str]) -> str:
    """``primary`` / ``approved_alias``, or raise if the address is not allowed."""
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email) or _CRLF.search(email):
        raise SenderError("sender_email_invalid", "Sender email address is not valid")
    login = (username or "").strip().lower()
    if email == login:
        return C.SENDER_PRIMARY
    if "@" in login and email.rsplit("@", 1)[1] == login.rsplit("@", 1)[1]:
        return C.SENDER_ALIAS
    raise SenderError(
        "sender_not_authorised",
        "The sender must be the mailbox's own address or another address on the same domain")


def load(db, company_id: str, sender_id: Optional[str]):
    """A sender and its connection, both scoped to ``company_id``."""
    from src.models.models import EmailSender, EmailConnection
    if not sender_id:
        return None, None
    sender = (db.query(EmailSender)
              .filter(EmailSender.company_id == company_id, EmailSender.id == sender_id)
              .first())
    if sender is None:
        return None, None
    conn = (db.query(EmailConnection)
            .filter(EmailConnection.company_id == company_id,
                    EmailConnection.id == sender.connection_id)
            .first())
    return sender, conn


def create(db, *, company_id: str, conn, email: str, label: str,
           display_name: Optional[str] = None, reply_to: Optional[str] = None,
           actor_user_id: Optional[str] = None, sender_id: Optional[str] = None,
           verification_status: Optional[str] = None, hourly_limit: Optional[int] = None):
    from src.models.models import EmailSender
    if conn is None or conn.company_id != company_id:
        raise SenderError(C.R_SENDER_NOT_FOUND, "Connection not found")
    status = verification_status or classify_address(email, conn.username)
    for value in (label, display_name, reply_to):
        if value and _CRLF.search(value):
            raise SenderError("header_invalid", "Line breaks are not allowed")
    if reply_to and not _EMAIL_RE.match(reply_to.strip()):
        raise SenderError("reply_to_invalid", "Reply-to address is not valid")
    taken = (db.query(EmailSender.id)
             .filter(EmailSender.company_id == company_id, EmailSender.label == label)
             .first())
    if taken:
        raise SenderError("sender_label_taken", f"A sender named {label!r} already exists")
    sender = EmailSender(company_id=company_id, connection_id=conn.id, label=label,
                         email=email.strip().lower(), display_name=display_name,
                         reply_to=(reply_to or "").strip() or None,
                         verification_status=status, enabled=True,
                         hourly_limit=hourly_limit, created_by_user_id=actor_user_id)
    if sender_id:
        sender.id = sender_id
    db.add(sender)
    db.flush()
    return sender


def soft_delete(db, sender) -> None:
    """Retire a sender without breaking messages that reference it.

    The label gets a suffix so the name can be reused; routes that pointed at it
    are cleared, which makes those events block (visibly) rather than send from
    something else.
    """
    from src.models.models import EmailRoute
    sender.deleted_at = datetime.utcnow()
    sender.enabled = False
    sender.label = f"{sender.label[:80]} [deleted {sender.id[:8]}]"
    for route in (db.query(EmailRoute)
                  .filter(EmailRoute.company_id == sender.company_id,
                          EmailRoute.sender_id == sender.id)
                  .all()):
        route.sender_id = None
        route.policy_version = (route.policy_version or 1) + 1
    db.flush()


def public(sender, conn=None) -> dict:
    """API representation. Never includes a credential."""
    reason = usable_reason(sender, conn) if conn is not None else None
    return {
        "id": sender.id,
        "label": sender.label,
        "email": sender.email,
        "display_name": sender.display_name,
        "reply_to": sender.reply_to,
        "connection_id": sender.connection_id,
        "verification_status": sender.verification_status,
        "enabled": bool(sender.enabled),
        "usable": reason is None if conn is not None else None,
        "unusable_reason": reason,
    }
