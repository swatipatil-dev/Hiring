"""
Support / Help Ticket Routes
─────────────────────────────
Lets a client-company admin open a threaded help request that the platform
superadmin can triage and reply to. Each side gets an in-app Notification and
an email when the other side acts.

Admin side (role=admin, tenant-scoped):
    POST   /api/v1/support/requests
    GET    /api/v1/support/requests
    GET    /api/v1/support/requests/{id}
    POST   /api/v1/support/requests/{id}/messages

Superadmin side (require_superadmin):
    GET    /api/v1/support/admin/requests
    GET    /api/v1/support/admin/requests/{id}
    POST   /api/v1/support/admin/requests/{id}/messages
    PUT    /api/v1/support/admin/requests/{id}/status

Support access (company admin consents to platform support reading its data):
    POST   /api/v1/support/access-grants          grant, default 24h, max 7 days
    GET    /api/v1/support/access-grants          this company's grants
    DELETE /api/v1/support/access-grants/{id}     revoke now
    GET    /api/v1/support/access-log             what platform staff did with this
                                                  company's data (from audit_log)
"""

import os
import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.config.database_config_mysql import get_db
from src.auth import AuthMiddleware, get_current_user
from src.models.models import (
    Company, User, Notification, SupportRequest, SupportMessage,
    SupportAccessGrant, AuditLog,
)
from src.services import email_service
from src.services.audit_log import (
    audit_superadmin_request, record_audit, new_grant_expiry, DEFAULT_GRANT_HOURS,
    MAX_GRANT_HOURS,
)
from routes.superadmin_routes import require_superadmin

logger = logging.getLogger(__name__)

# Superadmin requests on this router are written to audit_log; others pass through.
router = APIRouter(
    prefix="/api/v1/support",
    tags=["Support"],
    dependencies=[Depends(audit_superadmin_request)],
)

ALLOWED_CATEGORIES = {"general", "billing", "technical", "feature", "other"}
ALLOWED_PRIORITIES = {"low", "normal", "high", "urgent"}
ALLOWED_STATUSES = {"open", "in_progress", "resolved", "closed"}
PLATFORM_BASE_URL = os.getenv("PLATFORM_BASE_URL", "http://localhost:3000")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TicketCreate(BaseModel):
    subject: str = Field(..., min_length=3, max_length=255)
    body: str = Field(..., min_length=1)
    category: str = "general"
    priority: str = "normal"


class TicketReply(BaseModel):
    body: str = Field(..., min_length=1)


class TicketStatusUpdate(BaseModel):
    status: str


class MessageOut(BaseModel):
    id: str
    sender_id: str
    sender_role: str
    sender_name: str
    body: str
    created_at: datetime


class TicketSummary(BaseModel):
    id: str
    company_id: str
    company_name: Optional[str] = None
    created_by: str
    requester_name: str
    assigned_to: Optional[str] = None
    assignee_name: Optional[str] = None
    subject: str
    category: str
    priority: str
    status: str
    created_at: datetime
    updated_at: datetime
    message_count: int


class TicketDetail(TicketSummary):
    messages: List[MessageOut]
    resolved_at: Optional[datetime] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _full_name(u: Optional[User]) -> str:
    if not u:
        return "Unknown"
    return f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email


def _to_summary(req: SupportRequest, db: Session) -> TicketSummary:
    company = db.query(Company).filter(Company.id == req.company_id).first()
    creator = db.query(User).filter(User.id == req.created_by).first()
    assignee = db.query(User).filter(User.id == req.assigned_to).first() if req.assigned_to else None
    return TicketSummary(
        id=req.id,
        company_id=req.company_id,
        company_name=(company.name if company else None),
        created_by=req.created_by,
        requester_name=_full_name(creator),
        assigned_to=req.assigned_to,
        assignee_name=(_full_name(assignee) if assignee else None),
        subject=req.subject,
        category=req.category,
        priority=req.priority,
        status=req.status,
        created_at=req.created_at,
        updated_at=req.updated_at,
        message_count=len(req.messages),
    )


def _to_detail(req: SupportRequest, db: Session) -> TicketDetail:
    summary = _to_summary(req, db)
    msgs: List[MessageOut] = []
    sender_ids = {m.sender_id for m in req.messages}
    senders = {
        u.id: u for u in db.query(User).filter(User.id.in_(sender_ids)).all()
    } if sender_ids else {}
    for m in req.messages:
        msgs.append(MessageOut(
            id=m.id,
            sender_id=m.sender_id,
            sender_role=m.sender_role,
            sender_name=_full_name(senders.get(m.sender_id)),
            body=m.body,
            created_at=m.created_at,
        ))
    return TicketDetail(
        **summary.model_dump(),
        messages=msgs,
        resolved_at=req.resolved_at,
    )


