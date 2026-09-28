"""Explain, override, rescore and trace resume scores (HR).

- GET    /jobs/{job_id}/candidates/{candidate_id}/ats-explanation
- POST   /jobs/{job_id}/candidates/{candidate_id}/ats-override     {new_score, reason}
- DELETE /jobs/{job_id}/candidates/{candidate_id}/ats-override     {reason}
- GET    /jobs/{job_id}/candidates/{candidate_id}/ats-history
- POST   /jobs/{job_id}/candidates/{candidate_id}/rescore
- POST   /jobs/{job_id}/rescore-stale                             {limit}
- GET    /jobs/{job_id}/jd-versions[/{version_id}]

Kept out of hr_routes.py, which is already very large. Every endpoint is HR/admin only and
checks the caller can access the job. Candidates never see explanations or overrides.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import AuthMiddleware
from src.config.database_config_mysql import get_db
from src.models.models import (
    ATSResult, ATSScoreEvent, Candidate, JobDescriptionVersion, JobPosting, User,
)
from src.services import parse_quality
from src.services.ats_scoring_pipeline import (
    build_inputs_for_candidate, effective_score, persist_ats_result, score_for_job,
)
from src.services.enhanced_ats_service import SCORING_RULES_VERSION
from src.services.jd_versions import staleness
from src.services.job_access import can_access_job

router = APIRouter(prefix="/api/v1/hr", tags=["ATS explainability"])


def _job(db: Session, current_user: User, job_id: str) -> JobPosting:
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id, JobPosting.company_id == current_user.company_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if current_user.role != "admin":
        from routes.hr_routes import get_hr_profile
        if not can_access_job(db, job, get_hr_profile(db, current_user)):
            raise HTTPException(status_code=403, detail="You don't have access to this job")
    return job


def _candidate_result(db: Session, job: JobPosting, candidate_id: str):
    cand = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.job_id == job.id).first()
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found for this job")
    row = db.query(ATSResult).filter(ATSResult.candidate_id == cand.id, ATSResult.job_id == job.id).first()
    return cand, row


def _loads(raw, default):
    if raw is None:
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


def _version_no(db, version_id) -> Optional[int]:
    if not version_id:
        return None
    v = db.query(JobDescriptionVersion.version_no).filter(JobDescriptionVersion.id == version_id).first()
    return v[0] if v else None


def _event_dict(e: ATSScoreEvent, names: dict) -> dict:
    return {
        "id": e.id, "event_type": e.event_type, "model_score": e.model_score,
        "previous_effective_score": e.previous_effective_score,
        "new_effective_score": e.new_effective_score, "reason": e.reason,
        "actor": names.get(e.actor_user_id) if e.actor_user_id else "System",
        "scoring_rules_version": e.scoring_rules_version,
        "created_at": e.created_at.isoformat() if e.created_at else None,
    }


@router.get("/jobs/{job_id}/candidates/{candidate_id}/ats-explanation")
async def ats_explanation(job_id: str, candidate_id: str,
                          current_user: User = Depends(AuthMiddleware.get_current_hr),
                          db: Session = Depends(get_db)):
    """Everything behind one score: the breakdown, resume excerpts, what was not found,
    parse quality, provenance, staleness and any override."""
    job = _job(db, current_user, job_id)
    cand, row = _candidate_result(db, job, candidate_id)
    if not row:
        raise HTTPException(status_code=404, detail="This candidate has not been scored for this job")
    issues = _loads(row.parse_issues, [])
    return {
        "candidate_id": cand.id,
        "job_id": job.id,
        "effective_score": effective_score(row),
        "model_score": float(row.ats_score) if row.ats_score is not None else None,
        "override_score": row.override_score,
        "parse": {"status": row.parse_status, "confidence": row.parse_confidence,
                  "issues": issues, "issue_text": parse_quality.describe(issues)},
        "scoring_source": row.scoring_source,
        "mandatory": {"met": row.mandatory_met, "total": row.mandatory_total},
        "evidence_coverage": row.evidence_coverage,
        "explanation": _loads(row.explanation, None),
        "scoring_breakdown": _loads(row.scoring_breakdown, None),
        "provenance": {
            "scoring_rules_version": row.scoring_rules_version,
            "current_rules_version": SCORING_RULES_VERSION,
            "jd_version_no": _version_no(db, row.jd_version_id),
            "current_jd_version_no": _version_no(db, job.current_jd_version_id),
            "model_version": _loads(row.model_version, None),
            "input_hash": row.input_hash,
            "scored_at": row.scored_at.isoformat() if row.scored_at else None,
        },
        "stale_reason": staleness(row, job, SCORING_RULES_VERSION),
    }


class OverrideRequest(BaseModel):
    new_score: float = Field(..., ge=0, le=100)
    reason: str = Field(..., min_length=10, max_length=2000)


class ClearOverrideRequest(BaseModel):
    reason: str = Field(..., min_length=10, max_length=2000)


def _user_names(db, ids) -> dict:
    ids = {i for i in ids if i}
    return {u.id: f"{u.first_name} {u.last_name}".strip()
            for u in db.query(User).filter(User.id.in_(ids)).all()} if ids else {}


@router.post("/jobs/{job_id}/candidates/{candidate_id}/ats-override")
async def override_score(job_id: str, candidate_id: str, body: OverrideRequest,
                         current_user: User = Depends(AuthMiddleware.get_current_hr),
                         db: Session = Depends(get_db)):
    """Replace the model score with HR's own, with a reason. The model score is kept and
    stays visible; rankings and shortlists use the override from now on."""
    job = _job(db, current_user, job_id)
    cand, row = _candidate_result(db, job, candidate_id)
    if not row:
        raise HTTPException(status_code=404, detail="This candidate has not been scored for this job")
    previous = effective_score(row)
    event = ATSScoreEvent(
        company_id=row.company_id, ats_result_id=row.id, candidate_id=cand.id, job_id=job.id,
        event_type="override", model_score=float(row.ats_score or 0),
        previous_effective_score=previous, new_effective_score=round(body.new_score, 2),
        reason=body.reason.strip(), actor_user_id=current_user.id,
        scoring_rules_version=row.scoring_rules_version, jd_version_id=row.jd_version_id,
        created_at=datetime.utcnow(),
    )
    db.add(event)
    db.flush()
    row.override_score = round(body.new_score, 2)
    row.override_event_id = event.id
    cand.ats_score = row.override_score
    db.commit()
    return {"effective_score": row.override_score, "model_score": float(row.ats_score or 0),
            "event": _event_dict(event, _user_names(db, {current_user.id}))}


@router.delete("/jobs/{job_id}/candidates/{candidate_id}/ats-override")
async def clear_override(job_id: str, candidate_id: str, body: ClearOverrideRequest,
                         current_user: User = Depends(AuthMiddleware.get_current_hr),
                         db: Session = Depends(get_db)):
    job = _job(db, current_user, job_id)
    cand, row = _candidate_result(db, job, candidate_id)
    if not row or row.override_score is None:
        raise HTTPException(status_code=404, detail="There is no override to remove")
    previous = effective_score(row)
    row.override_score = None
    row.override_event_id = None
    event = ATSScoreEvent(
        company_id=row.company_id, ats_result_id=row.id, candidate_id=cand.id, job_id=job.id,
        event_type="override_cleared", model_score=float(row.ats_score or 0),
        previous_effective_score=previous, new_effective_score=effective_score(row),
        reason=body.reason.strip(), actor_user_id=current_user.id,
        scoring_rules_version=row.scoring_rules_version, jd_version_id=row.jd_version_id,
        created_at=datetime.utcnow(),
    )
    db.add(event)
    cand.ats_score = effective_score(row)
    db.commit()
    return {"effective_score": effective_score(row), "event": _event_dict(event, _user_names(db, {current_user.id}))}


@router.get("/jobs/{job_id}/candidates/{candidate_id}/ats-history")
async def score_history(job_id: str, candidate_id: str,
                        current_user: User = Depends(AuthMiddleware.get_current_hr),
                        db: Session = Depends(get_db)):
    job = _job(db, current_user, job_id)
    cand, _ = _candidate_result(db, job, candidate_id)
    events = db.query(ATSScoreEvent).filter(
        ATSScoreEvent.candidate_id == cand.id, ATSScoreEvent.job_id == job.id
    ).order_by(ATSScoreEvent.created_at.desc()).all()
    names = _user_names(db, {e.actor_user_id for e in events})
    return [_event_dict(e, names) for e in events]


def rescoring_inputs(db, cand):
    """Inputs for re-scoring an existing application.

    Uses the resume text stored when they applied, and goes back to the file only when
    there is none. Re-reading the file first meant a file that had since moved or become
    unreachable turned a readable applicant into "needs review" at every rescore.
    """
    stored = (cand.resume_text or "").strip()
    return build_inputs_for_candidate(
        db, user_id=cand.user_id,
        resume_path=None if stored else (cand.resume_path or None),
        resume_text=cand.resume_text if stored else None)


def _rescore(db, job, cand, actor_id):
    """Score again against the job as it is now. Never changes the candidate's stage."""
    outcome = score_for_job(rescoring_inputs(db, cand), job)
    return persist_ats_result(db, candidate=cand, job=job, outcome=outcome,
                              event_type="rescored", actor_id=actor_id)


