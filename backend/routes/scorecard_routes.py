"""
📝 SCORECARD ROUTES — /api/v1/scorecards

Independent panellist scorecards; the rules are in src/services/scorecards.py.
An interview is reachable by its panellists, by HR on its job, and by admins —
anyone else gets a 404, indistinguishable from an id that doesn't exist.
"""

from typing import List, Optional, Union

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from src.auth import get_current_user
from src.config.database_config_mysql import get_db
from src.models.models import HRProfile, Interview, InterviewScorecard, User
from src.services import scorecards
from src.services.job_access import can_access_job

router = APIRouter(prefix="/api/v1/scorecards", tags=["Scorecards"])

_STAFF = ("admin", "hr", "interviewer")


def _staff(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role not in _STAFF:
        raise HTTPException(403, "Staff access required")
    return current_user


def _interview_or_404(db, interview_id, user) -> Interview:
    iv = db.query(Interview).filter(Interview.id == interview_id,
                                    Interview.company_id == user.company_id).first()
    if not iv:
        raise HTTPException(404, "Interview not found")
    if scorecards.is_panelist(user, iv) or user.role == "admin":
        return iv
    if user.role == "hr" and iv.job is not None:
        hp = db.query(HRProfile).filter(HRProfile.user_id == user.id,
                                        HRProfile.company_id == user.company_id).first()
        if can_access_job(db, iv.job, hp):
            return iv
    raise HTTPException(404, "Interview not found")


class ScorecardIn(BaseModel):
    technical_score: Optional[float] = None
    communication_score: Optional[float] = None
    problem_solving_score: Optional[float] = None
    cultural_fit_score: Optional[float] = None
    strengths: Optional[Union[List[str], str]] = None
    weaknesses: Optional[Union[List[str], str]] = None
    recommendations: Optional[str] = None
    hire_recommendation: Optional[str] = None
    notes: Optional[str] = None


@router.get("/pending")
async def my_pending_scorecards(current_user: User = Depends(_staff), db: Session = Depends(get_db)):
    """Interviews I sit on whose scorecard I have not submitted (cancelled ones excluded)."""
    email = (current_user.email or "").lower()
    ivs = db.query(Interview).filter(
        Interview.company_id == current_user.company_id,
        or_(func.lower(Interview.interviewer_email) == email,
            Interview.panel_members.like(f'%"{current_user.email}"%')),
        func.upper(func.coalesce(Interview.status, "")) != "CANCELLED",
        Interview.is_scheduled_interview.is_(True),
    ).order_by(Interview.scheduled_datetime.desc()).limit(200).all()
    ivs = [iv for iv in ivs if scorecards.is_panelist(current_user, iv)]
    done = scorecards.submitted_interview_ids(db, current_user, [iv.id for iv in ivs])
    drafts = {c.interview_id for c in db.query(InterviewScorecard).filter(
        InterviewScorecard.panelist_user_id == current_user.id,
        InterviewScorecard.status == "draft").all()}
    out = []
    for iv in ivs:
        if iv.id in done:
            continue
        cand = iv.candidate
        out.append({
            "interview_id": iv.id,
            "candidate_name": f"{cand.first_name} {cand.last_name}".strip() if cand else None,
            "job_title": iv.job.title if iv.job else iv.job_title,
            "scheduled_datetime": iv.scheduled_datetime.isoformat() if iv.scheduled_datetime else None,
            "status": iv.status,
            "has_draft": iv.id in drafts,
        })
    return {"pending": out}


@router.get("/interviews/{interview_id}")
async def get_scorecards(interview_id: int, current_user: User = Depends(_staff),
                         db: Session = Depends(get_db)):
    iv = _interview_or_404(db, interview_id, current_user)
    out = scorecards.scorecard_overview(db, iv, current_user)
    cand = iv.candidate
    out["candidate_name"] = f"{cand.first_name} {cand.last_name}".strip() if cand else None
    out["job_title"] = iv.job.title if iv.job else iv.job_title
    return out


@router.put("/interviews/{interview_id}/mine")
async def save_my_scorecard(interview_id: int, body: ScorecardIn,
                            current_user: User = Depends(_staff), db: Session = Depends(get_db)):
    iv = _interview_or_404(db, interview_id, current_user)
    scorecards.save_scorecard(db, iv, current_user, body.model_dump(exclude_unset=True))
    db.commit()
    return scorecards.scorecard_overview(db, iv, current_user)


@router.post("/interviews/{interview_id}/mine/submit")
async def submit_my_scorecard(interview_id: int, body: ScorecardIn,
                              current_user: User = Depends(_staff), db: Session = Depends(get_db)):
    iv = _interview_or_404(db, interview_id, current_user)
    scorecards.save_scorecard(db, iv, current_user, body.model_dump(exclude_unset=True), submit=True)
    db.commit()
    return scorecards.scorecard_overview(db, iv, current_user)