def _notify(db: Session, *, user_id: str, company_id: Optional[str],
            title: str, message: str, ref_id: str) -> None:
    db.add(Notification(
        user_id=user_id,
        company_id=company_id,
        title=title,
        message=message,
        type="support",
        reference_type="support_request",
        reference_id=ref_id,
    ))


def _ticket_url_admin(ticket_id: str) -> str:
    return f"{PLATFORM_BASE_URL.rstrip('/')}/admin/support?ticket={ticket_id}"


def _ticket_url_superadmin(ticket_id: str) -> str:
    return f"{PLATFORM_BASE_URL.rstrip('/')}/superadmin/support?ticket={ticket_id}"


def _email_superadmins_new(db: Session, req: SupportRequest, body_text: str) -> None:
    """Send email + create notification rows for every active superadmin."""
    superadmins = db.query(User).filter(
        User.role == "superadmin", User.is_active == True   # noqa: E712
    ).all()
    company = db.query(Company).filter(Company.id == req.company_id).first()
    requester = db.query(User).filter(User.id == req.created_by).first()
    company_name = company.name if company else "Unknown company"
    requester_name = _full_name(requester)
    url = _ticket_url_superadmin(req.id)
    for sa in superadmins:
        _notify(db, user_id=sa.id, company_id=sa.company_id,
                title=f"New ticket: {req.subject}",
                message=f"{requester_name} ({company_name}) opened a {req.priority} ticket.",
                ref_id=req.id)
        try:
            email_service.send_support_new_to_superadmin(
                smtp_config=_platform_sender(db, "support.ticket_to_platform"),
                to=sa.email,
                company_name=company_name,
                requester_name=requester_name,
                subject=req.subject,
                priority=req.priority,
                body_text=body_text,
                ticket_url=url,
            )
        except Exception as e:
            logger.warning(f"Support email to superadmin {sa.email} failed: {e}")


def _platform_sender(db: Session, event_type: str):
    """The platform company's routed sender for one support event."""
    from src.tenancy.platform_company import get_platform_company_id
    return email_service.resolve_sender(db, get_platform_company_id(db), event_type=event_type)


def _email_superadmins_reply(db: Session, req: SupportRequest, body_text: str) -> None:
    superadmins = db.query(User).filter(
        User.role == "superadmin", User.is_active == True   # noqa: E712
    ).all()
    company = db.query(Company).filter(Company.id == req.company_id).first()
    requester = db.query(User).filter(User.id == req.created_by).first()
    company_name = company.name if company else "Unknown company"
    requester_name = _full_name(requester)
    url = _ticket_url_superadmin(req.id)
    for sa in superadmins:
        _notify(db, user_id=sa.id, company_id=sa.company_id,
                title=f"Reply on: {req.subject}",
                message=f"{requester_name} ({company_name}) replied on a ticket.",
                ref_id=req.id)
        try:
            email_service.send_support_reply_to_superadmin(
                smtp_config=_platform_sender(db, "support.reply_to_platform"),
                to=sa.email,
                company_name=company_name,
                requester_name=requester_name,
                subject=req.subject,
                body_text=body_text,
                ticket_url=url,
            )
        except Exception as e:
            logger.warning(f"Support reply email to superadmin {sa.email} failed: {e}")


def _email_admin_reply(db: Session, req: SupportRequest, body_text: str) -> None:
    admin = db.query(User).filter(User.id == req.created_by).first()
    if not admin:
        return
    _notify(db, user_id=admin.id, company_id=admin.company_id,
            title=f"Support reply: {req.subject}",
            message="The platform team replied on your ticket.",
            ref_id=req.id)
    # Platform → client: the platform's own support sender, not the client's
    # (which used to be resolved here, falling back to the environment).
    smtp_cfg = _platform_sender(db, "support.reply_to_admin")
    company = db.query(Company).filter(Company.id == admin.company_id).first()
    branding = None
    if company:
        branding = {
            "company_name": company.name,
            "color": company.primary_color,
            "logo_url": company.logo_url,
            "support_email": company.support_email,
        }
    try:
        email_service.send_support_reply_to_admin(
            to=admin.email,
            requester_name=_full_name(admin),
            subject=req.subject,
            body_text=body_text,
            ticket_url=_ticket_url_admin(req.id),
            branding=branding,
            smtp_config=smtp_cfg,
        )
    except Exception as e:
        logger.warning(f"Support reply email to admin {admin.email} failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  ADMIN (client company) ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/requests", response_model=TicketDetail, status_code=201)