@router.post("/jobs/{job_id}/candidates/{candidate_id}/rescore")
async def rescore_candidate(job_id: str, candidate_id: str,
                            current_user: User = Depends(AuthMiddleware.get_current_hr),
                            db: Session = Depends(get_db)):
    job = _job(db, current_user, job_id)
    cand, _ = _candidate_result(db, job, candidate_id)
    row = _rescore(db, job, cand, current_user.id)
    db.commit()
    return {"effective_score": effective_score(row), "model_score": float(row.ats_score or 0),
            "parse_status": row.parse_status, "is_overridden": row.override_score is not None}


class RescoreStaleRequest(BaseModel):
    limit: int = Field(25, ge=1, le=100)


@router.post("/jobs/{job_id}/rescore-stale")
async def rescore_stale(job_id: str, body: RescoreStaleRequest,
                        current_user: User = Depends(AuthMiddleware.get_current_hr),
                        db: Session = Depends(get_db)):
    """Rescore results measured against an older job description or older rules, a batch
    at a time (the caller repeats until `remaining` is 0). Scores are never rescored
    automatically: rankings only move when HR chooses to."""
    job = _job(db, current_user, job_id)
    rows = db.query(ATSResult).filter(ATSResult.job_id == job.id).all()
    stale = [r for r in rows if staleness(r, job, SCORING_RULES_VERSION) is not None]
    done = failed = 0
    for r in stale[:body.limit]:
        cand = db.query(Candidate).filter(Candidate.id == r.candidate_id).first()
        if not cand:
            continue
        try:
            _rescore(db, job, cand, current_user.id)
            db.commit()
            done += 1
        except Exception:
            db.rollback()
            failed += 1
    return {"rescored": done, "failed": failed, "remaining": max(0, len(stale) - body.limit)}


