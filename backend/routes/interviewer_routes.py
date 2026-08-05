"""
🎯 INTERVIEWER API ROUTES
Handles interview viewing, feedback submission, and interviewer-specific operations
"""

from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from typing import List, Optional
from src.config.database_config_mysql import get_db
from src.auth import get_current_user, AuthMiddleware
from src.models.models import (
    User, Interview, InterviewFeedback, InterviewerProfile, Candidate, JobPosting
)
from pydantic import BaseModel
from datetime import datetime

router = APIRouter(prefix="/api/v1/interviewer", tags=["Interviewers"])

# ==================== PYDANTIC MODELS ====================

class InterviewResponse(BaseModel):
    interview_id: str
    candidate_name: str
    job_title: str
    interview_type: str
    scheduled_datetime: datetime
    duration_minutes: int
    meeting_link: str
    status: str
    candidate_email: str
    candidate_phone: Optional[str]

class InterviewFeedbackCreate(BaseModel):
    technical_score: float
    communication_score: float
    problem_solving_score: float
    cultural_fit_score: float
    strengths: List[str]
    weaknesses: List[str]
    recommendations: str
    hire_recommendation: str
    additional_notes: Optional[str] = None

class InterviewFeedbackResponse(BaseModel):
    interview_id: str
    candidate_name: str
    job_title: str
    technical_score: float
    communication_score: float
    problem_solving_score: float
    cultural_fit_score: float
    overall_score: float
    strengths: List[str]
    weaknesses: List[str]
    recommendations: str
    hire_recommendation: str
    additional_notes: Optional[str]
    created_at: datetime

# ==================== HELPER FUNCTIONS ====================

def get_interviewer_profile(db: Session, user: User) -> InterviewerProfile:
    """Get interviewer profile for user, auto-creating if absent"""
    interviewer_profile = db.query(InterviewerProfile).filter(
        InterviewerProfile.user_id == user.id
    ).first()
    if not interviewer_profile:
        from src.models.models import Department
        dept = db.query(Department).filter(Department.company_id == user.company_id).first() if user.company_id else None
        interviewer_profile = InterviewerProfile(
            user_id=user.id,
            department_id=dept.id if dept else None,
            employee_id=f"IV-{str(user.id)[:8]}"
        )
        db.add(interviewer_profile)
        db.commit()
        db.refresh(interviewer_profile)
    return interviewer_profile

def create_interview_response(interview: Interview) -> InterviewResponse:
    """Create interview response"""
    candidate = interview.candidate
    job = interview.job
    return InterviewResponse(
        interview_id=str(interview.id),
        candidate_name=f"{candidate.first_name} {candidate.last_name}" if candidate else "Not Assigned",
        job_title=job.title if job else (interview.job_title or "N/A"),
        interview_type=interview.interview_type,
        scheduled_datetime=interview.scheduled_datetime,
        duration_minutes=interview.duration_minutes,
        meeting_link=interview.meeting_link or "",
        status=interview.status,
        candidate_email=candidate.email if candidate else "",
        candidate_phone=candidate.phone if candidate else ""
    )

def calculate_overall_score(
    technical_score: float,
    communication_score: float,
    problem_solving_score: float,
    cultural_fit_score: float
) -> float:
    """Calculate overall interview score"""
    return (
        technical_score * 0.3 +
        communication_score * 0.2 +
        problem_solving_score * 0.3 +
        cultural_fit_score * 0.2
    )

# ==================== INTERVIEW MANAGEMENT ENDPOINTS ====================

@router.get("/interviews", response_model=List[InterviewResponse])
async def get_my_interviews(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    upcoming_only: bool = False,
    current_user: User = Depends(AuthMiddleware.get_current_interviewer),
    db: Session = Depends(get_db)
):
    """
    Get interviews assigned to the current interviewer
    
    - **skip**: Number of records to skip (pagination)
    - **limit**: Maximum number of records to return
    - **status**: Filter by interview status
    - **upcoming_only**: Show only upcoming interviews
    """
    interviewer_profile = get_interviewer_profile(db, current_user)
    
    query = db.query(Interview).filter(
        Interview.company_id == current_user.company_id,
        (Interview.interviewer_email == current_user.email) |
        (Interview.created_by == current_user.email) |
        # Panel members aren't the lead, so also match their email inside the
        # panel_members JSON roster.
        (Interview.panel_members.like(f'%"{current_user.email}"%'))
    )
    
    if status:
        query = query.filter(Interview.status == status)
    
    if upcoming_only:
        query = query.filter(
            Interview.scheduled_datetime >= datetime.utcnow(),
            Interview.status == "scheduled"
        )
    
    interviews = query.order_by(Interview.scheduled_datetime.asc()).offset(skip).limit(limit).all()
    
    return [create_interview_response(interview) for interview in interviews]

