"""
📧 EMAIL API — tenant-scoped sending for the hiring app's admins and customer applications.

  POST   /api/v1/email/messages                    send (Idempotency-Key header)
  GET    /api/v1/email/messages                    list
  GET    /api/v1/email/messages/{id}               status, recipients, attempts
  POST   /api/v1/email/messages/{id}/retry
  POST   /api/v1/email/messages/{id}/cancel
  GET    /api/v1/email/connections
  POST   /api/v1/email/connections/smtp
  POST   /api/v1/email/connections/{id}/test
  POST   /api/v1/email/connections/{id}/disconnect
  GET    /api/v1/email/senders
  POST   /api/v1/email/senders
  PATCH  /api/v1/email/senders/{id}
  GET    /api/v1/email/suppressions
  DELETE /api/v1/email/suppressions/{email}

  POST   /api/v1/admin/api-keys                    (admin session only)
  GET    /api/v1/admin/api-keys
  POST   /api/v1/admin/api-keys/{id}/revoke
  POST   /api/v1/admin/api-keys/{id}/rotate

Authentication and the company come from the bearer credential
(src/email_api/principal.py). Anything that names another company's resource
gets 404, never 403, so ids cannot be probed. Errors carry
``{"detail": {"code", "message"}}``.
"""
import base64
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from src.auth import require_role
from src.config.database_config_mysql import get_db
from src.email_api import api_keys
from src.email_api import connections as conns
from src.email_api import constants as C
from src.email_api import limits
from src.email_api import messages as M
from src.email_api import senders as S
from src.email_api import schemas
from src.email_api.policy import resolve_sender
from src.email_api.principal import EmailPrincipal, require_scopes
from src.models.models import (EmailAttempt, EmailConnection, EmailMessage, EmailRecipient,
                               EmailSender, EmailSuppression, TenantApiKey, User)

router = APIRouter(prefix="/api/v1/email", tags=["Email API"])
keys_router = APIRouter(prefix="/api/v1/admin/api-keys", tags=["Email API keys"])


def _err(status: int, code: str, message: str, headers=None) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message},
                         headers=headers)


def _not_found(what: str = "Resource") -> HTTPException:
    return _err(404, "not_found", f"{what} not found")


def _iso(d):
    return d.isoformat() if d else None


# ── messages ────────────────────────────────────────────────────────────────

def _message(db, principal: EmailPrincipal, message_id: str) -> EmailMessage:
    msg = (db.query(EmailMessage)
           .filter(EmailMessage.company_id == principal.tenant_id, EmailMessage.id == message_id,
                   EmailMessage.is_test.is_(principal.is_test))
           .first())
    if msg is None:
        raise _not_found("Message")
    return msg


def _message_summary(msg: EmailMessage) -> dict:
    return {
        "message_id": msg.id, "status": msg.status, "status_reason": msg.status_reason,
        "event_type": msg.event_type, "subject": msg.subject, "test": bool(msg.is_test),
        "sender": {"id": msg.sender_id, "email": msg.from_email, "name": msg.from_name},
        "external_reference": msg.external_reference,
        "created_at": _iso(msg.created_at), "accepted_at": _iso(msg.accepted_at),
        "completed_at": _iso(msg.completed_at),
    }


def _message_detail(db, msg: EmailMessage) -> dict:
    rcpts = (db.query(EmailRecipient)
             .filter(EmailRecipient.company_id == msg.company_id, EmailRecipient.message_id == msg.id)
             .all())
    attempts = (db.query(EmailAttempt)
                .filter(EmailAttempt.company_id == msg.company_id, EmailAttempt.message_id == msg.id)
                .order_by(EmailAttempt.attempt_no)
                .all())
    out = _message_summary(msg)
    out.update({
        "status_detail": msg.status_detail,
        "priority": msg.priority,
        "route_source": msg.route_source,
        "connection": {"id": msg.connection_id},
        "reply_to": msg.reply_to,
        "actor": {
            "type": msg.actor_type, "user_id": msg.actor_user_id, "api_key_id": msg.api_key_id,
            # Whatever the customer's application said; never an identity we checked.
            "external_actor_reference": msg.external_actor_reference,
            "external_actor_reference_verified": False,
        },
        "metadata": msg.metadata_json,
        "attempt_count": msg.attempt_count,
        "next_attempt_at": _iso(msg.next_attempt_at),
        "recipients": [{"email": r.email, "name": r.name, "kind": r.kind, "status": r.status,
                        "smtp_code": r.smtp_code} for r in rcpts],
        "attempts": [{"attempt": a.attempt_no, "outcome": a.outcome, "phase": a.phase_reached,
                      "error_code": a.error_code, "smtp_code": a.smtp_code,
                      "started_at": _iso(a.started_at), "finished_at": _iso(a.finished_at)}
                     for a in attempts],
        # SMTP reports acceptance, not delivery; say which this connection can see.
        "delivery_events_supported": False,
    })
    return out