@router.get("/jobs/{job_id}/jd-versions")
async def jd_versions(job_id: str, current_user: User = Depends(AuthMiddleware.get_current_hr),
                      db: Session = Depends(get_db)):
    job = _job(db, current_user, job_id)
    versions = db.query(JobDescriptionVersion).filter(
        JobDescriptionVersion.job_id == job.id).order_by(JobDescriptionVersion.version_no.desc()).all()
    return [{"id": v.id, "version_no": v.version_no, "current": v.id == job.current_jd_version_id,
             "created_at": v.created_at.isoformat() if v.created_at else None} for v in versions]


@router.get("/jobs/{job_id}/jd-versions/{version_id}")
async def jd_version(job_id: str, version_id: str,
                     current_user: User = Depends(AuthMiddleware.get_current_hr),
                     db: Session = Depends(get_db)):
    job = _job(db, current_user, job_id)
    v = db.query(JobDescriptionVersion).filter(
        JobDescriptionVersion.id == version_id, JobDescriptionVersion.job_id == job.id).first()
    if not v:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"id": v.id, "version_no": v.version_no, "title": v.title, "description": v.description,
            "requirements": v.requirements, "required_skills": _loads(v.required_skills, []),
            "preferred_skills": _loads(v.preferred_skills, None), "experience_years": v.experience_years,
            "created_at": v.created_at.isoformat() if v.created_at else None}


