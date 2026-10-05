"""
🗂️ PIPELINE STAGES — configurable stages, owners, deadlines and reminders

A company's stages are the built-in DEFAULT_STAGES overlaid with its
``pipeline_stage_configs`` rows: a row can relabel, reorder, assign an owner, set a
deadline (``sla_days``) or deactivate a built-in stage, and can add a custom stage.

Deadlines are computed, not stored. A candidate entered their current stage at the
latest history row whose ``to_stage`` is that stage — so every code path that moves a
candidate (and there are many) keeps deadlines right without having to know about
them. The due time is that entry plus the stage's ``sla_days``.

The owner of a pending action is the stage's configured owner, or — when none is
set — the HR who owns the candidate's job.
"""

import html
import logging
import re
from collections import defaultdict
from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func

from src.models.models import (
    Candidate, CandidateStageHistory, HRProfile, JobPosting, Notification,
    PipelineStageConfig, StageReminderLog, User,
)
from src.services.candidate_status import TERMINAL_STAGES

logger = logging.getLogger(__name__)

# (key, label). Order is the default pipeline order.
DEFAULT_STAGES = [
    ("applied", "Applied"),
    ("ats_screening", "ATS screening"),
    ("hr_review", "HR review"),
    ("screening", "Screening"),
    ("shortlisted", "Shortlisted"),
    ("test_scheduled", "Test scheduled"),
    ("test_completed", "Test completed"),
    ("interview_scheduled", "Interview scheduled"),
    ("interview_completed", "Interview completed"),
    ("offer_sent", "Offer sent"),
    ("hired", "Hired"),
    ("rejected", "Rejected"),
    ("withdrawn", "Withdrawn"),
]
_DEFAULT_KEYS = {k for k, _ in DEFAULT_STAGES}
# Stages the rest of the app writes but that are not shown as their own column.
_LEGACY_KEYS = {"registered", "interview", "technical_interview", "assessment", "test_submitted",
                "offer", "onboarding", "merged"}

DUE_SOON_HOURS = 24
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{1,49}$")
_OWNER_ROLES = {"hr", "admin"}
_CLOSED_JOB_STATUSES = ("closed", "archived")


def _owner_name(user):
    return f"{user.first_name} {user.last_name}".strip() if user else None


def list_stages(db, company_id, include_inactive=True):
    """The company's stages in order, each with its label, owner, deadline and flags."""
    rows = {r.stage_key: r for r in db.query(PipelineStageConfig).filter(
        PipelineStageConfig.company_id == company_id).all()}
    owners = {}
    owner_ids = {r.owner_user_id for r in rows.values() if r.owner_user_id}
    if owner_ids:
        owners = {u.id: u for u in db.query(User).filter(
            User.id.in_(owner_ids), User.company_id == company_id).all()}

    stages = []
    for idx, (key, label) in enumerate(DEFAULT_STAGES):
        stages.append(_stage_view(key, label, idx * 10, rows.pop(key, None), owners, builtin=True))
    for key, row in rows.items():   # custom stages
        stages.append(_stage_view(key, row.label, row.position, row, owners, builtin=False))
    stages.sort(key=lambda s: (s["position"], s["key"]))
    if not include_inactive:
        stages = [s for s in stages if s["is_active"]]
    return stages


def _stage_view(key, default_label, default_position, row, owners, builtin):
    owner = owners.get(row.owner_user_id) if row and row.owner_user_id else None
    return {
        "key": key,
        "label": (row.label if row else None) or default_label,
        "position": row.position if row else default_position,
        "owner_user_id": owner.id if owner else None,
        "owner_name": _owner_name(owner),
        "sla_days": row.sla_days if row else None,
        "is_active": row.is_active if row else True,
        "is_builtin": builtin,
        "is_terminal": key in TERMINAL_STAGES,
    }


def valid_stage_keys(db, company_id):
    """Every stage a candidate may be moved to by hand: active configured and built-in
    stages. 'merged' is never a manual target — only a merge sets it."""
    keys = {s["key"] for s in list_stages(db, company_id, include_inactive=False)}
    return keys - {"merged"}