@router.post("/messages", status_code=202)
def send_message(body: schemas.SendMessage, response: Response,
                 idempotency_key: Optional[str] = Header(default=None, alias="Idempotency-Key"),
                 principal: EmailPrincipal = Depends(require_scopes("email:send")),
                 db: Session = Depends(get_db)):
    if body.template_version is not None:
        raise _err(422, "template_version_unsupported",
                   "Templates are not versioned yet; omit template_version")
    if C.event_category(body.event_type) != C.CAT_TENANT or body.event_type == C.EVENT_CONNECTION_TEST:
        raise _err(422, C.R_EVENT_NOT_ALLOWED, f"event_type {body.event_type!r} cannot be sent through the API")
    if body.sender_id:
        sender, _ = S.load(db, principal.tenant_id, body.sender_id)
        if sender is None or sender.deleted_at is not None:
            raise _err(422, C.R_SENDER_NOT_FOUND, "sender_id does not name one of your senders")
    if principal.actor_type == "api_key":
        wait = limits.submit_quota_retry_after(db, company_id=principal.tenant_id,
                                               api_key_id=principal.api_key_id,
                                               key_quota=principal.key_hourly_quota)
        if wait:
            raise _err(429, "quota_exceeded", "Hourly sending quota reached",
                       headers={"Retry-After": str(wait)})
    actor = principal.actor(body.external_actor_reference)
    decision = resolve_sender(db, principal.tenant_id, body.event_type,
                              requested_sender_id=body.sender_id, actor=actor)
    if decision.status == "disabled":
        raise _err(409, C.R_ROUTE_DISABLED, decision.reason or "This email type is switched off")
    try:
        result = M.submit(
            db, tenant_id=principal.tenant_id, decision=decision, event_type=body.event_type,
            to=[r.model_dump() for r in body.to], cc=[r.model_dump() for r in body.cc],
            bcc=[r.model_dump() for r in body.bcc], subject=body.subject, html=body.html,
            text=body.text, template_id=body.template_id, variables=body.variables, actor=actor,
            idempotency_key=idempotency_key, external_reference=body.external_reference,
            metadata=body.metadata, is_test=principal.is_test,
            requested_sender_id=body.sender_id)
    except M.SubmitError as e:
        db.rollback()
        raise _err(e.status_code, e.code, str(e))
    db.commit()
    msg = result.message
    if result.replayed:
        response.status_code = 200
        response.headers["Idempotent-Replayed"] = "true"
    return _message_summary(msg)


@router.get("/messages")
def list_messages(status: Optional[str] = None, event_type: Optional[str] = None,
                  to: Optional[str] = None, external_reference: Optional[str] = None,
                  created_after: Optional[datetime] = None, created_before: Optional[datetime] = None,
                  cursor: Optional[str] = None, limit: int = Query(50, ge=1, le=100),
                  principal: EmailPrincipal = Depends(require_scopes("email:read")),
                  db: Session = Depends(get_db)):
    q = db.query(EmailMessage).filter(EmailMessage.company_id == principal.tenant_id,
                                      EmailMessage.is_test.is_(principal.is_test))
    if status:
        q = q.filter(EmailMessage.status == status)
    if event_type:
        q = q.filter(EmailMessage.event_type == event_type)
    if external_reference:
        q = q.filter(EmailMessage.external_reference == external_reference)
    if created_after:
        q = q.filter(EmailMessage.created_at >= created_after)
    if created_before:
        q = q.filter(EmailMessage.created_at < created_before)
    if to:
        ids = (db.query(EmailRecipient.message_id)
               .filter(EmailRecipient.company_id == principal.tenant_id, EmailRecipient.email == to))
        q = q.filter(EmailMessage.id.in_(ids))
    if cursor:
        try:
            ts, last_id = base64.urlsafe_b64decode(cursor.encode()).decode().split("|", 1)
            ts = datetime.fromisoformat(ts)
        except Exception:
            raise _err(422, "cursor_invalid", "cursor is not valid")
        q = q.filter((EmailMessage.created_at < ts) |
                     ((EmailMessage.created_at == ts) & (EmailMessage.id < last_id)))
    rows = q.order_by(EmailMessage.created_at.desc(), EmailMessage.id.desc()).limit(limit + 1).all()
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more and rows:
        last = rows[-1]
        next_cursor = base64.urlsafe_b64encode(f"{last.created_at.isoformat()}|{last.id}".encode()).decode()
    return {"messages": [_message_summary(m) for m in rows], "next_cursor": next_cursor}


