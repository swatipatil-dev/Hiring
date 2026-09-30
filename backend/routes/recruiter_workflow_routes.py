"""
🧑‍💼 RECRUITER WORKFLOW ROUTES — /api/v1/hr/workflow

Stages and deadlines, pending actions and reminders, duplicate detection and
merging, preview-then-confirm bulk actions, and source tracking. The logic lives in
the services named on each section; these handlers authorise and translate.

Visibility follows advanced_features_routes: HR reach the candidates on jobs they own
or co-own, admins the whole company, and every lookup names the caller's company.
"""

import os
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.auth import AuthMiddleware
from src.config.database_config_mysql import get_db
from src.models.models import BulkOperation, Candidate, CandidateMerge, User
from routes.advanced_features_routes import _accessible_job_ids, _visible_candidates
from src.services import bulk_actions, candidate_duplicates, candidate_source, pipeline_stages

router = APIRouter(prefix="/api/v1/hr/workflow", tags=["Recruiter Workflow"])


def _candidate_or_404(db, candidate_id, user) -> Candidate:
    c = _visible_candidates(db, user).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(404, "Candidate not found")
    return c


def _job_scope(db, user):
    """None for admins (whole company), else the HR's accessible job ids."""
    if user.role == "admin":
        return None
    return [jid for (jid,) in db.execute(_accessible_job_ids(db, user)).all()]


# ─── stages, owners, deadlines (src/services/pipeline_stages.py) ─────────────

class StageIn(BaseModel):
    key: str
    label: str
    owner_user_id: Optional[str] = None
    sla_days: Optional[int] = None
    is_active: bool = True


class StagesIn(BaseModel):
    stages: List[StageIn]


@router.get("/stages")
async def get_stages(current_user: User = Depends(AuthMiddleware.get_current_hr),
                     db: Session = Depends(get_db)):
    return {"stages": pipeline_stages.list_stages(db, current_user.company_id),
            "due_soon_hours": pipeline_stages.DUE_SOON_HOURS}


@router.put("/stages")
async def put_stages(body: StagesIn, request: Request,
                     current_user: User = Depends(AuthMiddleware.get_current_hr),
                     db: Session = Depends(get_db)):
    from src.services.audit_log import record_audit
    pipeline_stages.save_stages(db, current_user.company_id, [s.model_dump() for s in body.stages])
    record_audit(db, action="hr.stages.update", actor=current_user,
                 company_id=current_user.company_id, resource="pipeline_stage_configs",
                 request=request, detail={"stages": [s.key for s in body.stages]}, commit=False)
    db.commit()
    return {"stages": pipeline_stages.list_stages(db, current_user.company_id)}


@router.get("/stage-owners")
async def stage_owner_choices(current_user: User = Depends(AuthMiddleware.get_current_hr),
                              db: Session = Depends(get_db)):
    users = db.query(User).filter(
        User.company_id == current_user.company_id,
        User.role.in_(["hr", "admin"]), User.is_active.is_(True),
    ).order_by(User.first_name, User.last_name).all()
    return {"owners": [{"id": u.id, "name": f"{u.first_name} {u.last_name}".strip(),
                        "email": u.email, "role": u.role} for u in users]}


@router.get("/pending-actions")
async def get_pending_actions(
    scope: str = Query("mine", pattern="^(mine|all)$"),
    status: str = Query("due", pattern="^(due|overdue|due_soon|all)$"),
    job_id: Optional[str] = None,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db),
):
    """Candidates waiting on an action. ``scope=mine`` = owned by me; ``status=due`` =
    overdue or due soon (the default), ``all`` includes those still on track."""
    statuses = {"due": ("overdue", "due_soon"), "overdue": ("overdue",),
                "due_soon": ("due_soon",), "all": None}[status]
    job_ids = _job_scope(db, current_user)
    if job_id:
        if job_ids is not None and job_id not in job_ids:
            raise HTTPException(404, "Job not found")
        job_ids = [job_id]
    items = pipeline_stages.pending_actions(
        db, current_user.company_id, job_ids=job_ids, statuses=statuses,
        owner_user_id=current_user.id if scope == "mine" else None)
    public = pipeline_stages.public_items(items)
    return {
        "items": public,
        "counts": {"overdue": sum(1 for i in public if i["status"] == "overdue"),
                   "due_soon": sum(1 for i in public if i["status"] == "due_soon"),
                   "on_track": sum(1 for i in public if i["status"] == "on_track")},
    }