def save_stages(db, company_id, stages):
    """Upsert the company's stage settings from a full list (order = position).

    Built-in stages cannot be deleted, only deactivated, because other code writes
    them. A custom stage absent from the list is deactivated rather than deleted, so
    a candidate still sitting in it keeps a label. The caller commits.
    """
    if not isinstance(stages, list) or not stages:
        raise HTTPException(400, "stages must be a non-empty list")
    existing = {r.stage_key: r for r in db.query(PipelineStageConfig).filter(
        PipelineStageConfig.company_id == company_id).all()}

    owner_ids = {s.get("owner_user_id") for s in stages if s.get("owner_user_id")}
    valid_owners = set()
    if owner_ids:
        valid_owners = {u.id for u in db.query(User).filter(
            User.id.in_(owner_ids), User.company_id == company_id,
            User.role.in_(_OWNER_ROLES), User.is_active.is_(True)).all()}
    bad = owner_ids - valid_owners
    if bad:
        raise HTTPException(400, "Stage owners must be active HR or admin users in your company.")

    seen = set()
    for pos, s in enumerate(stages):
        key = (s.get("key") or "").strip().lower()
        if not _KEY_RE.match(key):
            raise HTTPException(400, f"Invalid stage key '{key}': use lowercase letters, digits and _")
        if key in seen:
            raise HTTPException(400, f"Stage '{key}' is listed twice")
        if key in _LEGACY_KEYS:
            raise HTTPException(400, f"'{key}' is a reserved stage name")
        seen.add(key)
        label = (s.get("label") or "").strip()[:100]
        if not label:
            raise HTTPException(400, f"Stage '{key}' needs a label")
        sla = s.get("sla_days")
        if sla in ("", None):
            sla = None
        else:
            try:
                sla = int(sla)
            except (TypeError, ValueError):
                raise HTTPException(400, f"Deadline for '{key}' must be a whole number of days")
            if sla < 1 or sla > 365:
                raise HTTPException(400, f"Deadline for '{key}' must be between 1 and 365 days")
        if key in TERMINAL_STAGES and sla is not None:
            raise HTTPException(400, f"'{key}' is a final stage and cannot have a deadline")
        is_active = bool(s.get("is_active", True))
        if key in TERMINAL_STAGES and not is_active:
            raise HTTPException(400, f"'{key}' is a final stage and cannot be turned off")

        row = existing.get(key)
        if row is None:
            row = PipelineStageConfig(company_id=company_id, stage_key=key)
            db.add(row)
        row.label = label
        row.position = pos * 10
        row.owner_user_id = s.get("owner_user_id") or None
        row.sla_days = sla
        row.is_active = is_active

    for key, row in existing.items():
        if key not in seen and key not in _DEFAULT_KEYS:
            row.is_active = False


# ─── deadlines & pending actions ─────────────────────────────────────────────

def stage_entry_times(db, candidates):
    """{candidate_id: when they entered their current stage}."""
    out = {}
    ids = [c.id for c in candidates]
    for i in range(0, len(ids), 500):
        chunk = ids[i:i + 500]
        rows = db.query(CandidateStageHistory.candidate_id, func.max(CandidateStageHistory.created_at)) \
            .join(Candidate, Candidate.id == CandidateStageHistory.candidate_id) \
            .filter(CandidateStageHistory.candidate_id.in_(chunk),
                    CandidateStageHistory.to_stage == Candidate.current_stage) \
            .group_by(CandidateStageHistory.candidate_id).all()
        out.update({cid: ts for cid, ts in rows if ts})
    for c in candidates:
        out.setdefault(c.id, c.created_at)
    return out


def _job_owner_user_ids(db, job_ids):
    if not job_ids:
        return {}
    rows = db.query(JobPosting.id, HRProfile.user_id) \
        .outerjoin(HRProfile, HRProfile.id == JobPosting.hr_id) \
        .filter(JobPosting.id.in_(list(job_ids))).all()
    return {jid: uid for jid, uid in rows}