@router.get("/messages/{message_id}")
def get_message(message_id: str, principal: EmailPrincipal = Depends(require_scopes("email:read")),
                db: Session = Depends(get_db)):
    return _message_detail(db, _message(db, principal, message_id))


@router.post("/messages/{message_id}/retry")
def retry_message(message_id: str, body: Optional[schemas.RetryMessage] = None,
                  principal: EmailPrincipal = Depends(require_scopes("email:send")),
                  db: Session = Depends(get_db)):
    msg = _message(db, principal, message_id)
    try:
        M.retry(db, msg, confirm_duplicate_risk=bool(body and body.confirm_duplicate_risk),
                actor=principal.actor())
    except M.SubmitError as e:
        db.rollback()
        raise _err(e.status_code, e.code, str(e))
    db.commit()
    return _message_summary(msg)


@router.post("/messages/{message_id}/cancel")
def cancel_message(message_id: str, principal: EmailPrincipal = Depends(require_scopes("email:send")),
                   db: Session = Depends(get_db)):
    msg = _message(db, principal, message_id)
    try:
        M.cancel(db, msg)
    except M.SubmitError as e:
        db.rollback()
        raise _err(e.status_code, e.code, str(e))
    db.commit()
    return _message_summary(msg)


# ── connections ─────────────────────────────────────────────────────────────

def _connection(db, principal, connection_id) -> EmailConnection:
    conn = (db.query(EmailConnection)
            .filter(EmailConnection.company_id == principal.tenant_id,
                    EmailConnection.id == connection_id)
            .first())
    if conn is None:
        raise _not_found("Connection")
    return conn


def _live_only(principal: EmailPrincipal) -> None:
    if principal.is_test:
        raise _err(403, "live_key_required", "Test keys cannot change or use real mailboxes")


@router.get("/connections")
def list_connections(principal: EmailPrincipal = Depends(require_scopes("connections:read")),
                     db: Session = Depends(get_db)):
    rows = (db.query(EmailConnection)
            .filter(EmailConnection.company_id == principal.tenant_id)
            .order_by(EmailConnection.created_at)
            .all())
    return {"connections": [conns.public(c) for c in rows]}


@router.post("/connections/smtp", status_code=201)
def create_smtp_connection(body: schemas.CreateSmtpConnection,
                           principal: EmailPrincipal = Depends(require_scopes("connections:write")),
                           db: Session = Depends(get_db)):
    _live_only(principal)
    try:
        # Check the sender address before touching the network.
        S.classify_address(body.sender_email, body.username)
        conn = conns.create_smtp(
            db, company_id=principal.tenant_id, name=body.name, host=body.host, port=body.port,
            security=body.security, username=body.username, password=body.password,
            actor_user_id=principal.user_id, hourly_limit=body.hourly_limit)
        sender = S.create(db, company_id=principal.tenant_id, conn=conn, email=body.sender_email,
                          label=body.sender_label or body.name, display_name=body.sender_name,
                          reply_to=body.reply_to, actor_user_id=principal.user_id)
    except (conns.ConnectionError_, S.SenderError) as e:
        db.rollback()
        raise _err(422, e.code, str(e))
    db.commit()
    return {"connection": conns.public(conn), "sender": S.public(sender, conn)}


