"""
🧾 AUDIT LOG — privileged access to client data

Every superadmin request, every read of a client company's data by platform
support, and every support-access grant is recorded in `audit_log`. Rows are
append-only: the before_flush guard below refuses to update or delete one, so
no route can quietly edit the trail.

Support access (the "restrict" half)
------------------------------------
Platform staff (superadmin) may read a client company's candidate and interview
data only while that company's admin has an active `SupportAccessGrant` — a
time-limited, revocable consent (default 24 hours). Call
``require_support_access(db, request, user, company_id, resource)`` at the top
of any handler that returns such data; for a non-superadmin caller it does
nothing. It records the read (or the refusal) either way.

Where it is enforced today (full list in docs/TENANT_ISOLATION.md → Support access):
  * superadmin_routes  GET /companies/{id} user list; POST /companies/{id}/admin once
                       the company has an admin
  * mcq_api            per-interview and per-question reads and writes
  * proctoring/routes  results, evidence, reviews and live sessions
  * judge_routes       problems and submissions by id, and the live-status WebSocket
  * interview_access   staff_can_access_interview (evaluation and judge grading routes)
Cross-tenant lists are refused to superadmin (refuse_superadmin_listing). Aggregate
counts and company profile/branding are not candidate data and need no grant, but every
superadmin request on the superadmin, support and database routers is audited.

Superadmin access fails closed: an access whose audit row cannot be written is refused
(_audit_or_deny → 503).
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy import event
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.config.database_config_mysql import get_db
from src.models.models import AuditLog, SupportAccessGrant, User

logger = logging.getLogger(__name__)

DEFAULT_GRANT_HOURS = 24
MAX_GRANT_HOURS = 24 * 7


@event.listens_for(Session, "before_flush")
def _audit_log_is_append_only(session, flush_context, instances):
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, AuditLog):
            raise RuntimeError("audit_log rows are append-only")


def _client_ip(request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    try:
        from src.rate_limit import client_ip_key
        return client_ip_key(request)[:64]
    except Exception:
        return None


def record_audit(db: Optional[Session], *, action: str, actor: Optional[User] = None,
                 company_id: Optional[str] = None, resource: Optional[str] = None,
                 request: Optional[Request] = None, detail=None,
                 commit: bool = True, required: bool = False) -> None:
    """Append one audit row. Never raises into the caller: a failure is logged.

    Commits by default so the row survives the request failing afterwards (a
    refused access raises straight after recording it). Call it before the
    handler has pending writes of its own, or pass commit=False and commit with them.

    ``required=True`` makes it fail closed instead: a failed write is re-raised as
    AuditUnavailable, for accesses that must not happen unrecorded.
    """
    own_session = db is None
    if own_session:
        from src.config.database_config_mysql import db_manager
        db = db_manager.get_db_session()
    try:
        row = AuditLog(
            company_id=company_id,
            actor_user_id=str(actor.id) if actor is not None else None,
            actor_email=getattr(actor, "email", None),
            actor_role=getattr(actor, "role", None),
            action=action[:100],
            resource=(resource or "")[:500] or None,
            method=request.method if request is not None else None,
            path=str(request.url.path)[:500] if request is not None else None,
            ip=_client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:255] or None
            if request is not None else None,
            detail=detail if (detail is None or isinstance(detail, str))
            else json.dumps(detail, default=str),
        )
        db.add(row)
        if commit:
            db.commit()
    except Exception as exc:
        logger.error("audit_log write failed (%s %s): %s", action, resource, exc)
        try:
            db.rollback()
        except Exception:
            pass
        if required:
            raise AuditUnavailable(str(exc)) from exc
    finally:
        if own_session:
            db.close()


class AuditUnavailable(RuntimeError):
    """A required audit row could not be written."""


def _audit_or_deny(db, **kw) -> None:
    """Record a superadmin's access to client data, or refuse the access (503).

    Support access is only acceptable because it is on the record; an access we
    cannot record is an access we do not allow.
    """
    try:
        record_audit(db, required=True, **kw)
    except AuditUnavailable:
        raise HTTPException(status_code=503,
                            detail="Support access is unavailable: it could not be audited.")


def _is_write(request: Optional[Request]) -> bool:
    return request is not None and request.method.upper() not in ("GET", "HEAD", "OPTIONS")


def audit_superadmin_access(request: Optional[Request], user: Optional[User], resource: str,
                            db: Optional[Session] = None,
                            company_id: Optional[str] = None) -> None:
    """Record a superadmin's read of `resource`. A no-op for every other role."""
    if user is None or (user.role or "").lower() != "superadmin":
        return
    _audit_or_deny(db, action="superadmin.write" if _is_write(request) else "superadmin.read",
                   actor=user, company_id=company_id, resource=resource, request=request)


