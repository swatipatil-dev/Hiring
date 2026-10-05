"""The admin and HR screens' email endpoints, on top of the email API's tables.

The frontend's Email Settings, mailbox manager, Mail Routing panel and HR
dialogs keep their request and response shapes; underneath, a "mailbox" is now
a sender with its connection, and the "company SMTP" is the company's default
sender. ``tenant_configs`` and ``company_mailboxes`` are no longer read or
written (revision t2e3m4a5i6l7 copied them; a rollback copies back with
scripts/email_connections_to_legacy.py).

What changed for an admin, deliberately:

* saving a mailbox checks the destination (public host, port 465 or 587) and
  logs in to it; one that fails is saved with its error instead of looking fine;
* a test email goes to the sender's own address or a user of the company;
* clearing the company SMTP leaves the company with no default sender — its
  mail is then blocked, not sent from the platform's account.

Errors are raised as ``FacadeError`` with a message for the existing UI.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from . import constants as C
from . import connections as conns
from . import senders as S

DEFAULT_LABEL = "Company default"
DEFAULT_SOURCE_PREFIX = "tenant_configs:"


class FacadeError(Exception):
    def __init__(self, status_code: int, message: str, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


def _wrap(e: Exception) -> FacadeError:
    code = getattr(e, "code", None)
    return FacadeError(400, str(e), code)


# Legacy "error_type" values the existing UI understands.
_ERROR_TYPES = {
    C.R_AUTH_FAILED: "auth", C.R_TLS_CERT_INVALID: "tls", C.R_STARTTLS_UNAVAILABLE: "tls",
    "network_error": "network", "dns_error": "network", C.R_DESTINATION_NOT_ALLOWED: "network",
    "smtp_temporary": "smtp", "smtp_rejected": "smtp", C.R_SENDER_REJECTED: "smtp",
    "test_rate_limited": "rate_limited",
}


def legacy_error_type(code: Optional[str]) -> Optional[str]:
    if code is None:
        return None
    return _ERROR_TYPES.get(code, "unknown")


def _route(db, company_id, event_type):
    from src.models.models import EmailRoute
    return (db.query(EmailRoute)
            .filter(EmailRoute.company_id == company_id, EmailRoute.event_type == event_type)
            .first())


def set_route(db, company_id: str, event_type: str, *, sender_id=None, enabled=None,
              actor_user_id: Optional[str] = None, clear_sender: bool = False):
    """Create or change one route, bumping its policy version when it changes."""
    from src.models.models import EmailRoute
    route = _route(db, company_id, event_type)
    if route is None:
        route = EmailRoute(company_id=company_id, event_type=event_type, enabled=True,
                           policy_version=1)
        db.add(route)
        changed = True
    else:
        changed = False
    if clear_sender or sender_id is not None:
        new_sender = None if clear_sender else sender_id
        changed = changed or route.sender_id != new_sender
        route.sender_id = new_sender
    if enabled is not None and bool(route.enabled) != bool(enabled):
        route.enabled, changed = bool(enabled), True
    if changed and route.id is not None:
        route.policy_version = (route.policy_version or 1) + 1
    route.updated_by_user_id = actor_user_id
    db.flush()
    return route


# ── the company default sender ("Email Settings") ──────────────────────────

def default_sender(db, company_id: str):
    """The company's default sender and its connection, or (None, None)."""
    route = _route(db, company_id, C.DEFAULT_ROUTE)
    if route is None or not route.sender_id:
        return None, None
    return S.load(db, company_id, route.sender_id)


def company_smtp_view(db, company_id: str) -> dict:
    sender, conn = default_sender(db, company_id)
    if sender is None or conn is None or sender.deleted_at is not None:
        return {"smtp_host": "smtp.gmail.com", "smtp_port": 587, "smtp_user": "",
                "smtp_from_email": "", "has_password": False, "configured": False,
                "status": None, "error_code": None, "security": None, "sender_id": None}
    return {
        "smtp_host": conn.host or "", "smtp_port": conn.port, "smtp_user": conn.username or "",
        "smtp_from_email": sender.email, "has_password": bool(conn.credential_enc),
        "configured": S.usable_reason(sender, conn) is None,
        "status": conn.status, "error_code": conn.error_code, "error_detail": conn.error_detail,
        "security": conn.security, "sender_id": sender.id,
        "last_tested_at": conn.last_tested_at.isoformat() if conn.last_tested_at else None,
    }


def save_company_smtp(db, company_id: str, *, host, port, user, password, from_email,
                      actor_user_id=None) -> dict:
    """Create or update the company default sender. Verifies the login."""
    sender, conn = default_sender(db, company_id)
    from_email = (from_email or user or "").strip().lower()
    try:
        S.classify_address(from_email, user)
        if conn is None or sender is None or sender.deleted_at is not None:
            if not password:
                raise FacadeError(400, "Enter the mailbox password", "password_required")
            conn = conns.create_smtp(db, company_id=company_id, name="Company SMTP", host=host,
                                     port=port, username=user, password=password,
                                     actor_user_id=actor_user_id)
            sender = S.create(db, company_id=company_id, conn=conn, email=from_email,
                              label=_free_label(db, company_id, DEFAULT_LABEL),
                              actor_user_id=actor_user_id)
            set_route(db, company_id, C.DEFAULT_ROUTE, sender_id=sender.id,
                      actor_user_id=actor_user_id)
        else:
            conns.update_smtp(db, conn, host=host, port=port, username=user,
                              password=password or None)
            _change_address(db, sender, conn, from_email)
    except (conns.ConnectionError_, S.SenderError) as e:
        raise _wrap(e)
    return company_smtp_view(db, company_id)