@router.post("/connections/{connection_id}/test")
def test_connection(connection_id: str, body: Optional[schemas.TestConnection] = None,
                    principal: EmailPrincipal = Depends(require_scopes("connections:write")),
                    db: Session = Depends(get_db)):
    _live_only(principal)
    conn = _connection(db, principal, connection_id)
    sender = (db.query(EmailSender)
              .filter(EmailSender.company_id == principal.tenant_id,
                      EmailSender.connection_id == conn.id, EmailSender.deleted_at.is_(None))
              .order_by(EmailSender.created_at)
              .first())
    if sender is None:
        raise _err(409, C.R_SENDER_NOT_FOUND, "This connection has no sender to test with")
    to = (body.to if body and body.to else sender.email).strip()
    if to.lower() != sender.email.lower():
        # A controlled destination only: the sender itself, or someone in the company.
        known = (db.query(User.id)
                 .filter(User.company_id == principal.tenant_id, User.email == to,
                         User.is_active.is_(True))
                 .first())
        if not known:
            raise _err(422, "test_recipient_not_allowed",
                       "Test emails go to the sender's own address or an active user of your company")
    try:
        result = conns.send_test(db, company_id=principal.tenant_id, conn=conn, sender=sender,
                                 to=to, actor=principal.actor())
    except conns.ConnectionError_ as e:
        db.rollback()
        raise _err(429 if e.code == "test_rate_limited" else 422, e.code, str(e))
    db.commit()
    return {**result, "connection": conns.public(conn)}


@router.post("/connections/{connection_id}/disconnect")
def disconnect_connection(connection_id: str,
                          principal: EmailPrincipal = Depends(require_scopes("connections:write")),
                          db: Session = Depends(get_db)):
    _live_only(principal)
    conn = _connection(db, principal, connection_id)
    conns.disconnect(db, conn)
    db.commit()
    return {"connection": conns.public(conn)}


# ── senders ─────────────────────────────────────────────────────────────────

def _sender(db, principal, sender_id) -> EmailSender:
    sender, _ = S.load(db, principal.tenant_id, sender_id)
    if sender is None or sender.deleted_at is not None:
        raise _not_found("Sender")
    return sender


@router.get("/senders")
def list_senders(principal: EmailPrincipal = Depends(require_scopes("senders:read")),
                 db: Session = Depends(get_db)):
    rows = (db.query(EmailSender)
            .filter(EmailSender.company_id == principal.tenant_id, EmailSender.deleted_at.is_(None))
            .order_by(EmailSender.label)
            .all())
    by_id = {c.id: c for c in db.query(EmailConnection)
             .filter(EmailConnection.company_id == principal.tenant_id).all()}
    return {"senders": [S.public(s, by_id.get(s.connection_id)) for s in rows]}


@router.post("/senders", status_code=201)
def create_sender(body: schemas.CreateSender,
                  principal: EmailPrincipal = Depends(require_scopes("senders:write")),
                  db: Session = Depends(get_db)):
    _live_only(principal)
    conn = _connection(db, principal, body.connection_id)
    try:
        sender = S.create(db, company_id=principal.tenant_id, conn=conn, email=body.email,
                          label=body.label, display_name=body.display_name,
                          reply_to=body.reply_to, actor_user_id=principal.user_id)
    except S.SenderError as e:
        db.rollback()
        raise _err(422, e.code, str(e))
    db.commit()
    return S.public(sender, conn)


@router.patch("/senders/{sender_id}")
def update_sender(sender_id: str, body: schemas.UpdateSender,
                  principal: EmailPrincipal = Depends(require_scopes("senders:write")),
                  db: Session = Depends(get_db)):
    _live_only(principal)
    sender = _sender(db, principal, sender_id)
    try:
        S.update(db, sender, **body.model_dump(exclude_unset=True))
    except S.SenderError as e:
        db.rollback()
        raise _err(422, e.code, str(e))
    db.commit()
    conn = (db.query(EmailConnection)
            .filter(EmailConnection.company_id == principal.tenant_id,
                    EmailConnection.id == sender.connection_id)
            .first())
    return S.public(sender, conn)


# ── suppressions ────────────────────────────────────────────────────────────