def audit_superadmin_request(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    """Router-level dependency: record every request a superadmin makes.

    Wired onto the superadmin, support and database-management routers. Runs before
    the handler, so the row is committed with nothing else pending, and it records
    the attempt even if the handler then fails.
    """
    if (user.role or "").lower() != "superadmin":
        return
    _audit_or_deny(db, action="superadmin.request", actor=user,
                   company_id=_request_company(db, request),
                   resource=f"{request.method} {request.url.path}", request=request)


def _request_company(db: Session, request: Request) -> Optional[str]:
    """The client company a superadmin request is about, so the row reaches that
    company's access log: the {company_id} in the path, or a support ticket's company."""
    params = request.path_params
    if params.get("company_id"):
        return params["company_id"]
    if params.get("ticket_id"):
        from src.models.models import SupportRequest
        row = (db.query(SupportRequest.company_id)
               .execution_options(_tenant_bypass=True)
               .filter(SupportRequest.id == params["ticket_id"]).first())
        return row[0] if row else None
    return None


def active_grant(db: Session, company_id: Optional[str]) -> Optional[SupportAccessGrant]:
    if not company_id:
        return None
    now = datetime.utcnow()
    return (
        db.query(SupportAccessGrant)
        .execution_options(_tenant_bypass=True)
        .filter(
            SupportAccessGrant.company_id == company_id,
            SupportAccessGrant.revoked_at.is_(None),
            SupportAccessGrant.expires_at > now,
        )
        .order_by(SupportAccessGrant.expires_at.desc())
        .first()
    )


def require_support_access(db: Session, request: Optional[Request], user: Optional[User],
                           company_id: Optional[str], resource: str) -> Optional[SupportAccessGrant]:
    """Gate a superadmin's read of a company's candidate/interview data.

    Non-superadmin callers pass straight through (their own tenant checks apply).
    A superadmin needs an active grant from that company's admin; the read or the
    refusal is audited either way.
    """
    if user is None or (user.role or "").lower() != "superadmin":
        return
    grant = active_grant(db, company_id)
    if grant is None:
        record_audit(db, action="support_access.denied", actor=user, company_id=company_id,
                     resource=resource, request=request)
        raise HTTPException(
            status_code=403,
            detail="This company has not granted support access. Ask its admin to grant "
                   "access from Settings → Support access.",
        )
    _audit_or_deny(db, action="superadmin.write" if _is_write(request) else "superadmin.read",
                   actor=user, company_id=company_id, resource=resource, request=request,
                   detail={"grant_id": grant.id})
    return grant


def refuse_superadmin_listing(user: Optional[User], what: str) -> None:
    """Cross-tenant lists are not support access: a superadmin reaches one company's
    records, by id, under that company's grant. Listing every tenant's is refused."""
    if user is not None and (user.role or "").lower() == "superadmin":
        raise HTTPException(status_code=403,
                            detail=f"{what} are listed per company; superadmins open a "
                                   f"specific record under that company's support-access grant.")


def grant_hours(hours: Optional[float]) -> float:
    if hours is None:
        return DEFAULT_GRANT_HOURS
    return max(1.0, min(float(hours), MAX_GRANT_HOURS))


def new_grant_expiry(hours: Optional[float]) -> datetime:
    return datetime.utcnow() + timedelta(hours=grant_hours(hours))
