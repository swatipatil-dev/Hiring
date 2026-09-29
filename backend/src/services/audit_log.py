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

Where it is enforced today:
  * superadmin_routes  GET /companies/{id} — HR/interviewer/candidate list only under a grant
  * mcq_api            any per-interview read (_assert_interview_in_tenant)
  * proctoring/routes  GET /results/{interview_id} and evidence frames
  * judge_routes       problem reads (_problem_or_404) and GET /submissions/{id}
  * interview_access   staff_can_access_interview (evaluation and judge grading routes)
Aggregate counts (company list)
and company profile/branding are not candidate data and need no grant, but every
superadmin request on the superadmin, support and database routers is audited.
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
                 commit: bool = True) -> None:
    """Append one audit row. Never raises into the caller: a failure is logged.

    Commits by default so the row survives the request failing afterwards (a
    refused access raises straight after recording it). Call it before the
    handler has pending writes of its own, or pass commit=False and commit with them.
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
    finally:
        if own_session:
            db.close()


def audit_superadmin_access(request: Optional[Request], user: Optional[User], resource: str,
                            db: Optional[Session] = None,
                            company_id: Optional[str] = None) -> None:
    """Record a superadmin's read of `resource`. A no-op for every other role."""
    if user is None or (user.role or "").lower() != "superadmin":
        return
    record_audit(db, action="superadmin.read", actor=user, company_id=company_id,
                 resource=resource, request=request)


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
    record_audit(db, action="superadmin.request", actor=user,
                 company_id=request.path_params.get("company_id"),
                 resource=f"{request.method} {request.url.path}", request=request)


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
                           company_id: Optional[str], resource: str) -> None:
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
    record_audit(db, action="superadmin.read", actor=user, company_id=company_id,
                 resource=resource, request=request, detail={"grant_id": grant.id})


def grant_hours(hours: Optional[float]) -> float:
    if hours is None:
        return DEFAULT_GRANT_HOURS
    return max(1.0, min(float(hours), MAX_GRANT_HOURS))


def new_grant_expiry(hours: Optional[float]) -> datetime:
    return datetime.utcnow() + timedelta(hours=grant_hours(hours))