@router.get("/resume-pool/{entry_id}/matches/{job_id}/explanation")
async def pool_match_explanation(entry_id: str, job_id: str,
                                 current_user: User = Depends(AuthMiddleware.get_current_hr),
                                 db: Session = Depends(get_db)):
    """Why a pooled resume matches (or doesn't) a job: computed on demand and not saved.

    Pool entries are matched against every open job, so storing a full explanation per
    job would be large and mostly unread. This scores the stored text against the job as
    it is now, exactly as an application would be, and returns the evidence.
    """
    from routes.hr_routes import _get_pool_entry
    entry = _get_pool_entry(db, entry_id, current_user)
    job = _job(db, current_user, job_id)
    source_user = getattr(entry, "source_user_id", None)
    inputs = build_inputs_for_candidate(db, user_id=source_user, resume_text=entry.resume_text or "")
    outcome = score_for_job(inputs, job)
    r = outcome.result
    return {
        "entry_id": entry.id,
        "job_id": job.id,
        "score": float(r.ats_score or 0),
        "parse": {"status": outcome.parse["status"], "issues": outcome.parse["issues"],
                  "issue_text": parse_quality.describe(outcome.parse["issues"])},
        "scoring_source": outcome.scoring_source,
        "mandatory": {"met": getattr(r, "mandatory_met", None), "total": getattr(r, "mandatory_total", None)},
        "evidence_coverage": getattr(r, "evidence_coverage", None),
        "explanation": getattr(r, "explanation", None) or None,
        "scoring_breakdown": r.scoring_breakdown or None,
        "provenance": {"scoring_rules_version": SCORING_RULES_VERSION,
                       "jd_version_no": _version_no(db, job.current_jd_version_id)},
    }


# ── Checking the ranking against independent review ─────────────────────────

from src.models.models import (  # noqa: E402
    MissedCandidateInvestigation, ReviewSample, ScreeningReview,
)
from src.services import screening_review as sr  # noqa: E402


class SampleRequest(BaseModel):
    cutoff_score: Optional[float] = Field(None, ge=0, le=100)
    top_n: Optional[int] = Field(None, ge=1)
    size: int = Field(10, ge=1, le=50)
    include_needs_review: bool = True
    seed: Optional[int] = None


def _blinded(db, sample: ReviewSample, reviewer_id: str):
    ids = json.loads(sample.candidate_ids or "[]")
    cands = {c.id: c for c in db.query(Candidate).filter(Candidate.id.in_(ids)).all()} if ids else {}
    mine = {r.candidate_id: r for r in db.query(ScreeningReview).filter(
        ScreeningReview.sample_id == sample.id, ScreeningReview.reviewer_user_id == reviewer_id).all()}
    # No score, rank or explanation: the reviewer judges the resume alone.
    return [{"candidate_id": cid, "name": f"{cands[cid].first_name} {cands[cid].last_name}".strip(),
             "resume_path": cands[cid].resume_path,
             "my_judgement": mine[cid].judgement if cid in mine else None}
            for cid in ids if cid in cands]


@router.post("/jobs/{job_id}/review-samples")
async def create_review_sample(job_id: str, body: SampleRequest,
                               current_user: User = Depends(AuthMiddleware.get_current_hr),
                               db: Session = Depends(get_db)):
    """A random sample of applicants below the cutoff, for a blind review."""
    job = _job(db, current_user, job_id)
    sample = sr.draw_sample(db, job, cutoff_score=body.cutoff_score, top_n=body.top_n, size=body.size,
                            seed=body.seed, include_needs_review=body.include_needs_review,
                            reviewer_id=current_user.id)
    db.commit()
    return {"sample_id": sample.id, "seed": sample.seed, "below_cutoff_population": sample.below_cutoff_population,
            "candidates": _blinded(db, sample, current_user.id)}