@router.get("/interviews/{interview_id}", response_model=InterviewResponse)
async def get_interview_details(
    interview_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_interviewer),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific interview"""
    interviewer_profile = get_interviewer_profile(db, current_user)
    
    interview = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == current_user.company_id,
        (Interview.interviewer_email == current_user.email) | (Interview.created_by == current_user.email)
    ).first()

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found or not assigned to you")

    return create_interview_response(interview)

@router.get("/interviews/{interview_id}/candidate")
async def get_interview_candidate_info(
    interview_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_interviewer),
    db: Session = Depends(get_db)
):
    """
    Get candidate information for an interview
    Note: Limited information provided to interviewer (sandboxed access)
    """
    interviewer_profile = get_interviewer_profile(db, current_user)
    
    interview = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == current_user.company_id,
        (Interview.interviewer_email == current_user.email) | (Interview.created_by == current_user.email)
    ).first()

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found or not assigned to you")

    candidate = interview.candidate
    
    # Get ATS result scoped to this interview's job so multi-job candidates show correct data
    from src.models.models import ATSResult
    ats_result = db.query(ATSResult).filter(
        ATSResult.candidate_id == candidate.id,
        ATSResult.job_id == interview.job_id
    ).first()
    
    return {
        "candidate_info": {
            "name": f"{candidate.first_name} {candidate.last_name}",
            "email": candidate.email,
            "phone": candidate.phone,
            "extracted_skills": candidate.extracted_skills or [],
            "experience_years": candidate.experience_years
        },
        "job_info": {
            "title": interview.job.title,
            "required_skills": interview.job.required_skills or [],
            "experience_years": interview.job.experience_years
        },
        "ats_highlights": {
            "matched_skills": ats_result.matched_skills if ats_result else [],
            "missing_skills": ats_result.missing_skills if ats_result else [],
            "skills_match_score": float(ats_result.skills_match_score) if ats_result else None
        } if ats_result else None
    }

# ==================== FEEDBACK ENDPOINTS ====================

@router.post("/interviews/{interview_id}/feedback")
async def submit_interview_feedback(
    interview_id: str,
    feedback_data: InterviewFeedbackCreate,
    current_user: User = Depends(AuthMiddleware.get_current_interviewer),
    db: Session = Depends(get_db)
):
    """
    Submit interview feedback (PHASE 8)
    
    - **interview_id**: Interview ID
    - **technical_score**: Technical skills score (0-100)
    - **communication_score**: Communication score (0-100)
    - **problem_solving_score**: Problem solving score (0-100)
    - **cultural_fit_score**: Cultural fit score (0-100)
    - **strengths**: List of candidate strengths
    - **weaknesses**: List of candidate weaknesses
    - **recommendations**: Recommendations
    - **hire_recommendation**: Hire recommendation (strong_hire, hire, no_hire, strong_no_hire)
    - **additional_notes**: Additional notes
    """
    interviewer_profile = get_interviewer_profile(db, current_user)
    
    # Validate interview
    interview = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == current_user.company_id,
        (Interview.interviewer_email == current_user.email) | (Interview.created_by == current_user.email)
    ).first()

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found or not assigned to you")

    # Normalize scores: accept 0-10 or 0-100
    for attr in ["technical_score", "communication_score", "problem_solving_score", "cultural_fit_score"]:
        val = getattr(feedback_data, attr)
        if val <= 10:
            setattr(feedback_data, attr, val * 10)  # scale 0-10 to 0-100

    # Validate hire recommendation
    valid_recommendations = ["strong_hire", "hire", "proceed", "no_hire", "strong_no_hire"]
    if feedback_data.hire_recommendation not in valid_recommendations:
        raise HTTPException(
            status_code=400,
            detail=f"hire_recommendation must be one of: {valid_recommendations}"
        )
    
    # Calculate overall score
    overall_score = calculate_overall_score(
        feedback_data.technical_score,
        feedback_data.communication_score,
        feedback_data.problem_solving_score,
        feedback_data.cultural_fit_score
    )
    
    # Check if feedback already exists
    existing_fb = db.query(InterviewFeedback).filter(InterviewFeedback.interview_id == interview_id).first()
    if existing_fb:
        raise HTTPException(status_code=409, detail="Feedback already submitted for this interview")

    # Create feedback
    import json as _json
    strengths_str = _json.dumps(feedback_data.strengths) if isinstance(feedback_data.strengths, list) else feedback_data.strengths
    weaknesses_str = _json.dumps(feedback_data.weaknesses) if isinstance(feedback_data.weaknesses, list) else feedback_data.weaknesses

    db_feedback = InterviewFeedback(
        interview_id=interview_id,
        interviewer_id=interviewer_profile.id,
        technical_score=feedback_data.technical_score,
        communication_score=feedback_data.communication_score,
        problem_solving_score=feedback_data.problem_solving_score,
        cultural_fit_score=feedback_data.cultural_fit_score,
        overall_score=overall_score,
        strengths=strengths_str,
        weaknesses=weaknesses_str,
        recommendations=feedback_data.recommendations,
        hire_recommendation=feedback_data.hire_recommendation,
        additional_notes=feedback_data.additional_notes
    )
    
    db.add(db_feedback)

    # Update interview status
    interview.status = "completed"

    # Update candidate stage
    from src.models.models import CandidateStageHistory, Candidate
    if interview.candidate_id:
        cand = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
        if cand:
            prev = cand.current_stage
            cand.current_stage = "interview_completed"
            stage_history = CandidateStageHistory(
                candidate_id=interview.candidate_id,
                from_stage=prev,
                to_stage="interview_completed",
                changed_by_id=current_user.id,
                notes=f"Interview completed, feedback submitted. Overall score: {round(overall_score, 1)}"
            )
            db.add(stage_history)

    db.commit()
    
    return {
        "message": "Interview feedback submitted successfully",
        "interview_id": interview_id,
        "overall_score": round(overall_score, 2),
        "hire_recommendation": feedback_data.hire_recommendation
    }

@router.get("/interviews/{interview_id}/feedback")
async def get_interview_feedback(
    interview_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_interviewer),
    db: Session = Depends(get_db)
):
    """Get feedback for an interview (can view own submitted feedback)"""
    interviewer_profile = get_interviewer_profile(db, current_user)
    
    # Validate interview
    interview = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == current_user.company_id,
        (Interview.interviewer_email == current_user.email) | (Interview.created_by == current_user.email)
    ).first()

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found or not assigned to you")

    # Get feedback
    feedback = db.query(InterviewFeedback).filter(
        InterviewFeedback.interview_id == interview_id
    ).first()
    
    if not feedback:
        raise HTTPException(status_code=404, detail="Feedback not found for this interview")
    
    return {
        "interview_id": interview_id,
        "candidate_name": f"{interview.candidate.first_name} {interview.candidate.last_name}",
        "job_title": interview.job.title,
        "technical_score": float(feedback.technical_score),
        "communication_score": float(feedback.communication_score),
        "problem_solving_score": float(feedback.problem_solving_score),
        "cultural_fit_score": float(feedback.cultural_fit_score),
        "overall_score": float(feedback.overall_score),
        "strengths": feedback.strengths,
        "weaknesses": feedback.weaknesses,
        "recommendations": feedback.recommendations,
        "hire_recommendation": feedback.hire_recommendation,
        "additional_notes": feedback.additional_notes,
        "created_at": feedback.created_at
    }

@router.get("/interviews/history")
async def get_interview_history(
    skip: int = 0,
    limit: int = 50,
    current_user: User = Depends(AuthMiddleware.get_current_interviewer),
    db: Session = Depends(get_db)
):
    """Get interview history for the interviewer"""
    interviewer_profile = get_interviewer_profile(db, current_user)
    
    # Get interviews with feedback
    interviews = db.query(Interview).join(InterviewFeedback).filter(
        Interview.company_id == current_user.company_id,
        (Interview.interviewer_email == current_user.email) | (Interview.created_by == current_user.email),
        Interview.status == "completed"
    ).order_by(Interview.scheduled_datetime.desc()).offset(skip).limit(limit).all()
    
    result = []
    for interview in interviews:
        feedback = interview.feedback
        result.append({
            "interview_id": str(interview.id),
            "candidate_name": f"{interview.candidate.first_name} {interview.candidate.last_name}",
            "job_title": interview.job.title,
            "interview_type": interview.interview_type,
            "interview_date": interview.scheduled_datetime,
            "overall_score": float(feedback.overall_score),
            "hire_recommendation": feedback.hire_recommendation,
            "feedback_date": feedback.created_at
        })
    
    return {"interviews": result}

# ==================== INTERVIEWER PROFILE ENDPOINTS ====================

@router.get("/profile")
async def get_my_profile(
    current_user: User = Depends(AuthMiddleware.get_current_interviewer),
    db: Session = Depends(get_db)
):
    """Get interviewer profile information"""
    interviewer_profile = get_interviewer_profile(db, current_user)
    
    # Get interview statistics scoped to this tenant
    _base = db.query(Interview).filter(
        Interview.company_id == current_user.company_id,
        (Interview.interviewer_email == current_user.email) | (Interview.created_by == current_user.email)
    )
    total_interviews = _base.count()
    completed_interviews = _base.filter(Interview.status == "completed").count()
    upcoming_interviews = _base.filter(
        Interview.status == "scheduled",
        Interview.scheduled_datetime >= datetime.utcnow()
    ).count()
    
    return {
        "profile": {
            "employee_id": interviewer_profile.employee_id,
            "department": interviewer_profile.department.name if interviewer_profile.department else None,
            "expertise_areas": interviewer_profile.expertise_areas or [],
            "years_experience": interviewer_profile.years_experience,
            "created_at": interviewer_profile.created_at
        },
        "statistics": {
            "total_interviews": total_interviews,
            "completed_interviews": completed_interviews,
            "upcoming_interviews": upcoming_interviews
        },
        "user_info": {
            "name": f"{current_user.first_name} {current_user.last_name}",
            "email": current_user.email,
            "phone": current_user.phone
        }
    }