@router.post("/reminders/run")
async def run_reminders(request: Request, db: Session = Depends(get_db)):
    """Send pending-action reminders. Safe on a timer (idempotent within a day).

    Auth: the ``WORKFLOW_REMINDER_TOKEN`` shared secret in ``X-Workflow-Reminder-Token``
    sweeps every company; an HR/admin JWT sweeps only the caller's company. Same shape
    as /judge/regrade-pending, and for the same reason both branches bind the tenant.
    """
    from routes.judge_routes import _shared_secret_matches
    from src.auth import resolve_token_user
    from src.tenancy.context import set_current_company

    company_id = None
    if _shared_secret_matches(request.headers.get("X-Workflow-Reminder-Token"),
                              "WORKFLOW_REMINDER_TOKEN"):
        set_current_company(None)
    else:
        auth = request.headers.get("Authorization", "")
        user = None
        if auth.startswith("Bearer "):
            try:
                user, _ = resolve_token_user(db, auth[7:])
            except HTTPException:
                user = None
        if not user or user.role not in ("hr", "admin"):
            raise HTTPException(401, "Unauthorized")
        company_id = user.company_id
        set_current_company(company_id)
    send_email = os.getenv("WORKFLOW_REMINDER_EMAIL", "1").lower() not in ("0", "false", "no")
    return pipeline_stages.run_reminders(db, company_id=company_id, send_email=send_email)


# ─── duplicates & merging (src/services/candidate_duplicates.py) ─────────────

class MergeIn(BaseModel):
    primary_id: str
    merged_id: str
    choices: Dict[str, str] = {}
    reason: Optional[str] = None
    confirm: bool = False


@router.get("/candidates/{candidate_id}/duplicates")
async def candidate_duplicates_for(candidate_id: str,
                                   current_user: User = Depends(AuthMiddleware.get_current_hr),
                                   db: Session = Depends(get_db)):
    c = _candidate_or_404(db, candidate_id, current_user)
    return {"candidate_id": c.id,
            "matches": candidate_duplicates.find_duplicates_for(db, c, _job_scope(db, current_user))}


@router.get("/duplicates")
async def list_duplicate_groups(job_id: Optional[str] = None, include_cross_job: bool = False,
                                current_user: User = Depends(AuthMiddleware.get_current_hr),
                                db: Session = Depends(get_db)):
    job_ids = _job_scope(db, current_user)
    if job_id:
        if job_ids is not None and job_id not in job_ids:
            raise HTTPException(404, "Job not found")
        job_ids = [job_id]
    groups = candidate_duplicates.find_duplicate_groups(
        db, current_user.company_id, job_ids=job_ids, include_cross_job=include_cross_job)
    return {"groups": groups, "total": len(groups)}


@router.post("/merge/preview")
async def merge_preview(body: MergeIn, current_user: User = Depends(AuthMiddleware.get_current_hr),
                        db: Session = Depends(get_db)):
    _candidate_or_404(db, body.primary_id, current_user)
    _candidate_or_404(db, body.merged_id, current_user)
    return candidate_duplicates.preview_merge(db, current_user.company_id, body.primary_id, body.merged_id)