@router.get("/review-samples/{sample_id}")
async def get_review_sample(sample_id: str, current_user: User = Depends(AuthMiddleware.get_current_hr),
                            db: Session = Depends(get_db)):
    sample = db.query(ReviewSample).filter(ReviewSample.id == sample_id).first()
    if not sample:
        raise HTTPException(status_code=404, detail="Sample not found")
    _job(db, current_user, sample.job_id)
    return {"sample_id": sample.id, "job_id": sample.job_id, "candidates": _blinded(db, sample, current_user.id)}


class ScreeningReviewRequest(BaseModel):
    judgement: str
    reason: str = Field(..., min_length=10, max_length=2000)
    sample_id: Optional[str] = None


@router.post("/jobs/{job_id}/candidates/{candidate_id}/screening-review")
async def add_screening_review(job_id: str, candidate_id: str, body: ScreeningReviewRequest,
                               current_user: User = Depends(AuthMiddleware.get_current_hr),
                               db: Session = Depends(get_db)):
    """An independent judgement of whether this applicant suits the job, with a reason."""
    if body.judgement not in sr.JUDGEMENTS:
        raise HTTPException(status_code=422, detail="judgement must be suitable or not_suitable")
    job = _job(db, current_user, job_id)
    cand = db.query(Candidate).filter(Candidate.id == candidate_id, Candidate.job_id == job.id).first()
    if not cand:
        raise HTTPException(status_code=404, detail="Candidate not found for this job")
    rv = sr.record_review(db, job, cand, judgement=body.judgement, reason=body.reason.strip(),
                          reviewer_id=current_user.id, sample_id=body.sample_id)
    db.commit()
    return {"id": rv.id, "judgement": rv.judgement, "blind": rv.blind}


@router.get("/jobs/{job_id}/screening-report")
async def screening_report(job_id: str, cutoff_score: Optional[float] = Query(None, ge=0, le=100),
                           top_n: Optional[int] = Query(None, ge=1),
                           current_user: User = Depends(AuthMiddleware.get_current_hr),
                           db: Session = Depends(get_db)):
    job = _job(db, current_user, job_id)
    return sr.report(db, job, cutoff_score=cutoff_score, top_n=top_n)


class InvestigationRequest(BaseModel):
    cause: str
    notes: Optional[str] = Field(None, max_length=4000)


@router.post("/jobs/{job_id}/candidates/{candidate_id}/missed-investigation")
async def record_missed_investigation(job_id: str, candidate_id: str, body: InvestigationRequest,
                                      current_user: User = Depends(AuthMiddleware.get_current_hr),
                                      db: Session = Depends(get_db)):
    """Why a suitable applicant ended up below the cutoff."""
    if body.cause not in sr.CAUSES:
        raise HTTPException(status_code=422, detail=f"cause must be one of {', '.join(sr.CAUSES)}")
    if body.cause == "other" and not (body.notes or "").strip():
        raise HTTPException(status_code=422, detail="Describe the cause when choosing 'other'")
    job = _job(db, current_user, job_id)
    latest = (db.query(ScreeningReview).filter(ScreeningReview.job_id == job.id,
                                               ScreeningReview.candidate_id == candidate_id)
              .order_by(ScreeningReview.created_at.desc()).first())
    if not latest or latest.judgement != "suitable":
        raise HTTPException(status_code=409, detail="Only an applicant a reviewer judged suitable can be a missed candidate")
    inv = MissedCandidateInvestigation(
        company_id=job.company_id, job_id=job.id, candidate_id=candidate_id, review_id=latest.id,
        cause=body.cause, notes=(body.notes or "").strip() or None, investigator_user_id=current_user.id,
        created_at=datetime.utcnow(),
    )
    db.add(inv)
    db.commit()
    return {"id": inv.id, "cause": inv.cause}
