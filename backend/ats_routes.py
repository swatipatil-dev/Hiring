"""
🔧 ATS SYSTEM API ROUTES
Add ATS system API routes to the existing recruitment system
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List, Optional
from pydantic import BaseModel, EmailStr
from datetime import datetime
import json

from database_config_mysql import get_db
from ats_models import ATSUser, ATSInterview, InterviewStatus, InterviewType, DifficultyLevel
from auth import get_password_hash, get_current_user

router = APIRouter(prefix="/api/v1/ats", tags=["ATS System"])

# Pydantic Models
class ATSUserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str
    full_name: Optional[str] = None
    is_active: bool = True

class ATSUserResponse(BaseModel):
    id: int
    username: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class ATSInterviewCreate(BaseModel):
    title: str
    description: Optional[str] = None
    job_position: str
    job_title: Optional[str] = None
    job_level: Optional[str] = None
    role_type: Optional[str] = None
    job_details_data: Optional[dict] = None
    interview_type: InterviewType
    difficulty_level: DifficultyLevel
    scheduled_datetime: datetime
    duration_minutes: int
    interview_config_data: Optional[dict] = None
    skills_config_data: Optional[dict] = None
    question_format_data: Optional[dict] = None
    evaluation_rules_data: Optional[dict] = None
    ai_generation_rules_data: Optional[dict] = None
    security_settings_data: Optional[dict] = None
    interviewer_name: str
    interviewer_email: Optional[str] = None
    meeting_link: Optional[str] = None
    interviewer_details_data: Optional[dict] = None
    notes: Optional[str] = None

class ATSInterviewResponse(BaseModel):
    id: int
    title: str
    description: Optional[str]
    job_position: str
    job_title: Optional[str]
    job_level: Optional[str]
    role_type: Optional[str]
    interview_type: InterviewType
    difficulty_level: DifficultyLevel
    scheduled_datetime: datetime
    duration_minutes: int
    interviewer_name: str
    interviewer_email: Optional[str]
    meeting_link: Optional[str]
    status: InterviewStatus
    notes: Optional[str]
    feedback: Optional[str]
    rating: Optional[int]
    created_by: str
    created_at: datetime
    updated_at: datetime
    
    class Config:
        from_attributes = True

# ATS User Routes
@router.post("/users", response_model=ATSUserResponse, status_code=status.HTTP_201_CREATED)
def create_ats_user(user: ATSUserCreate, db: Session = Depends(get_db)):
    """Create a new ATS system user"""
    
    # Check if username already exists
    existing_user = db.query(ATSUser).filter(ATSUser.username == user.username).first()
    if existing_user:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already exists"
        )
    
    # Check if email already exists
    existing_email = db.query(ATSUser).filter(ATSUser.email == user.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already exists"
        )
    
    # Create new user
    hashed_password = get_password_hash(user.password)
    db_user = ATSUser(
        username=user.username,
        email=user.email,
        hashed_password=hashed_password,
        full_name=user.full_name,
        is_active=user.is_active
    )
    
    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    
    return db_user

@router.get("/users", response_model=List[ATSUserResponse])
def get_ats_users(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Get all ATS system users"""
    users = db.query(ATSUser).offset(skip).limit(limit).all()
    return users

@router.get("/users/{user_id}", response_model=ATSUserResponse)
def get_ats_user(user_id: int, db: Session = Depends(get_db)):
    """Get ATS system user by ID"""
    user = db.query(ATSUser).filter(ATSUser.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    return user

# ATS Interview Routes
@router.post("/interviews", response_model=ATSInterviewResponse, status_code=status.HTTP_201_CREATED)
def create_ats_interview(interview: ATSInterviewCreate, db: Session = Depends(get_db)):
    """Create a new ATS interview"""
    
    # Create new interview
    db_interview = ATSInterview(
        title=interview.title,
        description=interview.description,
        job_position=interview.job_position,
        job_title=interview.job_title,
        job_level=interview.job_level,
        role_type=interview.role_type,
        job_details_data=json.dumps(interview.job_details_data) if interview.job_details_data else None,
        interview_type=interview.interview_type,
        difficulty_level=interview.difficulty_level,
        scheduled_datetime=interview.scheduled_datetime,
        duration_minutes=interview.duration_minutes,
        interview_config_data=json.dumps(interview.interview_config_data) if interview.interview_config_data else None,
        skills_config_data=json.dumps(interview.skills_config_data) if interview.skills_config_data else None,
        question_format_data=json.dumps(interview.question_format_data) if interview.question_format_data else None,
        evaluation_rules_data=json.dumps(interview.evaluation_rules_data) if interview.evaluation_rules_data else None,
        ai_generation_rules_data=json.dumps(interview.ai_generation_rules_data) if interview.ai_generation_rules_data else None,
        security_settings_data=json.dumps(interview.security_settings_data) if interview.security_settings_data else None,
        interviewer_name=interview.interviewer_name,
        interviewer_email=interview.interviewer_email,
        meeting_link=interview.meeting_link,
        interviewer_details_data=json.dumps(interview.interviewer_details_data) if interview.interviewer_details_data else None,
        notes=interview.notes,
        created_by="system"  # This should come from authentication
    )
    
    db.add(db_interview)
    db.commit()
    db.refresh(db_interview)
    
    return db_interview

@router.get("/interviews", response_model=List[ATSInterviewResponse])
def get_ats_interviews(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    """Get all ATS interviews"""
    interviews = db.query(ATSInterview).offset(skip).limit(limit).all()
    return interviews

@router.get("/interviews/{interview_id}", response_model=ATSInterviewResponse)
def get_ats_interview(interview_id: int, db: Session = Depends(get_db)):
    """Get ATS interview by ID"""
    interview = db.query(ATSInterview).filter(ATSInterview.id == interview_id).first()
    if not interview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Interview not found"
        )
    return interview

@router.put("/interviews/{interview_id}", response_model=ATSInterviewResponse)
def update_ats_interview(interview_id: int, interview_update: dict, db: Session = Depends(get_db)):
    """Update ATS interview"""
    interview = db.query(ATSInterview).filter(ATSInterview.id == interview_id).first()
    if not interview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Interview not found"
        )
    
    # Update fields
    for field, value in interview_update.items():
        if hasattr(interview, field) and field not in ['id', 'created_at', 'created_by']:
            if field.endswith('_data') and isinstance(value, dict):
                setattr(interview, field, json.dumps(value))
            else:
                setattr(interview, field, value)
    
    interview.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(interview)
    
    return interview

@router.delete("/interviews/{interview_id}")
def delete_ats_interview(interview_id: int, db: Session = Depends(get_db)):
    """Delete ATS interview"""
    interview = db.query(ATSInterview).filter(ATSInterview.id == interview_id).first()
    if not interview:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Interview not found"
        )
    
    db.delete(interview)
    db.commit()
    
    return {"message": "Interview deleted successfully"}

# ATS System Status
@router.get("/status")
def get_ats_status(db: Session = Depends(get_db)):
    """Get ATS system status"""
    try:
        user_count = db.query(ATSUser).count()
        interview_count = db.query(ATSInterview).count()
        
        return {
            "status": "active",
            "users_count": user_count,
            "interviews_count": interview_count,
            "database": "recruitment_db",
            "tables": ["ats_system", "ats_interviews", "ats_results"]
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting status: {str(e)}"
        )