@router.post("/merge")
async def merge(body: MergeIn, request: Request,
                current_user: User = Depends(AuthMiddleware.get_current_hr),
                db: Session = Depends(get_db)):
    if not body.confirm:
        raise HTTPException(400, "Set confirm=true to merge — preview the merge first.")
    _candidate_or_404(db, body.primary_id, current_user)
    _candidate_or_404(db, body.merged_id, current_user)
    from src.services.audit_log import record_audit
    record = candidate_duplicates.merge_candidates(
        db, current_user.company_id, body.primary_id, body.merged_id, body.choices,
        current_user, reason=body.reason)
    record_audit(db, action="hr.candidate.merge", actor=current_user,
                 company_id=current_user.company_id, resource=f"candidate_merges/{record.id}",
                 request=request, detail={"primary": body.primary_id, "merged": body.merged_id},
                 commit=False)
    db.commit()
    return {"merge": candidate_duplicates.merge_view(record)}


@router.get("/merges")
async def list_merges(candidate_id: Optional[str] = None,
                      current_user: User = Depends(AuthMiddleware.get_current_hr),
                      db: Session = Depends(get_db)):
    q = db.query(CandidateMerge).filter(CandidateMerge.company_id == current_user.company_id)
    if candidate_id:
        _candidate_or_404(db, candidate_id, current_user)
        q = q.filter((CandidateMerge.primary_candidate_id == candidate_id) |
                     (CandidateMerge.merged_candidate_id == candidate_id))
    visible = {c.id for c in _visible_candidates(db, current_user).with_entities(Candidate.id)} \
        if current_user.role != "admin" else None
    merges = [m for m in q.order_by(CandidateMerge.created_at.desc()).limit(200).all()
              if visible is None or m.primary_candidate_id in visible]
    return {"merges": [candidate_duplicates.merge_view(m) for m in merges]}


@router.post("/merges/{merge_id}/undo")
async def undo_merge(merge_id: str, request: Request,
                     current_user: User = Depends(AuthMiddleware.get_current_hr),
                     db: Session = Depends(get_db)):
    m = db.query(CandidateMerge).filter(CandidateMerge.id == merge_id,
                                        CandidateMerge.company_id == current_user.company_id).first()
    if not m:
        raise HTTPException(404, "Merge not found")
    _candidate_or_404(db, m.primary_candidate_id, current_user)
    from src.services.audit_log import record_audit
    record = candidate_duplicates.undo_merge(db, current_user.company_id, merge_id, current_user)
    record_audit(db, action="hr.candidate.merge_undo", actor=current_user,
                 company_id=current_user.company_id, resource=f"candidate_merges/{record.id}",
                 request=request, commit=False)
    db.commit()
    return {"merge": candidate_duplicates.merge_view(record)}


# ─── bulk actions (src/services/bulk_actions.py) ─────────────────────────────

class BulkPreviewIn(BaseModel):
    action: str
    candidate_ids: List[str]
    target_stage: Optional[str] = None
    note: Optional[str] = None
    notify_candidates: bool = True


class BulkExecuteIn(BaseModel):
    confirm_count: int


def _users_by_id(db, ids):
    ids = {i for i in ids if i}
    return {u.id: u for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}


@router.post("/bulk/preview")
async def bulk_preview(body: BulkPreviewIn, current_user: User = Depends(AuthMiddleware.get_current_hr),
                       db: Session = Depends(get_db)):
    op = bulk_actions.preview_bulk(
        db, current_user, _visible_candidates(db, current_user), body.action, body.candidate_ids,
        {"target_stage": body.target_stage, "note": body.note,
         "notify_candidates": body.notify_candidates})
    db.commit()
    return bulk_actions.op_view(op, _users_by_id(db, [op.created_by_id]))


@router.post("/bulk/{op_id}/execute")
async def bulk_execute(op_id: str, body: BulkExecuteIn, request: Request,
                       current_user: User = Depends(AuthMiddleware.get_current_hr),
                       db: Session = Depends(get_db)):
    op, rejected = bulk_actions.execute_bulk(
        db, current_user, op_id, body.confirm_count, _visible_candidates(db, current_user),
        request=request)
    db.commit()
    emails_failed = 0
    if rejected:
        from src.services.email_service import notify_candidate_rejected
        for c in rejected:
            if not notify_candidate_rejected(db, c).get("ok", True):
                emails_failed += 1
    out = bulk_actions.op_view(op, _users_by_id(db, [op.created_by_id]))
    out["rejection_emails"] = {"attempted": len(rejected), "failed": emails_failed}
    return out