def pending_actions(db, company_id, *, job_ids=None, owner_user_id=None, now=None,
                    statuses=("overdue", "due_soon"), stage=None):
    """Candidates whose current stage has a deadline, with owner and due state.

    ``job_ids`` limits to those jobs (an HR's accessible jobs); ``owner_user_id``
    to actions owned by that user. Candidates on closed jobs are left out.
    """
    now = now or datetime.utcnow()
    stages = {s["key"]: s for s in list_stages(db, company_id, include_inactive=False)
              if s["sla_days"] and not s["is_terminal"]}
    if stage:
        stages = {k: v for k, v in stages.items() if k == stage}
    if not stages:
        return []

    q = db.query(Candidate, JobPosting.title, JobPosting.id).join(
        JobPosting, JobPosting.id == Candidate.job_id
    ).filter(
        Candidate.company_id == company_id,
        Candidate.current_stage.in_(list(stages)),
        Candidate.merged_into_id.is_(None),
        JobPosting.status.notin_(_CLOSED_JOB_STATUSES),
    )
    if job_ids is not None:
        q = q.filter(Candidate.job_id.in_(job_ids))
    rows = q.all()
    if not rows:
        return []

    entered = stage_entry_times(db, [c for c, _, _ in rows])
    job_owner = _job_owner_user_ids(db, {jid for _, _, jid in rows})
    user_ids = {s["owner_user_id"] for s in stages.values() if s["owner_user_id"]} | \
        {u for u in job_owner.values() if u}
    users = {u.id: u for u in db.query(User).filter(User.id.in_(user_ids)).all()} if user_ids else {}

    items = []
    for cand, job_title, job_id in rows:
        st = stages[cand.current_stage]
        entered_at = entered.get(cand.id) or cand.created_at or now
        due_at = entered_at + timedelta(days=st["sla_days"])
        if now > due_at:
            state = "overdue"
        elif due_at - now <= timedelta(hours=DUE_SOON_HOURS):
            state = "due_soon"
        else:
            state = "on_track"
        if statuses and state not in statuses:
            continue
        owner_id = st["owner_user_id"] or job_owner.get(job_id)
        if owner_user_id and owner_id != owner_user_id:
            continue
        items.append({
            "candidate_id": cand.id,
            "candidate_name": f"{cand.first_name} {cand.last_name}".strip(),
            "job_id": job_id,
            "job_title": job_title,
            "stage": cand.current_stage,
            "stage_label": st["label"],
            "owner_user_id": owner_id,
            "owner_name": _owner_name(users.get(owner_id)),
            "owner_source": "stage" if st["owner_user_id"] else "job",
            "entered_at": entered_at.isoformat(),
            "due_at": due_at.isoformat(),
            "status": state,
            "hours_overdue": round((now - due_at).total_seconds() / 3600, 1) if state == "overdue" else 0,
            "_entered": entered_at,
            "_due": due_at,
        })
    items.sort(key=lambda i: i["_due"])
    return items


def public_items(items):
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


# ─── reminders ───────────────────────────────────────────────────────────────

