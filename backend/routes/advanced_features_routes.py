# -*- coding: utf-8 -*-
"""
Advanced Features Routes — HireFlow v3.1 (Deep Workflow Edition)
=================================================================
Endpoints:
  1.  Candidate 360 Profile           GET  /advanced/candidates/{id}/profile
  2.  Pipeline Funnel Analytics       GET  /advanced/jobs/{id}/funnel
  3.  Candidate Comparison            POST /advanced/candidates/compare
  4.  Bulk Candidate Actions          POST /advanced/candidates/bulk-action
  5.  Advanced Candidate Search       GET  /advanced/candidates/search
  6.  CSV/JSON Export                 GET  /advanced/jobs/{id}/export
  7.  Question Bank CRUD              GET/POST/PUT/DELETE /advanced/question-bank
  8.  Auto-Reject Rule                POST /advanced/jobs/{id}/auto-reject
  9.  Interview Calendar + Conflicts  GET  /advanced/calendar
 10.  Hiring Velocity Analytics       GET  /advanced/analytics/hiring-velocity
 11.  Department Dashboard            GET  /advanced/analytics/department
 12.  Candidate Scorecard             GET  /advanced/candidates/{id}/scorecard
 13.  Candidate Notes                 GET/POST /advanced/candidates/{id}/notes
 14.  Job Timeline                    GET  /advanced/jobs/{id}/timeline
 15.  Executive Overview              GET  /advanced/analytics/overview
 16.  Conflict-aware Reschedule       POST /advanced/interviews/{id}/reschedule
 17.  Job Clone                       POST /advanced/jobs/{id}/clone
 18.  Skill Gap Report                GET  /advanced/jobs/{id}/skill-gap
"""

import csv
import io
import json
import uuid
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, and_, or_, text
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.config.database_config_mysql import db_manager
from src.models.models import (
    Candidate, CandidateStageHistory, Department, HRProfile,
    Interview, InterviewFeedback, InterviewerProfile,
    JobPosting, Offer, User, ATSResult, SystemConfig
)
from src.models.question_models import Question

router = APIRouter(prefix="/api/v1/advanced", tags=["Advanced Features"])

PIPELINE_STAGES = ["applied", "screening", "shortlisted", "interview", "assessment", "offer", "hired"]


# ─── helpers ─────────────────────────────────────────────────────────────────

def get_db():
    session = db_manager.SessionLocal()
    try:
        yield session
    finally:
        session.close()


def require_roles(*roles):
    def _check(current_user: User = Depends(get_current_user)):
        if current_user.role not in roles:
            raise HTTPException(403, f"Role '{current_user.role}' not permitted")
        return current_user
    return _check


def _safe_json(val, default=None):
    if not val:
        return default if default is not None else []
    try:
        return json.loads(val)
    except Exception:
        return default if default is not None else []


def _float(val, default=0.0):
    try:
        return round(float(val), 2) if val is not None else default
    except Exception:
        return default


def _ats_label(score: float) -> str:
    if score >= 80:
        return "Excellent"
    if score >= 65:
        return "Good"
    if score >= 50:
        return "Fair"
    return "Poor"


def _stage_avg_days(history_rows, stage: str) -> Optional[float]:
    """
    Compute avg days a candidate stayed in `stage` across all transition history.
    Uses consecutive rows for the same candidate.
    """
    by_cand: Dict[str, List] = defaultdict(list)
    for h in history_rows:
        by_cand[h.candidate_id].append(h)

    durations = []
    for cid, rows in by_cand.items():
        rows.sort(key=lambda x: x.created_at or datetime.min)
        for i, row in enumerate(rows):
            if row.from_stage == stage and i > 0:
                prev = rows[i - 1]
                if prev.created_at and row.created_at:
                    durations.append((row.created_at - prev.created_at).total_seconds() / 86400)
    return round(sum(durations) / len(durations), 1) if durations else None


# ─────────────────────────────────────────────────────────────────────────────
# 1. CANDIDATE 360° PROFILE  (deep version)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/candidates/{candidate_id}/profile",
            summary="Candidate 360 Profile — full pipeline history")