@router.post("/bulk/{op_id}/cancel")
async def bulk_cancel(op_id: str, current_user: User = Depends(AuthMiddleware.get_current_hr),
                      db: Session = Depends(get_db)):
    op = bulk_actions.cancel_bulk(db, current_user, op_id)
    db.commit()
    return bulk_actions.op_view(op, _users_by_id(db, [op.created_by_id]), include_detail=False)


@router.get("/bulk")
async def bulk_history(limit: int = Query(50, ge=1, le=200),
                       current_user: User = Depends(AuthMiddleware.get_current_hr),
                       db: Session = Depends(get_db)):
    """Bulk-action audit history. HR see their own batches; admins see everyone's."""
    q = db.query(BulkOperation).filter(BulkOperation.company_id == current_user.company_id,
                                       BulkOperation.status != "previewed")
    if current_user.role != "admin":
        q = q.filter(BulkOperation.created_by_id == current_user.id)
    ops = q.order_by(BulkOperation.created_at.desc()).limit(limit).all()
    users = _users_by_id(db, [o.created_by_id for o in ops])
    return {"operations": [bulk_actions.op_view(o, users, include_detail=False) for o in ops]}


@router.get("/bulk/{op_id}")
async def bulk_detail(op_id: str, current_user: User = Depends(AuthMiddleware.get_current_hr),
                      db: Session = Depends(get_db)):
    op = db.query(BulkOperation).filter(BulkOperation.id == op_id,
                                        BulkOperation.company_id == current_user.company_id).first()
    if not op or (current_user.role != "admin" and op.created_by_id != current_user.id):
        raise HTTPException(404, "Bulk action not found")
    return bulk_actions.op_view(op, _users_by_id(db, [op.created_by_id]))


# ─── source tracking (src/services/candidate_source.py) ──────────────────────

class SourceIn(BaseModel):
    source: str
    source_detail: Optional[str] = None
    referrer_name: Optional[str] = None
    referrer_email: Optional[str] = None


@router.get("/sources")
async def source_choices(current_user: User = Depends(AuthMiddleware.get_current_hr)):
    return {"sources": [{"key": k, "label": v} for k, v in candidate_source.SOURCES.items()]}


@router.patch("/candidates/{candidate_id}/source")
async def set_candidate_source(candidate_id: str, body: SourceIn, request: Request,
                               current_user: User = Depends(AuthMiddleware.get_current_hr),
                               db: Session = Depends(get_db)):
    c = _candidate_or_404(db, candidate_id, current_user)
    before = candidate_source.source_view(c)
    for k, v in candidate_source.normalize_source(
            body.source, body.source_detail, body.referrer_name, body.referrer_email).items():
        setattr(c, k, v)
    from src.services.audit_log import record_audit
    record_audit(db, action="hr.candidate.source", actor=current_user,
                 company_id=current_user.company_id, resource=f"candidates/{c.id}",
                 request=request, detail={"before": before.get("source"), "after": c.source},
                 commit=False)
    db.commit()
    return candidate_source.source_view(c)


@router.get("/sources/report")
async def source_report(job_id: Optional[str] = None,
                        current_user: User = Depends(AuthMiddleware.get_current_hr),
                        db: Session = Depends(get_db)):
    """Applications and conversion by source, for the caller's jobs."""
    job_ids = _job_scope(db, current_user)
    if job_id:
        if job_ids is not None and job_id not in job_ids:
            raise HTTPException(404, "Job not found")
        job_ids = [job_id]
    q = db.query(Candidate.source, Candidate.source_detail, Candidate.current_stage).filter(
        Candidate.company_id == current_user.company_id,
        Candidate.job_id.isnot(None),
        Candidate.merged_into_id.is_(None),
    )
    if job_ids is not None:
        q = q.filter(Candidate.job_id.in_(job_ids))
    rows = candidate_source.source_report(q.all())
    return {"sources": rows, "total": sum(r["total"] for r in rows)}