def clear_company_smtp(db, company_id: str, actor_user_id=None) -> None:
    sender, conn = default_sender(db, company_id)
    if conn is not None:
        conns.disconnect(db, conn)
    set_route(db, company_id, C.DEFAULT_ROUTE, clear_sender=True, actor_user_id=actor_user_id)


def _free_label(db, company_id, wanted):
    from src.models.models import EmailSender
    label, n = wanted, 2
    while (db.query(EmailSender.id)
           .filter(EmailSender.company_id == company_id, EmailSender.label == label).first()):
        label, n = f"{wanted} ({n})", n + 1
    return label


def _change_address(db, sender, conn, email: str) -> None:
    """The legacy screens edit the From address in place.

    Mail already queued under the old address will block at delivery
    (``sender_changed``) rather than go out under the new one.
    """
    email = (email or "").strip().lower()
    if email and email != sender.email:
        sender.verification_status = S.classify_address(email, conn.username)
        sender.email = email
        db.flush()


# ── named mailboxes ────────────────────────────────────────────────────────

def _is_default_only(sender) -> bool:
    return (sender.legacy_source or "").startswith(DEFAULT_SOURCE_PREFIX)


def mailbox_view(sender, conn) -> dict:
    reason = S.usable_reason(sender, conn)
    return {
        "id": sender.id,
        "name": sender.label,
        "smtp_host": (conn.host if conn else "") or "",
        "smtp_port": conn.port if conn else None,
        "smtp_user": (conn.username if conn else "") or "",
        "smtp_from_email": sender.email,
        "hourly_limit": sender.hourly_limit or (conn.hourly_limit if conn else None),
        "has_password": bool(conn and conn.credential_enc),
        "is_active": bool(sender.enabled),
        "status": conn.status if conn else None,
        "error_code": conn.error_code if conn else None,
        "error_detail": conn.error_detail if conn else None,
        "security": conn.security if conn else None,
        "last_tested_at": conn.last_tested_at.isoformat() if conn and conn.last_tested_at else None,
        "usable": reason is None,
        "unusable_reason": S.describe_reason(reason, conn),
        "source": "environment" if (sender.legacy_source or "").startswith("env:") else "company",
    }


def list_mailboxes(db, company_id: str, *, usable_only: bool = False) -> list:
    from src.models.models import EmailSender, EmailConnection
    rows = (db.query(EmailSender)
            .filter(EmailSender.company_id == company_id, EmailSender.deleted_at.is_(None))
            .order_by(EmailSender.label)
            .all())
    by_id = {c.id: c for c in db.query(EmailConnection)
             .filter(EmailConnection.company_id == company_id).all()}
    out = []
    for s in rows:
        if _is_default_only(s):
            continue
        view = mailbox_view(s, by_id.get(s.connection_id))
        if usable_only and not s.enabled:
            continue
        out.append(view)
    return out


def _mailbox(db, company_id, mailbox_id):
    sender, conn = S.load(db, company_id, mailbox_id)
    if sender is None or sender.deleted_at is not None:
        raise FacadeError(404, "Mailbox not found")
    return sender, conn


def create_mailbox(db, company_id: str, *, name, host, port, user, password, from_email,
                   hourly_limit=None, is_active=True, actor_user_id=None) -> dict:
    name = (name or "").strip()
    if not name:
        raise FacadeError(400, "Mailbox name is required")
    from_email = (from_email or user or "").strip().lower()
    try:
        S.classify_address(from_email, user)
        if password:
            conn = conns.create_smtp(db, company_id=company_id, name=name, host=host, port=port,
                                     username=user, password=password,
                                     actor_user_id=actor_user_id, hourly_limit=hourly_limit)
        else:
            conn = _passwordless_connection(db, company_id, name, host, port, user,
                                            hourly_limit, actor_user_id)
        from src.models.models import EmailSender
        if (db.query(EmailSender.id)
                .filter(EmailSender.company_id == company_id, EmailSender.label == name).first()):
            raise FacadeError(409, "A mailbox with that name already exists")
        sender = S.create(db, company_id=company_id, conn=conn, email=from_email, label=name,
                          actor_user_id=actor_user_id, hourly_limit=hourly_limit)
        sender.enabled = bool(is_active)
        db.flush()
    except (conns.ConnectionError_, S.SenderError) as e:
        raise _wrap(e)
    return mailbox_view(sender, conn)