def create_ticket(
    payload: TicketCreate,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    """Client admin opens a new help ticket."""
    if payload.category not in ALLOWED_CATEGORIES:
        raise HTTPException(400, f"Invalid category. Allowed: {sorted(ALLOWED_CATEGORIES)}")
    if payload.priority not in ALLOWED_PRIORITIES:
        raise HTTPException(400, f"Invalid priority. Allowed: {sorted(ALLOWED_PRIORITIES)}")
    if not current_user.company_id:
        raise HTTPException(400, "Admin has no company context")

    req = SupportRequest(
        company_id=current_user.company_id,
        created_by=current_user.id,
        subject=payload.subject.strip(),
        category=payload.category,
        priority=payload.priority,
        status="open",
    )
    db.add(req)
    db.flush()  # need req.id for the first message

    first_msg = SupportMessage(
        request_id=req.id,
        sender_id=current_user.id,
        sender_role="admin",
        body=payload.body,
    )
    db.add(first_msg)
    db.flush()

    _email_superadmins_new(db, req, payload.body)

    db.commit()
    db.refresh(req)
    return _to_detail(req, db)


@router.get("/requests", response_model=List[TicketSummary])
def list_my_tickets(
    status_filter: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    """List tickets opened by this admin's company."""
    q = db.query(SupportRequest).filter(SupportRequest.company_id == current_user.company_id)
    if status_filter:
        if status_filter not in ALLOWED_STATUSES:
            raise HTTPException(400, f"Invalid status. Allowed: {sorted(ALLOWED_STATUSES)}")
        q = q.filter(SupportRequest.status == status_filter)
    rows = q.order_by(SupportRequest.updated_at.desc()).all()
    return [_to_summary(r, db) for r in rows]


@router.get("/requests/{ticket_id}", response_model=TicketDetail)
def get_my_ticket(
    ticket_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    req = db.query(SupportRequest).filter(SupportRequest.id == ticket_id).first()
    if not req or req.company_id != current_user.company_id:
        raise HTTPException(404, "Ticket not found")
    return _to_detail(req, db)


@router.post("/requests/{ticket_id}/messages", response_model=TicketDetail)
def reply_as_admin(
    ticket_id: str,
    payload: TicketReply,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    req = db.query(SupportRequest).filter(SupportRequest.id == ticket_id).first()
    if not req or req.company_id != current_user.company_id:
        raise HTTPException(404, "Ticket not found")
    if req.status == "closed":
        raise HTTPException(400, "Ticket is closed; reopen via the platform team")

    msg = SupportMessage(
        request_id=req.id,
        sender_id=current_user.id,
        sender_role="admin",
        body=payload.body,
    )
    db.add(msg)
    # If admin replies on a resolved ticket, treat as reopened
    if req.status == "resolved":
        req.status = "in_progress"
        req.resolved_at = None
    req.updated_at = datetime.utcnow()
    db.flush()

    _email_superadmins_reply(db, req, payload.body)

    db.commit()
    db.refresh(req)
    return _to_detail(req, db)


# ══════════════════════════════════════════════════════════════════════════════
#  SUPERADMIN ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/admin/requests", response_model=List[TicketSummary])
def list_all_tickets(
    status_filter: Optional[str] = None,
    company_id: Optional[str] = None,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    q = db.query(SupportRequest)
    if status_filter:
        if status_filter not in ALLOWED_STATUSES:
            raise HTTPException(400, f"Invalid status. Allowed: {sorted(ALLOWED_STATUSES)}")
        q = q.filter(SupportRequest.status == status_filter)
    if company_id:
        q = q.filter(SupportRequest.company_id == company_id)
    rows = q.order_by(SupportRequest.updated_at.desc()).all()
    return [_to_summary(r, db) for r in rows]


@router.get("/admin/requests/{ticket_id}", response_model=TicketDetail)
def get_any_ticket(
    ticket_id: str,
    _: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    req = db.query(SupportRequest).filter(SupportRequest.id == ticket_id).first()
    if not req:
        raise HTTPException(404, "Ticket not found")
    return _to_detail(req, db)


@router.post("/admin/requests/{ticket_id}/messages", response_model=TicketDetail)
def reply_as_superadmin(
    ticket_id: str,
    payload: TicketReply,
    current_user: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    req = db.query(SupportRequest).filter(SupportRequest.id == ticket_id).first()
    if not req:
        raise HTTPException(404, "Ticket not found")
    if req.status == "closed":
        raise HTTPException(400, "Ticket is closed; reopen before replying")

    msg = SupportMessage(
        request_id=req.id,
        sender_id=current_user.id,
        sender_role="superadmin",
        body=payload.body,
    )
    db.add(msg)
    # First superadmin reply claims the ticket
    if req.status == "open":
        req.status = "in_progress"
    if not req.assigned_to:
        req.assigned_to = current_user.id
    req.updated_at = datetime.utcnow()
    db.flush()

    _email_admin_reply(db, req, payload.body)

    db.commit()
    db.refresh(req)
    return _to_detail(req, db)


@router.put("/admin/requests/{ticket_id}/status", response_model=TicketDetail)
def update_status(
    ticket_id: str,
    payload: TicketStatusUpdate,
    current_user: User = Depends(require_superadmin),
    db: Session = Depends(get_db),
):
    new_status = (payload.status or "").lower()
    if new_status not in ALLOWED_STATUSES:
        raise HTTPException(400, f"Invalid status. Allowed: {sorted(ALLOWED_STATUSES)}")

    req = db.query(SupportRequest).filter(SupportRequest.id == ticket_id).first()
    if not req:
        raise HTTPException(404, "Ticket not found")

    req.status = new_status
    if new_status == "resolved":
        req.resolved_at = datetime.utcnow()
    elif new_status in ("open", "in_progress"):
        req.resolved_at = None
    if not req.assigned_to:
        req.assigned_to = current_user.id
    req.updated_at = datetime.utcnow()

    # Let the admin know the status changed
    admin = db.query(User).filter(User.id == req.created_by).first()
    if admin:
        _notify(db, user_id=admin.id, company_id=admin.company_id,
                title=f"Ticket {new_status}: {req.subject}",
                message=f"Your support ticket is now '{new_status}'.",
                ref_id=req.id)

    db.commit()
    db.refresh(req)
    return _to_detail(req, db)


# ══════════════════════════════════════════════════════════════════════════════
#  SUPPORT ACCESS GRANTS (company admin)
# ══════════════════════════════════════════════════════════════════════════════

class GrantCreate(BaseModel):
    reason: str = Field(..., min_length=3, max_length=500)
    hours: float = Field(DEFAULT_GRANT_HOURS, gt=0, le=MAX_GRANT_HOURS)


def _grant_out(g: SupportAccessGrant) -> dict:
    now = datetime.utcnow()
    return {
        "id": g.id,
        "reason": g.reason,
        "granted_by": g.granted_by,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
        "active": g.revoked_at is None and g.expires_at is not None and g.expires_at > now,
    }


@router.post("/access-grants", status_code=201)
def create_access_grant(
    payload: GrantCreate,
    request: Request,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    """Let platform support read this company's candidate and interview data for a
    limited time. Every read they make is recorded and visible at /access-log."""
    grant = SupportAccessGrant(
        company_id=current_user.company_id,
        granted_by=current_user.id,
        reason=payload.reason.strip(),
        expires_at=new_grant_expiry(payload.hours),
    )
    db.add(grant)
    db.flush()
    record_audit(db, action="support_access.granted", actor=current_user,
                 company_id=current_user.company_id, resource=f"grant {grant.id}",
                 request=request, detail={"hours": payload.hours, "reason": grant.reason},
                 commit=False)
    db.commit()
    db.refresh(grant)
    return _grant_out(grant)


@router.get("/access-grants")
def list_access_grants(
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    rows = (
        db.query(SupportAccessGrant)
        .filter(SupportAccessGrant.company_id == current_user.company_id)
        .order_by(SupportAccessGrant.created_at.desc())
        .limit(100)
        .all()
    )
    return [_grant_out(g) for g in rows]


@router.delete("/access-grants/{grant_id}")
def revoke_access_grant(
    grant_id: str,
    request: Request,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    grant = db.query(SupportAccessGrant).filter(
        SupportAccessGrant.id == grant_id,
        SupportAccessGrant.company_id == current_user.company_id,
    ).first()
    if not grant:
        raise HTTPException(404, "Grant not found")
    if grant.revoked_at is None:
        grant.revoked_at = datetime.utcnow()
        grant.revoked_by = current_user.id
        record_audit(db, action="support_access.revoked", actor=current_user,
                     company_id=current_user.company_id, resource=f"grant {grant.id}",
                     request=request, commit=False)
        db.commit()
        db.refresh(grant)
    return _grant_out(grant)


@router.get("/access-log")
def company_access_log(
    limit: int = 100,
    offset: int = 0,
    current_user: User = Depends(AuthMiddleware.get_current_admin),
    db: Session = Depends(get_db),
):
    """What platform staff (superadmin) did with this company's data, newest first,
    plus the grants and revocations made by this company's admins."""
    limit = max(1, min(limit, 500))
    q = db.query(AuditLog).filter(AuditLog.company_id == current_user.company_id)
    total = q.count()
    rows = q.order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(max(0, offset)).limit(limit).all()
    return {
        "total": total,
        "entries": [{
            "id": r.id,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "actor_email": r.actor_email,
            "actor_role": r.actor_role,
            "action": r.action,
            "resource": r.resource,
            "ip": r.ip,
        } for r in rows],
    }