@router.get("/suppressions")
def list_suppressions(principal: EmailPrincipal = Depends(require_scopes("email:read")),
                      db: Session = Depends(get_db)):
    rows = (db.query(EmailSuppression)
            .filter(EmailSuppression.company_id == principal.tenant_id)
            .order_by(EmailSuppression.created_at.desc())
            .limit(500)
            .all())
    return {"suppressions": [{"email": r.email, "reason": r.reason, "source": r.source,
                              "created_at": _iso(r.created_at)} for r in rows]}


@router.delete("/suppressions/{email}")
def delete_suppression(email: str, principal: EmailPrincipal = Depends(require_scopes("senders:write")),
                       db: Session = Depends(get_db)):
    n = (db.query(EmailSuppression)
         .filter(EmailSuppression.company_id == principal.tenant_id,
                 EmailSuppression.email == email.strip().lower())
         .delete(synchronize_session=False))
    if not n:
        raise _not_found("Suppression")
    db.commit()
    return {"deleted": email.strip().lower()}


# ── API keys (admin session only — a key cannot mint keys) ─────────────────

def _key(db, admin: User, key_id: str) -> TenantApiKey:
    row = (db.query(TenantApiKey)
           .filter(TenantApiKey.company_id == admin.company_id, TenantApiKey.id == key_id)
           .first())
    if row is None:
        raise _not_found("API key")
    return row


def _audit(db, request, admin, action, row, detail=None):
    from src.services.audit_log import record_audit
    record_audit(db, action=action, actor=admin, company_id=admin.company_id,
                 resource=f"api_key:{row.id}", request=request,
                 detail={"key_prefix": row.key_prefix, **(detail or {})}, commit=False)


def _admin_company(admin: User) -> str:
    if not admin.company_id:
        raise _err(400, "no_company", "Your account is not attached to a company")
    return admin.company_id


@keys_router.post("", status_code=201)
def create_api_key(body: schemas.CreateApiKey, request: Request,
                   admin: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    company_id = _admin_company(admin)
    try:
        row, token = api_keys.create(db, company_id=company_id, name=body.name, mode=body.mode,
                                     scopes=body.scopes, actor_user_id=admin.id,
                                     expires_at=body.expires_at, allowed_ips=body.allowed_ips,
                                     hourly_quota=body.hourly_quota)
    except api_keys.ApiKeyError as e:
        db.rollback()
        raise _err(e.status_code, e.code, str(e))
    _audit(db, request, admin, "api_key.create", row, {"mode": row.mode, "scopes": row.scopes})
    db.commit()
    return {**api_keys.public(row), "token": token,
            "token_notice": "Store this key now. It is shown once and cannot be retrieved again."}


@keys_router.get("")
def list_api_keys(admin: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    company_id = _admin_company(admin)
    rows = (db.query(TenantApiKey)
            .filter(TenantApiKey.company_id == company_id)
            .order_by(TenantApiKey.created_at.desc())
            .all())
    return {"api_keys": [api_keys.public(r) for r in rows], "scopes": list(C.SCOPES)}


@keys_router.post("/{key_id}/revoke")
def revoke_api_key(key_id: str, request: Request, admin: User = Depends(require_role(["admin"])),
                   db: Session = Depends(get_db)):
    row = _key(db, admin, key_id)
    api_keys.revoke(db, row, admin.id)
    _audit(db, request, admin, "api_key.revoke", row)
    db.commit()
    return api_keys.public(row)


@keys_router.post("/{key_id}/rotate", status_code=201)
def rotate_api_key(key_id: str, request: Request, body: Optional[schemas.RotateApiKey] = None,
                   admin: User = Depends(require_role(["admin"])), db: Session = Depends(get_db)):
    row = _key(db, admin, key_id)
    overlap = body.overlap_hours if body else 24
    try:
        new_row, token = api_keys.rotate(db, row, overlap_hours=overlap, actor_user_id=admin.id)
    except api_keys.ApiKeyError as e:
        db.rollback()
        raise _err(e.status_code, e.code, str(e))
    _audit(db, request, admin, "api_key.rotate", new_row,
           {"rotated_from": row.id, "old_key_expires_at": _iso(row.expires_at)})
    db.commit()
    return {**api_keys.public(new_row), "token": token, "previous": api_keys.public(row),
            "token_notice": "Store this key now. It is shown once and cannot be retrieved again."}