def run_reminders(db, company_id=None, now=None, send_email=True):
    """Remind each owner of their overdue and due-soon candidates. Idempotent within a
    day: a candidate's stage visit is reminded once when due soon, then once per 24h
    while overdue. Returns counts. Commits."""
    now = now or datetime.utcnow()
    if company_id:
        company_ids = [company_id]
    else:
        company_ids = [cid for (cid,) in db.query(PipelineStageConfig.company_id).filter(
            PipelineStageConfig.sla_days.isnot(None),
            PipelineStageConfig.is_active.is_(True),
        ).distinct().all() if cid]

    totals = {"companies": 0, "reminders": 0, "owners_notified": 0, "emails_sent": 0,
              "unowned": 0}
    for cid in company_ids:
        items = pending_actions(db, cid, now=now)
        if not items:
            continue
        totals["companies"] += 1
        recent = db.query(StageReminderLog).filter(
            StageReminderLog.company_id == cid,
            StageReminderLog.candidate_id.in_([i["candidate_id"] for i in items]),
            StageReminderLog.sent_at >= now - timedelta(days=30),
        ).all()

        def _already(item):
            for r in recent:
                if not _same_visit(r, item):
                    continue
                if item["status"] == "due_soon" and r.status == "due_soon":
                    return True
                if item["status"] == "overdue" and r.status == "overdue" \
                        and r.sent_at and r.sent_at > now - timedelta(hours=24):
                    return True
            return False

        by_owner = defaultdict(list)
        for item in items:
            if not item["owner_user_id"]:
                totals["unowned"] += 1
                continue
            if _already(item):
                continue
            by_owner[item["owner_user_id"]].append(item)

        for owner_id, owner_items in by_owner.items():
            owner = db.query(User).filter(User.id == owner_id).first()
            if not owner or not owner.is_active:
                continue
            overdue = sum(1 for i in owner_items if i["status"] == "overdue")
            due_soon = len(owner_items) - overdue
            parts = []
            if overdue:
                parts.append(f"{overdue} overdue")
            if due_soon:
                parts.append(f"{due_soon} due within {DUE_SOON_HOURS}h")
            db.add(Notification(
                user_id=owner_id, company_id=cid,
                title="Candidates waiting on you",
                message=f"{len(owner_items)} candidate(s) need action: " + ", ".join(parts) + ".",
                type="pending_action", reference_type="pending_actions",
            ))
            for i in owner_items:
                db.add(StageReminderLog(
                    company_id=cid, candidate_id=i["candidate_id"], stage_key=i["stage"],
                    stage_entered_at=i["_entered"], recipient_user_id=owner_id,
                    due_at=i["_due"], status=i["status"], sent_at=now,
                ))
            totals["reminders"] += len(owner_items)
            totals["owners_notified"] += 1
            if send_email and owner.email and _send_digest(db, cid, owner, owner_items):
                totals["emails_sent"] += 1
        db.commit()
    return totals


def _same_visit(log, item):
    """Same candidate, stage, recipient and stage visit. Entry times are compared to
    the second: MySQL DATETIME rounds away fractional seconds on the way in."""
    if (log.candidate_id, log.stage_key, log.recipient_user_id) != \
            (item["candidate_id"], item["stage"], item["owner_user_id"]):
        return False
    if log.stage_entered_at is None or item["_entered"] is None:
        return log.stage_entered_at is item["_entered"]
    return abs((log.stage_entered_at - item["_entered"]).total_seconds()) <= 1


def _send_digest(db, company_id, owner, items):
    try:
        from src.services.email_service import (_build_email, dispatch, resolve_sender,
                                                delivery_outcome)
        rows = "".join(
            f"<tr><td style='padding:4px 8px'>{html.escape(i['candidate_name'])}</td>"
            f"<td style='padding:4px 8px'>{html.escape(i['job_title'] or '')}</td>"
            f"<td style='padding:4px 8px'>{html.escape(i['stage_label'])}</td>"
            f"<td style='padding:4px 8px'>{'Overdue' if i['status'] == 'overdue' else 'Due soon'}"
            f" ({i['_due']:%d %b %H:%M} UTC)</td></tr>"
            for i in items
        )
        body = (f"<p>Hi {html.escape(owner.first_name or '')}, these candidates are waiting on "
                f"an action from you:</p><table style='border-collapse:collapse;font-size:0.9rem'>"
                f"<tr><th align='left'>Candidate</th><th align='left'>Job</th>"
                f"<th align='left'>Stage</th><th align='left'>Deadline</th></tr>{rows}</table>")
        result = dispatch(owner.email, f"{len(items)} candidate(s) need your action",
                          _build_email("Pending actions", body),
                          smtp_config=resolve_sender(db, company_id,
                                                     event_type="workflow.reminder_digest"),
                          db=db, reference_type="pending_actions")
        # dispatch never returns "ok"; reading it directly counted every
        # failure as delivered.
        return delivery_outcome(result)["ok"]
    except Exception as exc:
        logger.warning("pending-action digest to %s failed: %s", owner.email, exc)
        return False