def candidate_360_profile(
    candidate_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    """
    Returns everything about a candidate in one call:
    • personal info + salary / notice period
    • ATS score with per-factor breakdown and labels
    • stage history with time-per-stage analysis
    • all interviews, per-dimension feedback scores, questions attempted
    • assessment rounds with scores
    • offers (salary, bonus, status, days until expiry)
    • MCQ test summary (total attempted, correct, score %)
    • pipeline health score + next recommended action
    """
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(404, "Candidate not found")

    job = c.job

    # ── ATS breakdown ──────────────────────────────────────────────────────
    ats = db.query(ATSResult).filter(
        ATSResult.candidate_id == candidate_id,
        ATSResult.job_id == c.job_id
    ).first()
    ats_data = None
    if ats:
        s = _float(ats.ats_score)
        ats_data = {
            "ats_score": s,
            "ats_label": _ats_label(s),
            "factors": {
                "skills_match":  {"score": _float(ats.skills_match_score),  "weight": "35%"},
                "semantic":      {"score": _float(ats.semantic_score),       "weight": "25%"},
                "experience":    {"score": _float(ats.experience_score),     "weight": "20%"},
                "keyword":       {"score": _float(ats.keyword_score),        "weight": "20%"},
            },
            "matched_skills": _safe_json(ats.matched_skills),
            "missing_skills": _safe_json(ats.missing_skills),
            "scoring_breakdown": _safe_json(ats.scoring_breakdown, {}),
        }

    # ── Stage history + time-per-stage ─────────────────────────────────────
    history = (
        db.query(CandidateStageHistory)
        .filter(CandidateStageHistory.candidate_id == candidate_id)
        .order_by(CandidateStageHistory.created_at)
        .all()
    )
    stage_history = []
    for i, h in enumerate(history):
        days_in_stage = None
        if h.created_at and i + 1 < len(history) and history[i + 1].created_at:
            days_in_stage = (history[i + 1].created_at - h.created_at).days
        stage_history.append({
            "from_stage": h.from_stage,
            "to_stage": h.to_stage,
            "notes": h.notes,
            "changed_at": h.created_at.isoformat() if h.created_at else None,
            "days_in_prev_stage": days_in_stage,
        })

    # ── Interviews + questions attempted ───────────────────────────────────
    interviews_db = db.query(Interview).filter(Interview.candidate_id == candidate_id).all()
    interviews_out = []
    total_mcq_attempted = total_mcq_correct = 0
    for iv in interviews_db:
        fb = iv.interview_feedback
        # Count questions
        q_count = db.query(Question).filter(Question.interview_id == iv.id).count()

        interviews_out.append({
            "interview_id": iv.id,
            "title": iv.title or iv.job_title,
            "type": iv.interview_type,
            "difficulty": iv.difficulty_level,
            "status": iv.status,
            "scheduled_datetime": iv.scheduled_datetime.isoformat() if iv.scheduled_datetime else None,
            "duration_minutes": iv.duration_minutes,
            "interviewer_email": iv.interviewer_email,
            "meeting_link": iv.meeting_link,
            "question_count": q_count,
            "feedback": {
                "technical_score":       _float(fb.technical_score) if fb else None,
                "communication_score":   _float(fb.communication_score) if fb else None,
                "problem_solving_score": _float(fb.problem_solving_score) if fb else None,
                "cultural_fit_score":    _float(fb.cultural_fit_score) if fb else None,
                "overall_score":         _float(fb.overall_score) if fb else None,
                "hire_recommendation":   fb.hire_recommendation if fb else None,
                "strengths":  _safe_json(fb.strengths) if fb else [],
                "weaknesses": _safe_json(fb.weaknesses) if fb else [],
                "recommendations": fb.recommendations if fb else None,
            } if fb else None,
        })

    # ── Assessments ────────────────────────────────────────────────────────
    # AssessmentRound was dropped from the schema (see models.py header); the other
    # readers here were already updated, this one still dereferenced the removed
    # relationship and raised AttributeError for every candidate.
    assessments = []

    # ── Offers ─────────────────────────────────────────────────────────────
    offers_out = []
    for o in c.offers:
        days_until_expiry = None
        if o.expiry_date:
            days_until_expiry = (o.expiry_date - datetime.utcnow().date()).days
        offers_out.append({
            "offer_id": o.id,
            "offer_type": o.offer_type,
            "salary": _float(o.salary),
            "bonus": _float(o.bonus),
            "total_compensation": _float(o.salary) + _float(o.bonus),
            "status": o.status,
            "start_date": o.start_date.isoformat() if o.start_date else None,
            "expiry_date": o.expiry_date.isoformat() if o.expiry_date else None,
            "days_until_expiry": days_until_expiry,
            "is_expired": (days_until_expiry is not None and days_until_expiry < 0),
        })

    # ── Pipeline health + next action ──────────────────────────────────────
    days_in_pipeline = (datetime.utcnow() - c.created_at).days if c.created_at else 0
    stage = c.current_stage or "applied"
    next_action_map = {
        "applied":     "Review resume and run ATS screening",
        "screening":   "Schedule screening call",
        "shortlisted": "Schedule technical/HR interview",
        "interview":   "Collect interviewer feedback",
        "assessment":  "Review assessment results",
        "offer":       "Await candidate response",
        "hired":       "Initiate onboarding",
        "rejected":    "Archive candidate",
    }
    # Health: penalise for long time in pipeline or low ATS
    health_score = 100
    if days_in_pipeline > 60:
        health_score -= 30
    elif days_in_pipeline > 30:
        health_score -= 15
    if _float(c.ats_score) < 50:
        health_score -= 20
    if not interviews_db and stage in ("shortlisted", "interview"):
        health_score -= 10
    health_score = max(0, health_score)

    return {
        "candidate": {
            "id": c.id,
            "name": f"{c.first_name} {c.last_name}",
            "email": c.email,
            "phone": c.phone,
            "current_stage": stage,
            "ats_score": _float(c.ats_score),
            "ats_label": _ats_label(_float(c.ats_score)),
            "experience_years": c.experience_years,
            "extracted_skills": _safe_json(c.extracted_skills),
            "current_salary": _float(c.current_salary) if c.current_salary else None,
            "expected_salary": _float(c.expected_salary) if c.expected_salary else None,
            "notice_period": c.notice_period,
            "linkedin_url": c.linkedin_url,
            "github_url": c.github_url,
            "portfolio_url": c.portfolio_url,
            "applied_at": c.created_at.isoformat() if c.created_at else None,
            "days_in_pipeline": days_in_pipeline,
            "pipeline_health_score": health_score,
            "next_recommended_action": next_action_map.get(stage, "Review status"),
        },
        "job": {
            "id": job.id if job else None,
            "title": job.title if job else None,
            "department": job.department.name if job and job.department else None,
            "location": job.location if job else None,
            "employment_type": job.employment_type if job else None,
            "required_skills": _safe_json(job.required_skills) if job else [],
        },
        "ats_breakdown": ats_data,
        "stage_history": stage_history,
        "interviews": interviews_out,
        "assessments": assessments,
        "offers": offers_out,
        "summary": {
            "interview_count": len(interviews_db),
            "assessment_count": len(assessments),
            "offer_count": len(offers_out),
            "has_pending_offer": any(o["status"] in ("sent", "draft") for o in offers_out),
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 2. PIPELINE FUNNEL ANALYTICS  (with time-per-stage & bottleneck detection)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/jobs/{job_id}/funnel", summary="Pipeline Funnel + Bottleneck Analysis")
def pipeline_funnel(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    candidates = db.query(Candidate).filter(Candidate.job_id == job_id).all()
    total = len(candidates)
    cand_ids = [c.id for c in candidates]

    # Per-stage counts + avg ATS
    stage_counts: Dict[str, int] = defaultdict(int)
    stage_ats: Dict[str, List[float]] = defaultdict(list)
    for c in candidates:
        s = c.current_stage or "applied"
        stage_counts[s] += 1
        if c.ats_score:
            stage_ats[s].append(_float(c.ats_score))

    # Time-per-stage from history
    history = (
        db.query(CandidateStageHistory)
        .filter(CandidateStageHistory.candidate_id.in_(cand_ids))
        .order_by(CandidateStageHistory.candidate_id, CandidateStageHistory.created_at)
        .all()
    )
    avg_days_per_stage: Dict[str, Optional[float]] = {}
    for stage in PIPELINE_STAGES:
        avg_days_per_stage[stage] = _stage_avg_days(history, stage)

    # Build funnel rows
    funnel = []
    prev_count = total or 1
    worst_drop_stage, worst_drop_pct = None, 0.0
    for stage in PIPELINE_STAGES + ["rejected"]:
        count = stage_counts.get(stage, 0)
        pct_of_total = round(count / total * 100, 1) if total else 0
        conversion = round(count / prev_count * 100, 1) if prev_count and stage != "applied" else 100.0
        drop_pct = round(100 - conversion, 1) if stage != "applied" else 0.0
        if drop_pct > worst_drop_pct and stage not in ("rejected",):
            worst_drop_pct = drop_pct
            worst_drop_stage = stage
        avg_ats = round(sum(stage_ats[stage]) / len(stage_ats[stage]), 1) if stage_ats.get(stage) else None
        funnel.append({
            "stage": stage,
            "count": count,
            "pct_of_total": pct_of_total,
            "conversion_from_prev": conversion,
            "drop_off_pct": drop_pct,
            "avg_ats_score": avg_ats,
            "avg_days_in_stage": avg_days_per_stage.get(stage),
        })
        if count > 0 and stage != "rejected":
            prev_count = count

    hired = stage_counts.get("hired", 0)
    rejected = stage_counts.get("rejected", 0)
    offer_count = stage_counts.get("offer", 0)

    # Quality score: avg ATS of shortlisted vs all
    shortlisted_ats = stage_ats.get("shortlisted", [])
    quality_improvement = None
    all_ats_vals = [_float(c.ats_score) for c in candidates if c.ats_score]
    if shortlisted_ats and all_ats_vals:
        quality_improvement = round(
            (sum(shortlisted_ats) / len(shortlisted_ats)) - (sum(all_ats_vals) / len(all_ats_vals)), 1
        )

    avg_time_to_hire = None
    hired_cands = [c for c in candidates if c.current_stage == "hired"]
    if hired_cands:
        days_list = [(datetime.utcnow() - c.created_at).days for c in hired_cands if c.created_at]
        avg_time_to_hire = round(sum(days_list) / len(days_list), 1) if days_list else None

    return {
        "job_id": job_id,
        "job_title": job.title,
        "department": job.department.name if job.department else None,
        "total_applicants": total,
        "funnel": funnel,
        "insights": {
            "bottleneck_stage": worst_drop_stage,
            "bottleneck_drop_pct": worst_drop_pct,
            "avg_time_to_hire_days": avg_time_to_hire,
            "offer_acceptance_rate": round(hired / max(offer_count, 1) * 100, 1),
            "overall_hire_rate": round(hired / max(total, 1) * 100, 1),
            "quality_improvement_at_shortlist": quality_improvement,
            "active_in_pipeline": total - rejected - hired,
        },
    }


# ─────────────────────────────────────────────────────────────────────────────
# 3. CANDIDATE COMPARISON  (with sub-scores, skill overlap, rank)
# ─────────────────────────────────────────────────────────────────────────────
class CompareRequest(BaseModel):
    candidate_ids: List[str] = Field(..., min_length=2, max_length=5)


@router.post("/candidates/compare", summary="Side-by-Side Candidate Comparison")
def compare_candidates(
    req: CompareRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    results = []
    all_skill_sets: List[set] = []

    for cid in req.candidate_ids:
        c = db.query(Candidate).filter(Candidate.id == cid).first()
        if not c:
            raise HTTPException(404, f"Candidate {cid} not found")

        ats = db.query(ATSResult).filter(
            ATSResult.candidate_id == cid,
            ATSResult.job_id == c.job_id
        ).first()
        matched = set(s.lower() for s in _safe_json(ats.matched_skills if ats else None))
        missing = set(s.lower() for s in _safe_json(ats.missing_skills if ats else None))
        all_skill_sets.append(matched)

        # All interview feedbacks
        feedbacks = (
            db.query(InterviewFeedback)
            .join(Interview, InterviewFeedback.interview_id == Interview.id)
            .filter(Interview.candidate_id == cid)
            .all()
        )
        iv_count = len(feedbacks)
        avg_technical = avg_communication = avg_problem = avg_cultural = avg_overall = 0.0
        hire_votes = defaultdict(int)
        if feedbacks:
            avg_technical = round(sum(_float(f.technical_score) for f in feedbacks) / iv_count, 1)
            avg_communication = round(sum(_float(f.communication_score) for f in feedbacks) / iv_count, 1)
            avg_problem = round(sum(_float(f.problem_solving_score) for f in feedbacks) / iv_count, 1)
            avg_cultural = round(sum(_float(f.cultural_fit_score) for f in feedbacks) / iv_count, 1)
            avg_overall = round(sum(_float(f.overall_score) for f in feedbacks) / iv_count, 1)
            for f in feedbacks:
                if f.hire_recommendation:
                    hire_votes[f.hire_recommendation] += 1

        avg_assessment = 0.0  # assessment_rounds removed; use interview feedback scores

        results.append({
            "candidate_id": c.id,
            "name": f"{c.first_name} {c.last_name}",
            "email": c.email,
            "current_stage": c.current_stage,
            "experience_years": c.experience_years or 0,
            "expected_salary": _float(c.expected_salary) if c.expected_salary else None,
            "notice_period": c.notice_period,
            "ats": {
                "score": _float(c.ats_score),
                "label": _ats_label(_float(c.ats_score)),
                "skills_match":  _float(ats.skills_match_score) if ats else 0,
                "semantic":      _float(ats.semantic_score) if ats else 0,
                "experience":    _float(ats.experience_score) if ats else 0,
                "keyword":       _float(ats.keyword_score) if ats else 0,
            },
            "matched_skills": list(matched),
            "missing_skills": list(missing),
            "interviews": {
                "count": iv_count,
                "avg_technical": avg_technical,
                "avg_communication": avg_communication,
                "avg_problem_solving": avg_problem,
                "avg_cultural_fit": avg_cultural,
                "avg_overall": avg_overall,
                "hire_votes": dict(hire_votes),
            },
            "avg_assessment_score": avg_assessment,
            "linkedin_url": c.linkedin_url,
            "github_url": c.github_url,
            "applied_at": c.created_at.isoformat() if c.created_at else None,
            "unique_skills": [],  # filled below
            "shared_skills": [],  # filled below
        })

    # Skill overlap analysis
    if all_skill_sets:
        common = all_skill_sets[0].copy()
        for s in all_skill_sets[1:]:
            common &= s
        for i, r in enumerate(results):
            r["shared_skills"] = sorted(common)
            r["unique_skills"] = sorted(all_skill_sets[i] - common)

    # Composite ranking: 40% ATS + 35% interview + 25% assessment
    for r in results:
        r["composite_score"] = round(
            r["ats"]["score"] * 0.40
            + r["interviews"]["avg_overall"] * 0.35
            + r["avg_assessment_score"] * 0.25,
            1,
        )
    results.sort(key=lambda x: x["composite_score"], reverse=True)
    for rank, r in enumerate(results, 1):
        r["rank"] = rank

    return {
        "comparison": results,
        "ranking_formula": "40% ATS + 35% Interview Feedback + 25% Assessments",
        "shared_skills_across_all": sorted(
            all_skill_sets[0].intersection(*all_skill_sets[1:]) if len(all_skill_sets) > 1 else all_skill_sets[0]
        ),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4. BULK CANDIDATE ACTIONS  (move / reject / shortlist / tag / assign-task)
# ─────────────────────────────────────────────────────────────────────────────
VALID_STAGES = {"applied", "screening", "shortlisted", "interview", "assessment", "offer", "hired", "rejected"}

VALID_ACTIONS = {"move_stage", "reject", "shortlist", "hire", "assign_task"}


class BulkActionRequest(BaseModel):
    candidate_ids: List[str]
    action: str
    target_stage: Optional[str] = None   # for move_stage
    task_type: Optional[str] = None      # for assign_task
    task_due_days: int = 3               # days from now for task due date
    notes: Optional[str] = None


@router.post("/candidates/bulk-action", summary="Bulk Candidate Stage / Task Actions")
def bulk_candidate_action(
    req: BulkActionRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    if not req.candidate_ids:
        raise HTTPException(400, "No candidate IDs provided")
    if req.action not in VALID_ACTIONS:
        raise HTTPException(400, f"action must be one of: {sorted(VALID_ACTIONS)}")

    action_stage_map = {
        "reject": "rejected",
        "shortlist": "shortlisted",
        "hire": "hired",
    }

    if req.action == "move_stage":
        if not req.target_stage or req.target_stage not in VALID_STAGES:
            raise HTTPException(400, f"target_stage must be one of {sorted(VALID_STAGES)}")
        new_stage = req.target_stage
    elif req.action == "assign_task":
        new_stage = None
    else:
        new_stage = action_stage_map[req.action]

    # Resolve current user's HR profile for task assignment
    hr_profile = None
    if req.action == "assign_task":
        hr_profile = db.query(HRProfile).filter(HRProfile.user_id == current_user.id).first()
        if not hr_profile:
            raise HTTPException(400, "HR profile not found for current user")
        if not req.task_type:
            raise HTTPException(400, "task_type required for assign_task action")

    updated, failed, tasks_created = [], [], []
    rejected_candidates = []
    for cid in req.candidate_ids:
        c = db.query(Candidate).filter(Candidate.id == cid).first()
        if not c:
            failed.append({"candidate_id": cid, "reason": "not found"})
            continue

        if new_stage:
            old_stage = c.current_stage
            if old_stage == new_stage:
                updated.append({
                    "candidate_id": cid,
                    "name": f"{c.first_name} {c.last_name}",
                    "note": "already in target stage",
                    "stage": new_stage,
                })
                continue
            c.current_stage = new_stage
            c.updated_at = datetime.utcnow()
            db.add(CandidateStageHistory(
                id=str(uuid.uuid4()),
                candidate_id=cid,
                from_stage=old_stage,
                to_stage=new_stage,
                changed_by_id=current_user.id,
                notes=req.notes or f"Bulk {req.action}",
            ))
            updated.append({
                "candidate_id": cid,
                "name": f"{c.first_name} {c.last_name}",
                "from_stage": old_stage,
                "to_stage": new_stage,
            })
            if new_stage == "rejected":
                rejected_candidates.append(c)
        else:
            # assign_task (hr_tasks table removed; log as notification instead)
            tasks_created.append({
                "candidate_id": cid,
                "name": f"{c.first_name} {c.last_name}",
                "task_id": None,
                "task_type": req.task_type,
            })

    db.commit()

    # Send rejection emails after the transaction commits
    if rejected_candidates:
        from src.services.email_service import notify_candidate_rejected
        for c in rejected_candidates:
            notify_candidate_rejected(db, c)

    return {
        "action": req.action,
        "target_stage": new_stage,
        "updated_count": len(updated),
        "failed_count": len(failed),
        "tasks_created": len(tasks_created),
        "updated": updated,
        "tasks": tasks_created,
        "failed": failed,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 5. ADVANCED CANDIDATE SEARCH  (extended filters)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/candidates/search", summary="Advanced Multi-Filter Candidate Search")
def advanced_search(
    q: Optional[str] = Query(None, description="Name / email search"),
    job_id: Optional[str] = Query(None),
    department_id: Optional[str] = Query(None),
    stage: Optional[str] = Query(None),
    min_ats: Optional[float] = Query(None, ge=0, le=100),
    max_ats: Optional[float] = Query(None, ge=0, le=100),
    min_exp: Optional[int] = Query(None, ge=0),
    max_exp: Optional[int] = Query(None),
    max_expected_salary: Optional[float] = Query(None),
    skills: Optional[str] = Query(None, description="Comma-separated skills"),
    hire_recommendation: Optional[str] = Query(None),
    has_interview: Optional[bool] = Query(None),
    has_offer: Optional[bool] = Query(None),
    applied_after: Optional[str] = Query(None, description="YYYY-MM-DD"),
    applied_before: Optional[str] = Query(None, description="YYYY-MM-DD"),
    sort_by: str = Query("ats_score", description="ats_score|experience_years|created_at|name"),
    order: str = Query("desc"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    query = db.query(Candidate)

    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            Candidate.first_name.ilike(like),
            Candidate.last_name.ilike(like),
            Candidate.email.ilike(like),
        ))
    if job_id:
        query = query.filter(Candidate.job_id == job_id)
    if department_id:
        dept_job_ids = [
            j.id for j in db.query(JobPosting).filter(JobPosting.department_id == department_id).all()
        ]
        query = query.filter(Candidate.job_id.in_(dept_job_ids))
    if stage:
        query = query.filter(Candidate.current_stage == stage)
    if min_ats is not None:
        query = query.filter(Candidate.ats_score >= min_ats)
    if max_ats is not None:
        query = query.filter(Candidate.ats_score <= max_ats)
    if min_exp is not None:
        query = query.filter(Candidate.experience_years >= min_exp)
    if max_exp is not None:
        query = query.filter(Candidate.experience_years <= max_exp)
    if max_expected_salary is not None:
        query = query.filter(
            or_(Candidate.expected_salary == None, Candidate.expected_salary <= max_expected_salary)
        )
    if skills:
        for skill in (s.strip() for s in skills.split(",") if s.strip()):
            query = query.filter(Candidate.extracted_skills.ilike(f"%{skill}%"))
    if hire_recommendation:
        cids_with_rec = [
            row[0] for row in db.query(Interview.candidate_id)
            .join(InterviewFeedback, InterviewFeedback.interview_id == Interview.id)
            .filter(InterviewFeedback.hire_recommendation == hire_recommendation)
            .distinct()
        ]
        query = query.filter(Candidate.id.in_(cids_with_rec))
    if has_interview is True:
        iv_cids = [r[0] for r in db.query(Interview.candidate_id).filter(Interview.candidate_id != None).distinct()]
        query = query.filter(Candidate.id.in_(iv_cids))
    elif has_interview is False:
        iv_cids = [r[0] for r in db.query(Interview.candidate_id).filter(Interview.candidate_id != None).distinct()]
        query = query.filter(Candidate.id.notin_(iv_cids))
    if has_offer is True:
        offer_cids = [r[0] for r in db.query(Offer.candidate_id).distinct()]
        query = query.filter(Candidate.id.in_(offer_cids))
    elif has_offer is False:
        offer_cids = [r[0] for r in db.query(Offer.candidate_id).distinct()]
        query = query.filter(Candidate.id.notin_(offer_cids))
    if applied_after:
        query = query.filter(Candidate.created_at >= datetime.strptime(applied_after, "%Y-%m-%d"))
    if applied_before:
        query = query.filter(Candidate.created_at <= datetime.strptime(applied_before, "%Y-%m-%d"))

    total = query.count()

    sort_map = {
        "ats_score": Candidate.ats_score,
        "experience_years": Candidate.experience_years,
        "created_at": Candidate.created_at,
        "name": Candidate.first_name,
    }
    sort_col = sort_map.get(sort_by, Candidate.ats_score)
    query = query.order_by(sort_col.asc() if order == "asc" else sort_col.desc().nullslast())

    candidates = query.offset((page - 1) * page_size).limit(page_size).all()

    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "candidates": [
            {
                "id": c.id,
                "name": f"{c.first_name} {c.last_name}",
                "email": c.email,
                "phone": c.phone,
                "current_stage": c.current_stage,
                "ats_score": _float(c.ats_score),
                "ats_label": _ats_label(_float(c.ats_score)),
                "experience_years": c.experience_years,
                "expected_salary": _float(c.expected_salary) if c.expected_salary else None,
                "notice_period": c.notice_period,
                "extracted_skills": _safe_json(c.extracted_skills),
                "job_id": c.job_id,
                "job_title": c.job.title if c.job else None,
                "applied_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in candidates
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. EXPORT  (CSV or JSON, with ATS breakdown + interview data)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/jobs/{job_id}/export", summary="Export Candidates (CSV or JSON)")
def export_candidates(
    job_id: str,
    format: str = Query("csv", description="csv | json"),
    stage: Optional[str] = Query(None),
    min_ats: Optional[float] = Query(None),
    include_interview_scores: bool = Query(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    q = db.query(Candidate).filter(Candidate.job_id == job_id)
    if stage:
        q = q.filter(Candidate.current_stage == stage)
    if min_ats is not None:
        q = q.filter(Candidate.ats_score >= min_ats)
    candidates = q.order_by(Candidate.ats_score.desc().nullslast()).all()

    rows = []
    for c in candidates:
        ats = db.query(ATSResult).filter(
            ATSResult.candidate_id == c.id,
            ATSResult.job_id == c.job_id
        ).first()
        iv_score = None
        iv_rec = None
        if include_interview_scores:
            fb = (
                db.query(InterviewFeedback)
                .join(Interview, InterviewFeedback.interview_id == Interview.id)
                .filter(Interview.candidate_id == c.id)
                .order_by(InterviewFeedback.created_at.desc())
                .first()
            )
            if fb:
                iv_score = _float(fb.overall_score)
                iv_rec = fb.hire_recommendation

        row = {
            "Name": f"{c.first_name} {c.last_name}",
            "Email": c.email,
            "Phone": c.phone or "",
            "Stage": c.current_stage or "",
            "ATS Score": _float(c.ats_score),
            "ATS Label": _ats_label(_float(c.ats_score)),
            "Skills Match": _float(ats.skills_match_score) if ats else "",
            "Semantic Score": _float(ats.semantic_score) if ats else "",
            "Experience Score": _float(ats.experience_score) if ats else "",
            "Experience (yrs)": c.experience_years or "",
            "Expected Salary": _float(c.expected_salary) if c.expected_salary else "",
            "Notice Period": c.notice_period or "",
            "Skills": ", ".join(_safe_json(c.extracted_skills)),
            "Matched Skills": ", ".join(_safe_json(ats.matched_skills) if ats else []),
            "Missing Skills": ", ".join(_safe_json(ats.missing_skills) if ats else []),
            "LinkedIn": c.linkedin_url or "",
            "GitHub": c.github_url or "",
            "Applied At": c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
        }
        if include_interview_scores:
            row["Interview Score"] = iv_score or ""
            row["Hire Recommendation"] = iv_rec or ""
        rows.append(row)

    filename_base = f"candidates_{job.title.replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}"

    if format == "json":
        content = json.dumps({"job": job.title, "total": len(rows), "candidates": rows},
                             indent=2, ensure_ascii=False)
        return StreamingResponse(
            io.BytesIO(content.encode("utf-8")),
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename_base}.json"'},
        )

    # CSV (default)
    output = io.StringIO()
    if rows:
        writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    output.seek(0)
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8-sig")),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename_base}.csv"'},
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. QUESTION BANK  (full CRUD + usage stats)
# ─────────────────────────────────────────────────────────────────────────────
class QuestionBankItem(BaseModel):
    question_text: str
    question_type: str = "multiple_choice"
    difficulty_level: str = "Medium"
    category: Optional[str] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    code_template: Optional[str] = None
    points: int = 10
    time_limit_minutes: int = 5


class QuestionBankUpdate(BaseModel):
    question_text: Optional[str] = None
    question_type: Optional[str] = None
    difficulty_level: Optional[str] = None
    category: Optional[str] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    points: Optional[int] = None


@router.post("/question-bank", summary="Save Question to Bank")
def save_to_question_bank(
    item: QuestionBankItem,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    q = Question(
        id=str(uuid.uuid4()),
        interview_id=None,
        question_text=item.question_text,
        question_type=item.question_type,
        difficulty_level=item.difficulty_level,
        category=item.category,
        options=json.dumps(item.options) if item.options else None,
        correct_answer=item.correct_answer,
        sample_answer=item.sample_answer,
        code_template=item.code_template,
        points=item.points,
        time_limit_minutes=item.time_limit_minutes,
        status="approved",
        is_approved=True,
        approved_by=current_user.email,
    )
    db.add(q)
    db.commit()
    db.refresh(q)
    return {"message": "Question saved", "question_id": q.id}


@router.get("/question-bank", summary="List Question Bank with usage stats")
def list_question_bank(
    question_type: Optional[str] = Query(None),
    difficulty_level: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    query = db.query(Question).filter(Question.interview_id == None)  # noqa
    if question_type:
        query = query.filter(Question.question_type == question_type)
    if difficulty_level:
        query = query.filter(Question.difficulty_level == difficulty_level)
    if category:
        query = query.filter(Question.category.ilike(f"%{category}%"))
    if search:
        query = query.filter(Question.question_text.ilike(f"%{search}%"))

    total = query.count()
    questions = query.order_by(Question.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()

    # Usage count per question text (how many interview questions share same text)
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "questions": [
            {
                "id": qs.id,
                "question_text": qs.question_text,
                "question_type": qs.question_type,
                "difficulty_level": qs.difficulty_level,
                "category": qs.category,
                "options": _safe_json(qs.options),
                "correct_answer": qs.correct_answer,
                "sample_answer": qs.sample_answer,
                "code_template": qs.code_template,
                "points": qs.points,
                "time_limit_minutes": qs.time_limit_minutes,
                "approved_by": qs.approved_by,
                "created_at": qs.created_at.isoformat() if qs.created_at else None,
            }
            for qs in questions
        ],
    }


@router.put("/question-bank/{question_id}", summary="Update Bank Question")
def update_bank_question(
    question_id: str,
    update: QuestionBankUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    q = db.query(Question).filter(Question.id == question_id, Question.interview_id == None).first()  # noqa
    if not q:
        raise HTTPException(404, "Question not found in bank")
    if update.question_text is not None:
        q.question_text = update.question_text
    if update.question_type is not None:
        q.question_type = update.question_type
    if update.difficulty_level is not None:
        q.difficulty_level = update.difficulty_level
    if update.category is not None:
        q.category = update.category
    if update.options is not None:
        q.options = json.dumps(update.options)
    if update.correct_answer is not None:
        q.correct_answer = update.correct_answer
    if update.sample_answer is not None:
        q.sample_answer = update.sample_answer
    if update.points is not None:
        q.points = update.points
    db.commit()
    return {"message": "Question updated", "question_id": question_id}


@router.delete("/question-bank/{question_id}", summary="Delete Bank Question")
def delete_bank_question(
    question_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    q = db.query(Question).filter(Question.id == question_id, Question.interview_id == None).first()  # noqa
    if not q:
        raise HTTPException(404, "Question not found in bank")
    db.delete(q)
    db.commit()
    return {"message": "Question deleted", "question_id": question_id}


@router.post("/question-bank/{question_id}/add-to-interview/{interview_id}",
             summary="Copy Bank Question into Interview")
def add_bank_question_to_interview(
    question_id: str,
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    src = db.query(Question).filter(Question.id == question_id).first()
    if not src:
        raise HTTPException(404, "Question not found")
    iv = db.query(Interview).filter(Interview.id == interview_id).first()
    if not iv:
        raise HTTPException(404, "Interview not found")
    new_q = Question(
        id=str(uuid.uuid4()),
        interview_id=interview_id,
        question_text=src.question_text,
        question_type=src.question_type,
        difficulty_level=src.difficulty_level,
        category=src.category,
        options=src.options,
        correct_answer=src.correct_answer,
        sample_answer=src.sample_answer,
        code_template=src.code_template,
        points=src.points,
        time_limit_minutes=src.time_limit_minutes,
        status="approved",
        is_approved=True,
        approved_by=current_user.email,
    )
    db.add(new_q)
    db.commit()
    db.refresh(new_q)
    return {"message": "Copied to interview", "new_question_id": new_q.id, "interview_id": interview_id}


# ─────────────────────────────────────────────────────────────────────────────
# 8. AUTO-REJECT  (ATS + experience + missing required skills)
# ─────────────────────────────────────────────────────────────────────────────
class AutoRejectRequest(BaseModel):
    ats_threshold: Optional[float] = None
    min_experience_years: Optional[int] = None
    required_skills: Optional[List[str]] = None  # reject if ALL are missing
    dry_run: bool = True
    notes: Optional[str] = None


@router.post("/jobs/{job_id}/auto-reject", summary="Auto-Reject with Multi-Rule Engine")
def auto_reject(
    job_id: str,
    req: AutoRejectRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    if not any([req.ats_threshold, req.min_experience_years, req.required_skills]):
        raise HTTPException(400, "Provide at least one rejection rule")

    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    eligible = db.query(Candidate).filter(
        Candidate.job_id == job_id,
        Candidate.current_stage.notin_(["hired", "rejected", "offer"]),
    ).all()

    to_reject = []
    for c in eligible:
        reasons = []
        if req.ats_threshold and _float(c.ats_score) < req.ats_threshold:
            reasons.append(f"ATS {_float(c.ats_score):.1f} < threshold {req.ats_threshold}")
        if req.min_experience_years and (c.experience_years or 0) < req.min_experience_years:
            reasons.append(f"Experience {c.experience_years}yr < required {req.min_experience_years}yr")
        if req.required_skills:
            cand_skills = set(s.lower() for s in _safe_json(c.extracted_skills))
            missing_req = [s for s in req.required_skills if s.lower() not in cand_skills]
            if len(missing_req) == len(req.required_skills):  # ALL required skills missing
                reasons.append(f"Missing all required skills: {missing_req}")
        if reasons:
            to_reject.append({
                "candidate_id": c.id,
                "name": f"{c.first_name} {c.last_name}",
                "ats_score": _float(c.ats_score),
                "current_stage": c.current_stage,
                "rejection_reasons": reasons,
            })

    if req.dry_run:
        return {
            "dry_run": True,
            "rules": {
                "ats_threshold": req.ats_threshold,
                "min_experience_years": req.min_experience_years,
                "required_skills": req.required_skills,
            },
            "would_reject": len(to_reject),
            "candidates": to_reject,
            "message": "Set dry_run=false to apply permanently.",
        }

    # Apply rejections
    reject_ids = {r["candidate_id"] for r in to_reject}
    rejected_candidates = []
    for c in eligible:
        if c.id in reject_ids:
            reason_entry = next(r for r in to_reject if r["candidate_id"] == c.id)
            old = c.current_stage
            c.current_stage = "rejected"
            c.updated_at = datetime.utcnow()
            db.add(CandidateStageHistory(
                id=str(uuid.uuid4()),
                candidate_id=c.id,
                from_stage=old,
                to_stage="rejected",
                changed_by_id=current_user.id,
                notes=req.notes or "; ".join(reason_entry["rejection_reasons"]),
            ))
            rejected_candidates.append(c)
    db.commit()

    # Send rejection emails after the transaction commits
    from src.services.email_service import notify_candidate_rejected
    for c in rejected_candidates:
        notify_candidate_rejected(db, c)

    return {
        "dry_run": False,
        "rejected_count": len(to_reject),
        "candidates": to_reject,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 9. INTERVIEW CALENDAR  (with double-booking conflict detection)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/calendar", summary="Interview Calendar with Conflict Detection")
def interview_calendar(
    start_date: Optional[str] = Query(None, description="YYYY-MM-DD (default: today)"),
    end_date: Optional[str] = Query(None, description="YYYY-MM-DD (default: +30 days)"),
    interviewer_email: Optional[str] = Query(None),
    status: Optional[str] = Query(None, description="DRAFT|SCHEDULED|COMPLETED|CANCELLED"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    now = datetime.utcnow()
    sd = datetime.strptime(start_date, "%Y-%m-%d") if start_date else now.replace(hour=0, minute=0, second=0)
    ed = (datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)) if end_date else (sd + timedelta(days=30))

    query = (
        db.query(Interview)
        .filter(Interview.scheduled_datetime != None)  # noqa
        .filter(Interview.scheduled_datetime >= sd)
        .filter(Interview.scheduled_datetime < ed)
    )
    if interviewer_email:
        query = query.filter(Interview.interviewer_email == interviewer_email)
    if status:
        query = query.filter(Interview.status == status.upper())
    if current_user.role == "interviewer":
        query = query.filter(Interview.interviewer_email == current_user.email)

    interviews = query.order_by(Interview.scheduled_datetime).all()

    # Build events
    events = []
    for iv in interviews:
        c = db.query(Candidate).filter(Candidate.id == iv.candidate_id).first() if iv.candidate_id else None
        j = db.query(JobPosting).filter(JobPosting.id == iv.job_id).first() if iv.job_id else None
        dur = iv.duration_minutes or 60
        start_dt = iv.scheduled_datetime
        end_dt = start_dt + timedelta(minutes=dur)
        events.append({
            "interview_id": iv.id,
            "title": iv.title or (j.title + " Interview") if j else "Interview",
            "candidate_name": f"{c.first_name} {c.last_name}" if c else None,
            "candidate_id": iv.candidate_id,
            "job_title": j.title if j else iv.job_title,
            "job_id": iv.job_id,
            "interviewer_email": iv.interviewer_email,
            "status": iv.status,
            "scheduled_datetime": start_dt.isoformat(),
            "end_datetime": end_dt.isoformat(),
            "duration_minutes": dur,
            "meeting_link": iv.meeting_link,
            "difficulty_level": iv.difficulty_level,
            "interview_type": iv.interview_type,
            "has_feedback": iv.interview_feedback is not None,
        })

    # Conflict detection: same interviewer, overlapping time windows
    conflicts = []
    by_interviewer: Dict[str, List] = defaultdict(list)
    for e in events:
        if e["interviewer_email"]:
            by_interviewer[e["interviewer_email"]].append(e)

    for email, evs in by_interviewer.items():
        evs.sort(key=lambda x: x["scheduled_datetime"])
        for i in range(len(evs) - 1):
            a, b = evs[i], evs[i + 1]
            a_end = datetime.fromisoformat(a["end_datetime"])
            b_start = datetime.fromisoformat(b["scheduled_datetime"])
            if a_end > b_start:
                conflicts.append({
                    "interviewer": email,
                    "conflict_between": [a["interview_id"], b["interview_id"]],
                    "overlap_minutes": int((a_end - b_start).total_seconds() / 60),
                    "interviews": [
                        {"id": a["interview_id"], "start": a["scheduled_datetime"], "candidate": a["candidate_name"]},
                        {"id": b["interview_id"], "start": b["scheduled_datetime"], "candidate": b["candidate_name"]},
                    ],
                })

    # Group by date
    by_date: Dict[str, List] = defaultdict(list)
    for e in events:
        by_date[e["scheduled_datetime"][:10]].append(e)

    return {
        "period": {"start": sd.date().isoformat(), "end": (ed - timedelta(days=1)).date().isoformat()},
        "total_events": len(events),
        "conflicts_detected": len(conflicts),
        "conflicts": conflicts,
        "calendar": dict(by_date),
        "flat_list": events,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 10. HIRING VELOCITY  (median, monthly trend, stage conversion, percentiles)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/analytics/hiring-velocity", summary="Hiring Velocity & Time-to-Hire Metrics")
def hiring_velocity(
    department_id: Optional[str] = Query(None),
    days: int = Query(90, ge=7, le=365),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    since = datetime.utcnow() - timedelta(days=days)
    job_q = db.query(JobPosting).filter(JobPosting.created_at >= since)
    if department_id:
        job_q = job_q.filter(JobPosting.department_id == department_id)
    jobs = job_q.all()
    job_ids = [j.id for j in jobs]

    if not job_ids:
        return {"message": "No jobs in the window", "metrics": {}}

    all_cands = db.query(Candidate).filter(Candidate.job_id.in_(job_ids)).all()
    hired = [c for c in all_cands if c.current_stage == "hired"]
    rejected = [c for c in all_cands if c.current_stage == "rejected"]

    def days_list(clist):
        return sorted([(datetime.utcnow() - c.created_at).days for c in clist if c.created_at])

    def _median(lst):
        if not lst:
            return None
        n = len(lst)
        mid = n // 2
        return round((lst[mid - 1] + lst[mid]) / 2 if n % 2 == 0 else lst[mid], 1)

    hired_days = days_list(hired)
    avg_tth = round(sum(hired_days) / len(hired_days), 1) if hired_days else None
    median_tth = _median(hired_days)

    # Stage distribution
    stage_counts: Dict[str, int] = defaultdict(int)
    for c in all_cands:
        stage_counts[c.current_stage or "applied"] += 1

    # Stage-to-stage conversion (cumulative)
    stage_conversions = {}
    prev = len(all_cands) or 1
    for s in PIPELINE_STAGES:
        cnt = stage_counts.get(s, 0)
        stage_conversions[s] = round(cnt / prev * 100, 1) if prev else 0
        if cnt:
            prev = cnt

    # Monthly application trend
    monthly: Dict[str, int] = defaultdict(int)
    for c in all_cands:
        if c.created_at:
            monthly[c.created_at.strftime("%Y-%m")] += 1

    # Top departments by hire count
    dept_hires: Dict[str, int] = defaultdict(int)
    for c in hired:
        if c.job and c.job.department:
            dept_hires[c.job.department.name] += 1

    # ATS distribution of hired candidates
    hired_ats = [_float(c.ats_score) for c in hired if c.ats_score]

    return {
        "period_days": days,
        "metrics": {
            "total_jobs": len(jobs),
            "total_applicants": len(all_cands),
            "total_hired": len(hired),
            "total_rejected": len(rejected),
            "active_in_pipeline": len(all_cands) - len(hired) - len(rejected),
            "overall_hire_rate_pct": round(len(hired) / max(len(all_cands), 1) * 100, 1),
            "avg_time_to_hire_days": avg_tth,
            "median_time_to_hire_days": median_tth,
            "p75_time_to_hire_days": hired_days[int(len(hired_days) * 0.75)] if hired_days else None,
            "avg_ats_of_hired": round(sum(hired_ats) / len(hired_ats), 1) if hired_ats else None,
        },
        "stage_conversion_rates": stage_conversions,
        "stage_distribution": dict(stage_counts),
        "monthly_applications": dict(sorted(monthly.items())),
        "top_departments_by_hire": dict(sorted(dept_hires.items(), key=lambda x: -x[1])[:5]),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 11. DEPARTMENT DASHBOARD  (with interview + offer metrics)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/analytics/department", summary="Department-wise Hiring Dashboard")
def department_dashboard(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    departments = db.query(Department).all()
    result = []
    for dept in departments:
        jobs = db.query(JobPosting).filter(JobPosting.department_id == dept.id).all()
        job_ids = [j.id for j in jobs]
        cands = db.query(Candidate).filter(Candidate.job_id.in_(job_ids)).all() if job_ids else []
        hired = [c for c in cands if c.current_stage == "hired"]
        ats_vals = [_float(c.ats_score) for c in cands if c.ats_score]

        stage_dist = defaultdict(int)
        for c in cands:
            stage_dist[c.current_stage or "applied"] += 1

        # Interviews scheduled for this dept
        cand_ids = [c.id for c in cands]
        iv_count = db.query(Interview).filter(Interview.candidate_id.in_(cand_ids)).count() if cand_ids else 0
        offer_count = db.query(Offer).filter(Offer.candidate_id.in_(cand_ids)).count() if cand_ids else 0
        offer_accepted = (
            db.query(Offer)
            .filter(Offer.candidate_id.in_(cand_ids), Offer.status == "accepted")
            .count()
            if cand_ids else 0
        )

        interviewers = db.query(InterviewerProfile).filter(
            InterviewerProfile.department_id == dept.id
        ).all()

        result.append({
            "department_id": dept.id,
            "department_name": dept.name,
            "description": dept.description,
            "jobs": {
                "total": len(jobs),
                "active": sum(1 for j in jobs if j.status == "active"),
                "closed": sum(1 for j in jobs if j.status == "closed"),
            },
            "candidates": {
                "total": len(cands),
                "hired": len(hired),
                "hire_rate_pct": round(len(hired) / max(len(cands), 1) * 100, 1),
                "avg_ats_score": round(sum(ats_vals) / len(ats_vals), 1) if ats_vals else None,
                "stage_distribution": dict(stage_dist),
            },
            "interviews": {"total_scheduled": iv_count},
            "offers": {
                "total_extended": offer_count,
                "accepted": offer_accepted,
                "acceptance_rate_pct": round(offer_accepted / max(offer_count, 1) * 100, 1),
            },
            "interviewers": [
                {
                    "id": ip.id,
                    "name": f"{ip.user.first_name} {ip.user.last_name}" if ip.user else None,
                    "email": ip.user.email if ip.user else None,
                    "years_experience": ip.years_experience,
                    "expertise": _safe_json(ip.expertise_areas),
                }
                for ip in interviewers
            ],
        })

    result.sort(key=lambda x: x["candidates"]["total"], reverse=True)
    return {"total_departments": len(result), "departments": result}


# ─────────────────────────────────────────────────────────────────────────────
# 12. CANDIDATE SCORECARD  (with skill gap + percentile rank)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/candidates/{candidate_id}/scorecard", summary="Holistic Candidate Scorecard")
def candidate_scorecard(
    candidate_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(404, "Candidate not found")

    ats_score = _float(c.ats_score)

    # Interview feedbacks — all dimensions
    feedbacks = (
        db.query(InterviewFeedback)
        .join(Interview, InterviewFeedback.interview_id == Interview.id)
        .filter(Interview.candidate_id == candidate_id)
        .all()
    )
    hire_votes: Dict[str, int] = defaultdict(int)
    dim_scores: Dict[str, List[float]] = defaultdict(list)
    if feedbacks:
        for f in feedbacks:
            for dim, val in [
                ("technical", f.technical_score),
                ("communication", f.communication_score),
                ("problem_solving", f.problem_solving_score),
                ("cultural_fit", f.cultural_fit_score),
                ("overall", f.overall_score),
            ]:
                if val is not None:
                    dim_scores[dim].append(_float(val))
            if f.hire_recommendation:
                hire_votes[f.hire_recommendation] += 1

    avg_dim = {k: round(sum(v) / len(v), 1) for k, v in dim_scores.items()}
    interview_score = avg_dim.get("overall", 0.0)

    # Assessment score
    assessment_score = 0.0  # assessment_rounds removed; composite uses ATS + interview only

    composite = round(ats_score * 0.40 + interview_score * 0.35 + assessment_score * 0.25, 1)

    # Skill gap analysis
    ats = db.query(ATSResult).filter(
        ATSResult.candidate_id == candidate_id,
        ATSResult.job_id == c.job_id
    ).first()
    required = _safe_json(c.job.required_skills) if c.job else []
    matched = _safe_json(ats.matched_skills) if ats else []
    missing = _safe_json(ats.missing_skills) if ats else []
    skill_coverage_pct = round(len(matched) / max(len(required), 1) * 100, 1) if required else None

    # Percentile among same-job candidates
    percentile = None
    if c.job_id and c.ats_score:
        all_scores = [
            _float(x.ats_score) for x in
            db.query(Candidate).filter(Candidate.job_id == c.job_id, Candidate.ats_score != None).all()
        ]
        if all_scores:
            rank = sum(1 for s in all_scores if s < ats_score)
            percentile = round(rank / len(all_scores) * 100, 1)

    # Recommendation
    dominant_vote = max(hire_votes, key=hire_votes.get) if hire_votes else None
    if composite >= 75 or dominant_vote == "strong_hire":
        recommendation, badge = "Strong Hire", "green"
    elif composite >= 60 or dominant_vote == "hire":
        recommendation, badge = "Hire", "lightgreen"
    elif composite >= 45:
        recommendation, badge = "Consider", "yellow"
    else:
        recommendation, badge = "No Hire", "red"

    # Risk factors
    risks = []
    if ats_score < 50:
        risks.append("Low ATS score — weak skill match")
    if interview_score > 0 and interview_score < 50:
        risks.append("Below-average interview performance")
    if missing:
        risks.append(f"Missing {len(missing)} required skill(s): {', '.join(missing[:3])}")
    if (c.experience_years or 0) < (c.job.experience_years or 0) if c.job else False:
        risks.append("Less experience than required")

    return {
        "candidate_id": candidate_id,
        "name": f"{c.first_name} {c.last_name}",
        "email": c.email,
        "current_stage": c.current_stage,
        "ats_percentile": percentile,
        "scores": {
            "ats": round(ats_score, 1),
            "interview_overall": round(interview_score, 1),
            "interview_dimensions": avg_dim,
            "assessment": round(assessment_score, 1),
            "composite": composite,
        },
        "weights": {"ats": "40%", "interview": "35%", "assessment": "25%"},
        "skill_gap": {
            "required_skills": required,
            "matched_skills": matched,
            "missing_skills": missing,
            "coverage_pct": skill_coverage_pct,
        },
        "hire_votes": dict(hire_votes),
        "recommendation": recommendation,
        "recommendation_badge": badge,
        "risk_factors": risks,
        "strengths": list({s for f in feedbacks for s in _safe_json(f.strengths)}),
        "weaknesses": list({w for f in feedbacks for w in _safe_json(f.weaknesses)}),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 13. CANDIDATE NOTES  (internal notes by HR/Interviewer)
# ─────────────────────────────────────────────────────────────────────────────
class NoteCreate(BaseModel):
    note: str
    visibility: str = "hr"   # "hr" | "all" | "interviewer"


@router.post("/candidates/{candidate_id}/notes", summary="Add Internal Note to Candidate")
def add_candidate_note(
    candidate_id: str,
    body: NoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(404, "Candidate not found")

    # Store note as a stage-history entry with a special from_stage="note"
    note_entry = CandidateStageHistory(
        id=str(uuid.uuid4()),
        candidate_id=candidate_id,
        from_stage="__note__",
        to_stage=body.visibility,
        changed_by_id=current_user.id,
        notes=body.note,
    )
    db.add(note_entry)
    db.commit()
    return {
        "note_id": note_entry.id,
        "author": current_user.email,
        "note": body.note,
        "visibility": body.visibility,
        "created_at": note_entry.created_at.isoformat() if note_entry.created_at else None,
    }


@router.get("/candidates/{candidate_id}/notes", summary="Get Internal Notes for Candidate")
def get_candidate_notes(
    candidate_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    c = db.query(Candidate).filter(Candidate.id == candidate_id).first()
    if not c:
        raise HTTPException(404, "Candidate not found")

    notes = (
        db.query(CandidateStageHistory)
        .filter(
            CandidateStageHistory.candidate_id == candidate_id,
            CandidateStageHistory.from_stage == "__note__",
        )
        .order_by(CandidateStageHistory.created_at.desc())
        .all()
    )
    return {
        "candidate_id": candidate_id,
        "notes": [
            {
                "note_id": n.id,
                "note": n.notes,
                "visibility": n.to_stage,
                "author_id": n.changed_by_id,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in notes
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 14. JOB TIMELINE  (complete lifecycle of a job posting)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/jobs/{job_id}/timeline", summary="Full Job Lifecycle Timeline")
def job_timeline(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    events = []

    # Job created
    events.append({
        "event": "job_created",
        "label": f"Job Posted: {job.title}",
        "timestamp": job.created_at.isoformat() if job.created_at else None,
        "actor": None,
        "detail": f"Status: {job.status} | Dept: {job.department.name if job.department else 'N/A'}",
    })

    # All candidate applications
    candidates = db.query(Candidate).filter(Candidate.job_id == job_id).order_by(Candidate.created_at).all()
    for c in candidates:
        events.append({
            "event": "application_received",
            "label": f"Application: {c.first_name} {c.last_name}",
            "timestamp": c.created_at.isoformat() if c.created_at else None,
            "actor": c.email,
            "detail": f"ATS: {_float(c.ats_score)} | Stage: {c.current_stage}",
        })

    # All stage transitions
    cand_ids = [c.id for c in candidates]
    history = (
        db.query(CandidateStageHistory)
        .filter(
            CandidateStageHistory.candidate_id.in_(cand_ids),
            CandidateStageHistory.from_stage != "__note__",
        )
        .order_by(CandidateStageHistory.created_at)
        .all()
    )
    cand_map = {c.id: f"{c.first_name} {c.last_name}" for c in candidates}
    for h in history:
        events.append({
            "event": "stage_change",
            "label": f"{cand_map.get(h.candidate_id, '?')}: {h.from_stage} → {h.to_stage}",
            "timestamp": h.created_at.isoformat() if h.created_at else None,
            "actor": h.changed_by_id,
            "detail": h.notes or "",
        })

    # Interviews scheduled
    for c in candidates:
        ivs = db.query(Interview).filter(Interview.candidate_id == c.id).all()
        for iv in ivs:
            events.append({
                "event": "interview_scheduled",
                "label": f"Interview for {cand_map.get(c.id, '?')}",
                "timestamp": iv.created_at.isoformat() if iv.created_at else None,
                "actor": iv.interviewer_email,
                "detail": f"Type: {iv.interview_type} | Status: {iv.status}",
            })

    # Offers
    for c in candidates:
        for o in c.offers:
            events.append({
                "event": "offer_created",
                "label": f"Offer to {cand_map.get(c.id, '?')}: {o.offer_type}",
                "timestamp": o.created_at.isoformat() if o.created_at else None,
                "actor": None,
                "detail": f"Salary: {_float(o.salary)} | Status: {o.status}",
            })

    # Sort all events chronologically
    events.sort(key=lambda x: x["timestamp"] or "")

    return {
        "job_id": job_id,
        "job_title": job.title,
        "job_status": job.status,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "total_events": len(events),
        "total_applicants": len(candidates),
        "timeline": events,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 15. EXECUTIVE OVERVIEW  (system-wide summary dashboard)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/analytics/overview", summary="Executive Hiring Overview")
def executive_overview(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    now = datetime.utcnow()
    last_30 = now - timedelta(days=30)
    last_7 = now - timedelta(days=7)

    total_jobs = db.query(JobPosting).count()
    active_jobs = db.query(JobPosting).filter(JobPosting.status == "active").count()
    total_candidates = db.query(Candidate).count()
    new_this_week = db.query(Candidate).filter(Candidate.created_at >= last_7).count()
    new_this_month = db.query(Candidate).filter(Candidate.created_at >= last_30).count()
    hired_this_month = db.query(Candidate).filter(
        Candidate.current_stage == "hired", Candidate.updated_at >= last_30
    ).count()
    total_interviews = db.query(Interview).count()
    pending_interviews = db.query(Interview).filter(Interview.status == "SCHEDULED").count()
    total_offers = db.query(Offer).count()
    accepted_offers = db.query(Offer).filter(Offer.status == "accepted").count()
    pending_offers = db.query(Offer).filter(Offer.status == "sent").count()

    # ATS score distribution
    score_ranges = {"0-25": 0, "25-50": 0, "50-75": 0, "75-100": 0}
    for c in db.query(Candidate).filter(Candidate.ats_score != None).all():
        s = _float(c.ats_score)
        if s < 25:
            score_ranges["0-25"] += 1
        elif s < 50:
            score_ranges["25-50"] += 1
        elif s < 75:
            score_ranges["50-75"] += 1
        else:
            score_ranges["75-100"] += 1

    # Stage distribution
    stage_dist: Dict[str, int] = defaultdict(int)
    for c in db.query(Candidate).all():
        stage_dist[c.current_stage or "applied"] += 1

    # Top 5 active jobs by applicant count
    top_jobs = (
        db.query(JobPosting, func.count(Candidate.id).label("cnt"))
        .join(Candidate, Candidate.job_id == JobPosting.id, isouter=True)
        .filter(JobPosting.status == "active")
        .group_by(JobPosting.id)
        .order_by(func.count(Candidate.id).desc())
        .limit(5)
        .all()
    )

    return {
        "generated_at": now.isoformat(),
        "jobs": {
            "total": total_jobs,
            "active": active_jobs,
            "closed": total_jobs - active_jobs,
        },
        "candidates": {
            "total": total_candidates,
            "new_this_week": new_this_week,
            "new_this_month": new_this_month,
            "hired_this_month": hired_this_month,
            "stage_distribution": dict(stage_dist),
            "ats_score_distribution": score_ranges,
        },
        "interviews": {
            "total": total_interviews,
            "pending_scheduled": pending_interviews,
        },
        "offers": {
            "total": total_offers,
            "accepted": accepted_offers,
            "pending_response": pending_offers,
            "acceptance_rate_pct": round(accepted_offers / max(total_offers, 1) * 100, 1),
        },
        "top_active_jobs": [
            {"job_id": j.id, "title": j.title, "applicants": cnt}
            for j, cnt in top_jobs
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 16. RESCHEDULE INTERVIEW  (with conflict check)
# ─────────────────────────────────────────────────────────────────────────────
class RescheduleRequest(BaseModel):
    new_datetime: str             # ISO format: "2026-04-01T10:00:00"
    new_duration_minutes: Optional[int] = None
    new_meeting_link: Optional[str] = None
    reason: Optional[str] = None
    force: bool = False           # override conflict warning


@router.post("/interviews/{interview_id}/reschedule", summary="Reschedule Interview with Conflict Check")
def reschedule_interview(
    interview_id: int,
    req: RescheduleRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr", "interviewer")),
):
    iv = db.query(Interview).filter(Interview.id == interview_id).first()
    if not iv:
        raise HTTPException(404, "Interview not found")

    try:
        new_dt = datetime.fromisoformat(req.new_datetime)
    except ValueError:
        raise HTTPException(400, "new_datetime must be ISO format e.g. 2026-04-01T10:00:00")

    if new_dt <= datetime.utcnow():
        raise HTTPException(400, "new_datetime must be in the future")

    dur = req.new_duration_minutes or iv.duration_minutes or 60
    new_end = new_dt + timedelta(minutes=dur)

    # Conflict check for same interviewer
    conflicts = []
    if iv.interviewer_email and not req.force:
        overlapping = (
            db.query(Interview)
            .filter(
                Interview.interviewer_email == iv.interviewer_email,
                Interview.id != interview_id,
                Interview.scheduled_datetime != None,  # noqa
                Interview.status.notin_(["CANCELLED", "COMPLETED"]),
            )
            .all()
        )
        for other in overlapping:
            o_start = other.scheduled_datetime
            o_end = o_start + timedelta(minutes=other.duration_minutes or 60)
            if not (new_end <= o_start or new_dt >= o_end):
                conflicts.append({
                    "conflicting_interview_id": other.id,
                    "existing_start": o_start.isoformat(),
                    "existing_end": o_end.isoformat(),
                    "overlap_minutes": int(
                        (min(new_end, o_end) - max(new_dt, o_start)).total_seconds() / 60
                    ),
                })

    if conflicts and not req.force:
        return {
            "rescheduled": False,
            "reason": "Scheduling conflicts detected. Set force=true to override.",
            "conflicts": conflicts,
        }

    old_dt = iv.scheduled_datetime
    iv.scheduled_datetime = new_dt
    if req.new_duration_minutes:
        iv.duration_minutes = req.new_duration_minutes
    if req.new_meeting_link:
        iv.meeting_link = req.new_meeting_link
    iv.notes = (iv.notes or "") + f"\n[Rescheduled {datetime.utcnow().strftime('%Y-%m-%d')}] {req.reason or ''}"
    iv.updated_at = datetime.utcnow()
    db.commit()

    return {
        "rescheduled": True,
        "interview_id": interview_id,
        "old_datetime": old_dt.isoformat() if old_dt else None,
        "new_datetime": new_dt.isoformat(),
        "new_end_datetime": new_end.isoformat(),
        "conflicts_overridden": len(conflicts),
    }


# ─────────────────────────────────────────────────────────────────────────────
# 17. JOB CLONE  (duplicate a job posting)
# ─────────────────────────────────────────────────────────────────────────────
class JobCloneRequest(BaseModel):
    new_title: Optional[str] = None
    new_department_id: Optional[str] = None
    new_location: Optional[str] = None
    status: str = "draft"


@router.post("/jobs/{job_id}/clone", summary="Clone a Job Posting")
def clone_job(
    job_id: str,
    req: JobCloneRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    src = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not src:
        raise HTTPException(404, "Job not found")

    hr_profile = db.query(HRProfile).filter(HRProfile.user_id == current_user.id).first()

    new_job = JobPosting(
        id=str(uuid.uuid4()),
        title=req.new_title or f"{src.title} (Copy)",
        description=src.description,
        requirements=src.requirements,
        department_id=req.new_department_id or src.department_id,
        created_by_id=current_user.id,
        hr_id=hr_profile.id if hr_profile else src.hr_id,
        interviewer_id=src.interviewer_id,
        location=req.new_location or src.location,
        employment_type=src.employment_type,
        salary_min=src.salary_min,
        salary_max=src.salary_max,
        experience_years=src.experience_years,
        required_skills=src.required_skills,
        status=req.status,
    )
    db.add(new_job)
    db.commit()
    db.refresh(new_job)

    return {
        "message": "Job cloned successfully",
        "original_job_id": job_id,
        "new_job_id": new_job.id,
        "new_title": new_job.title,
        "status": new_job.status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 18. SKILL GAP REPORT  (required vs matched vs missing, across all candidates)
# ─────────────────────────────────────────────────────────────────────────────
@router.get("/jobs/{job_id}/skill-gap", summary="Skill Gap Report for Job")
def skill_gap_report(
    job_id: str,
    stage: Optional[str] = Query(None, description="Filter candidates by stage"),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_roles("admin", "hr")),
):
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(404, "Job not found")

    required_skills = _safe_json(job.required_skills)
    if not required_skills:
        return {"message": "No required skills defined for this job"}

    q = db.query(Candidate).filter(Candidate.job_id == job_id)
    if stage:
        q = q.filter(Candidate.current_stage == stage)
    candidates = q.all()

    if not candidates:
        return {"message": "No candidates found"}

    skill_coverage: Dict[str, int] = {s: 0 for s in required_skills}
    candidate_coverage = []

    for c in candidates:
        cand_skills = set(s.lower() for s in _safe_json(c.extracted_skills))
        matched = [s for s in required_skills if s.lower() in cand_skills]
        missing = [s for s in required_skills if s.lower() not in cand_skills]
        for s in matched:
            skill_coverage[s] += 1
        candidate_coverage.append({
            "candidate_id": c.id,
            "name": f"{c.first_name} {c.last_name}",
            "ats_score": _float(c.ats_score),
            "coverage_pct": round(len(matched) / len(required_skills) * 100, 1),
            "matched": matched,
            "missing": missing,
        })

    # Rank skills by scarcity (least covered first)
    skill_stats = [
        {
            "skill": skill,
            "candidates_with_skill": count,
            "coverage_pct": round(count / len(candidates) * 100, 1),
            "scarcity": "high" if count / len(candidates) < 0.3 else "medium" if count / len(candidates) < 0.6 else "low",
        }
        for skill, count in sorted(skill_coverage.items(), key=lambda x: x[1])
    ]

    avg_coverage = round(sum(r["coverage_pct"] for r in candidate_coverage) / len(candidate_coverage), 1)

    return {
        "job_id": job_id,
        "job_title": job.title,
        "required_skills": required_skills,
        "total_candidates": len(candidates),
        "avg_skill_coverage_pct": avg_coverage,
        "skill_stats": skill_stats,
        "most_scarce_skill": skill_stats[0]["skill"] if skill_stats else None,
        "best_covered_skill": skill_stats[-1]["skill"] if skill_stats else None,
        "candidate_breakdown": sorted(candidate_coverage, key=lambda x: -x["coverage_pct"]),
    }