def _passwordless_connection(db, company_id, name, host, port, user, hourly_limit, actor_user_id):
    """A mailbox saved without a password yet: kept, but it cannot send."""
    from src.models.models import EmailConnection
    from . import ssrf
    try:
        endpoint = ssrf.validate_smtp_endpoint(host, port)
    except ssrf.DestinationNotAllowed as e:
        raise conns.ConnectionError_(e.code, str(e))
    conn = EmailConnection(company_id=company_id, type=C.CONN_SMTP, provider="smtp", name=name,
                           status=C.CONN_NEEDS_ATTENTION, error_code="no_password",
                           host=endpoint["host"], port=endpoint["port"],
                           security=endpoint["security"], username=(user or "").strip(),
                           account_key=conns.account_key(endpoint["host"], user),
                           hourly_limit=hourly_limit, created_by_user_id=actor_user_id)
    db.add(conn)
    db.flush()
    return conn


def update_mailbox(db, company_id: str, mailbox_id: str, *, name, host, port, user, password,
                   from_email, hourly_limit=None, is_active=True) -> dict:
    sender, conn = _mailbox(db, company_id, mailbox_id)
    name = (name or "").strip()
    if not name:
        raise FacadeError(400, "Mailbox name is required")
    from src.models.models import EmailSender
    clash = (db.query(EmailSender.id)
             .filter(EmailSender.company_id == company_id, EmailSender.label == name,
                     EmailSender.id != sender.id)
             .first())
    if clash:
        raise FacadeError(409, "A mailbox with that name already exists")
    try:
        if conn.status == C.CONN_DISCONNECTED and not password:
            raise FacadeError(400, "Re-enter the password to reconnect this mailbox")
        if conn.status == C.CONN_NEEDS_ATTENTION and conn.error_code == "no_password" and not password:
            conns.update_smtp(db, conn, name=name, host=host, port=port, username=user,
                              hourly_limit=hourly_limit, validate=False)
        else:
            conns.update_smtp(db, conn, name=name, host=host, port=port, username=user,
                              password=password or None, hourly_limit=hourly_limit)
        S.update(db, sender, label=name, enabled=is_active)
        sender.hourly_limit = hourly_limit
        _change_address(db, sender, conn, from_email or user)
    except (conns.ConnectionError_, S.SenderError) as e:
        raise _wrap(e)
    return mailbox_view(sender, conn)


def delete_mailbox(db, company_id: str, mailbox_id: str) -> None:
    """Retire a mailbox. Steps that used it fall back to the company default;
    queued mail through it blocks rather than switching sender."""
    sender, conn = _mailbox(db, company_id, mailbox_id)
    S.soft_delete(db, sender)
    from src.models.models import EmailSender
    others = (db.query(EmailSender.id)
              .filter(EmailSender.company_id == company_id,
                      EmailSender.connection_id == conn.id, EmailSender.deleted_at.is_(None))
              .first())
    if conn is not None and not others:
        conns.disconnect(db, conn)


# ── test sends ─────────────────────────────────────────────────────────────

def test_send(db, company_id: str, sender, conn, to: str, *, actor=None,
              subject: Optional[str] = None, html: Optional[str] = None) -> dict:
    """A controlled, synchronous test send, in the legacy response shape."""
    from src.models.models import User
    to = (to or "").strip()
    if to.lower() != sender.email.lower():
        known = (db.query(User.id)
                 .filter(User.company_id == company_id, User.email == to,
                         User.is_active.is_(True))
                 .first())
        if not known:
            raise FacadeError(400, "Test emails go to the mailbox's own address or to an "
                                   "active user of your company.", "test_recipient_not_allowed")
    if conn is None:
        return {"sent": False, "error": "This mailbox has no connection", "error_type": "unusable"}
    reason = S.usable_reason(sender, conn)
    if reason and not (reason == C.R_CONNECTION_UNAVAILABLE and conn.credential_enc
                       and conn.status != C.CONN_DISCONNECTED):
        # Missing password, disabled, deleted: a configuration answer, not an SMTP round trip.
        # A *failed* connection is still tested — that is how an admin fixes it.
        return {"sent": False, "error": S.describe_reason(reason, conn), "error_type": "unusable",
                "error_code": reason}
    try:
        result = conns.send_test(db, company_id=company_id, conn=conn, sender=sender, to=to,
                                 actor=actor, subject=subject, html=html)
    except conns.ConnectionError_ as e:
        return {"sent": False, "error": str(e), "error_type": legacy_error_type(e.code),
                "error_code": e.code}
    return {"sent": result["sent"], "error": result["error"],
            "error_type": legacy_error_type(result["error_code"]),
            "error_code": result["error_code"], "message_id": result["message_id"],
            "status": result["status"]}


def public_config(sender, conn) -> dict:
    """Non-secret fields for the test endpoints' "config" block."""
    import os
    return {"smtp_host": conn.host if conn else None, "smtp_port": conn.port if conn else None,
            "smtp_user": conn.username if conn else None, "from_email": sender.email,
            "email_enabled": os.getenv("EMAIL_ENABLED", "true").lower() == "true"}
