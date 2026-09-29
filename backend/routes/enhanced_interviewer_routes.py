"""
🎯 ENHANCED INTERVIEWER API ROUTES
Advanced interviewer features with AI question generation and comprehensive interview management
"""

from fastapi import APIRouter, HTTPException, status, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional, Dict, Any, Union
from src.config.database_config_mysql import get_db
from src.auth import get_current_user, AuthMiddleware, require_role
from src.models.models import Interview, User, Candidate, InterviewerProfile, JobPosting, Department, CandidateRequest, JDTemplate, HRProfile
from src.services.staff_directory import active_hr_query
from src.services.job_dedup import (
    find_open_duplicate_job, find_open_duplicate_request, duplicate_request_detail,
)
from src.models.question_schemas import (
    QuestionGenerationRequest, QuestionGenerationResponse, 
    QuestionListResponse as QuestionListResponseBase, Question, QuestionUpdate, QuestionCreate,
    QuestionEvaluationRequest, QuestionEvaluationResponse,
    FollowupQuestionRequest, FollowupQuestionResponse,
    InterviewQuestionGeneration, InterviewQuestionResponse,
    OllamaHealthCheck, QuestionGenerationStats
)
from src.prompt_builder import prompt_builder
from src.services.gemini_service import gemini_service
from pydantic import BaseModel, EmailStr, Field, ConfigDict, field_validator
from datetime import datetime, timedelta
from enum import Enum
import uuid
import json
import logging

# Roles that build and run tests. Superadmin is excluded: these routes act inside one
# company, and a superadmin has none.
_STAFF_ROLES = ["interviewer", "hr", "admin"]

# Shared with every other reader of these columns, so a Python-repr row parses
# the same way everywhere. See src/services/skills_parsing.py.
from src.services.skills_parsing import parse_skills as _parse_skills, dump_skills

# Enums for better type safety
class BloomTaxonomyLevel(str, Enum):
    REMEMBER = "Remember"
    UNDERSTAND = "Understand"
    APPLY = "Apply"
    ANALYZE = "Analyze"
    EVALUATE = "Evaluate"
    CREATE = "Create"

class JobLevel(str, Enum):
    FRESHER = "Fresher"
    JUNIOR = "Junior"
    MID_LEVEL = "Mid-level"
    SENIOR = "Senior"
    LEAD = "Lead"
    MANAGER = "Manager"

class RoleType(str, Enum):
    FULL_TIME = "Full-time"
    PART_TIME = "Part-time"
    CONTRACT = "Contract"
    INTERNSHIP = "Internship"

class InterviewType(str, Enum):
    TECHNICAL = "TECHNICAL"
    NON_TECHNICAL = "NON_TECHNICAL"
    MIXED = "MIXED"

class InterviewStatus(str, Enum):
    DRAFT = "DRAFT"
    GENERATING = "GENERATING"
    READY = "READY"
    ERROR = "ERROR"
    SCHEDULED = "SCHEDULED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    RESCHEDULED = "RESCHEDULED"

class QuestionType(str, Enum):
    MCQ = "mcq"
    CODING = "coding"
    DESCRIPTIVE = "descriptive"
    CASE_STUDY = "case_study"
    APTITUDE = "aptitude"

class QuestionStatus(str, Enum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    ARCHIVED = "archived"

# Pydantic models with proper configuration
class SkillQuestionType(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    type: str = "multiple_choice"
    count_per_set: int = 3
    pool_size: Optional[int] = None

class Skill(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    name: str
    difficulty: str = "MEDIUM"
    marks_per_question: int = 1
    question_types: List[SkillQuestionType] = []
    # Legacy fields (optional for backward compatibility)
    weight: Optional[int] = None
    question_count: Optional[int] = None
    difficulty_override: Optional[str] = None

def _ci_enum(enum_cls, value):
    """Case-insensitive enum value lookup."""
    if value is None:
        return value
    val_lower = str(value).lower()
    for member in enum_cls:
        if member.value.lower() == val_lower:
            return member.value
    return value  # pass through; Pydantic will raise the proper error

class JobDetails(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    job_position: str
    job_title: str
    job_level: JobLevel
    role_type: RoleType

    @field_validator('job_level', mode='before')
    @classmethod
    def normalize_job_level(cls, v):
        return _ci_enum(JobLevel, v)

    @field_validator('role_type', mode='before')
    @classmethod
    def normalize_role_type(cls, v):
        return _ci_enum(RoleType, v)

class InterviewConfiguration(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    interview_type: str
    difficulty_level: str
    duration_minutes: int
    scheduled_datetime: datetime
    # Minutes granted per question, keyed by question type ("multiple_choice", "coding", ...).
    # duration_minutes is computed from this map times the per-type question counts, and every
    # question's own time_limit_minutes is stamped from it — see generator_router._configured_time.
    # Must stay declared: interview_config_data is written with model_dump(), which drops
    # anything this model does not know about.
    time_per_question_type: Optional[Dict[str, int]] = None

    @field_validator('interview_type', mode='before')
    @classmethod
    def normalize_interview_type(cls, v):
        mapping = {
            'technical': 'TECHNICAL', 'non_technical': 'NON_TECHNICAL',
            'mixed': 'MIXED', 'behavioral': 'TECHNICAL', 'hr': 'NON_TECHNICAL'
        }
        return mapping.get(str(v).lower(), str(v).upper())

    @field_validator('difficulty_level', mode='before')
    @classmethod
    def normalize_difficulty(cls, v):
        return str(v).upper()

class SkillsConfiguration(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    skills: List[Skill] = []
    number_of_topics: int = 0
    topic_coverage_percent: int = 80

class QuestionFormat(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    total_questions: int = 0
    mcq: int = 0
    coding: int = 0
    descriptive: int = 0
    case_study: int = 0
    aptitude: int = 0
    communication: int = 0
    include_explanations: bool = False
    shuffle_questions: bool = False
    shuffle_options: bool = False

class EvaluationRules(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    total_marks: int
    negative_marking: bool = False
    negative_mark_percent: int = 0
    section_cutoff: int = 0
    overall_cutoff: int = 0

class AIGenerationRules(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    conceptual_vs_practical_ratio: str
    real_world_scenarios: bool = False
    bloom_taxonomy_level: BloomTaxonomyLevel = BloomTaxonomyLevel.REMEMBER
    coding_focus_areas: List[str] = []
    avoid_topics: List[str] = []

    @field_validator('bloom_taxonomy_level', mode='before')
    @classmethod
    def normalize_bloom(cls, v):
        return _ci_enum(BloomTaxonomyLevel, v)

class SecuritySettings(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    # Superseded by InterviewConfiguration.time_per_question_type; the wizards no longer send it.
    # Kept with a default so older payloads and stored rows still validate.
    time_per_question: int = 5
    enable_webcam_monitoring: bool = False
    # Tab-switch detection is ON by default; an interviewer must opt out deliberately.
    enable_tab_switch_detection: bool = True
    # Same reasoning: the exam runs fullscreen unless someone deliberately turns it off.
    require_fullscreen: bool = True
    copy_paste_allowed: bool = False

class InterviewerDetails(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    name: str
    email: EmailStr
    meeting_link: str

class InterviewCreate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    title: str
    description: str
    job_id: Optional[str] = None
    job_details: JobDetails
    interview_configuration: InterviewConfiguration
    skills_configuration: SkillsConfiguration
    question_format: QuestionFormat
    evaluation_rules: EvaluationRules
    ai_generation_rules: AIGenerationRules
    security_settings: SecuritySettings
    interviewer_details: InterviewerDetails
    notes: Optional[str] = None

def _coerce_status(raw) -> "InterviewStatus":
    """Map a stored status onto the enum without raising.

    The enum's values are uppercase but lowercase rows exist in older data, so a bare
    ``InterviewStatus(raw)`` raised ValueError and 500'd the whole response.
    """
    try:
        return InterviewStatus((raw or "").upper())
    except ValueError:
        return InterviewStatus.SCHEDULED


class InterviewUpdate(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    title: Optional[str] = None
    description: Optional[str] = None
    job_details: Optional[JobDetails] = None
    interview_configuration: Optional[InterviewConfiguration] = None
    skills_configuration: Optional[SkillsConfiguration] = None
    question_format: Optional[QuestionFormat] = None
    evaluation_rules: Optional[EvaluationRules] = None
    ai_generation_rules: Optional[AIGenerationRules] = None
    security_settings: Optional[SecuritySettings] = None
    interviewer_details: Optional[InterviewerDetails] = None
    job_id: Optional[str] = None
    notes: Optional[str] = None
    status: Optional[InterviewStatus] = None
    feedback: Optional[str] = None
    rating: Optional[int] = None
    num_sets: Optional[int] = None

class InterviewResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    id: int
    title: str = ""
    description: Optional[str] = None
    job_details: JobDetails
    interview_configuration: InterviewConfiguration
    skills_configuration: SkillsConfiguration
    question_format: QuestionFormat
    evaluation_rules: EvaluationRules
    ai_generation_rules: AIGenerationRules
    security_settings: SecuritySettings
    interviewer_details: InterviewerDetails
    notes: Optional[str] = None
    status: InterviewStatus
    feedback: Optional[str] = None
    rating: Optional[int] = None
    created_by: Optional[str] = ""
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    job_id: Optional[str] = None
    job_position: Optional[str] = None

class InterviewListResponse(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    interviews: List[InterviewResponse]
    total: int
    page: int
    size: int

router = APIRouter(prefix="/api/v1/interviewer", tags=["Enhanced Interviewer"])

# ==================== DEPARTMENT-BASED ASSIGNMENT MODELS ====================

class AssignedJobResponse(BaseModel):
    job_id: str
    job_title: str
    department: str
    department_id: Optional[str] = None
    required_skills: List[str]
    candidate_count: int
    shortlisted_count: int
    interviews_scheduled: int
    status: str
    created_at: datetime

class AssignedCandidateResponse(BaseModel):
    candidate_id: str
    name: str
    email: str
    phone: Optional[str] = None
    current_stage: str
    ats_score: Optional[float] = None
    applied_at: datetime
    has_interview_scheduled: bool
    interview_id: Optional[int] = None

class ScheduleInterviewRequest(BaseModel):
    candidate_id: str
    job_id: str
    title: str
    interview_type: str = "TECHNICAL"
    difficulty_level: str = "MEDIUM"
    scheduled_datetime: datetime
    duration_minutes: int = 60
    meeting_link: Optional[str] = None
    notes: Optional[str] = None

# ==================== AUTO-CREATE JOB FROM CANDIDATE REQUEST ====================

class AutoCreateJobRequest(BaseModel):
    title: str
    description: str
    requirements: str
    department: Optional[str] = None
    employment_type: str = "full-time"
    experience_years: int = 0
    required_skills: List[str] = []
    preferred_skills: Optional[List[str]] = None


@router.post("/auto-create-job")
async def auto_create_job_from_request(
    data: AutoCreateJobRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Auto-create a job posting when interviewer selects a candidate request to create a test"""
    from src.models.models import HRProfile, InterviewerProfile

    # Reuse an existing OPEN job for this role rather than creating a second one. This
    # endpoint is a get-or-create by design — the test wizard calls it to obtain a
    # job_id to hang a test on — so a match returns 200 with that job rather than 409.
    #
    # The previous query matched `title.ilike(...)` with no company and no status filter,
    # which meant it could hand back another TENANT's job, and that a role closed a year
    # ago permanently blocked reopening it. find_open_duplicate_job fixes both.
    #
    # Department is not part of the match here: the caller supplies a department name,
    # not an id, and the id is only resolved further down. Matching on title within the
    # company is what this endpoint has always done, minus the two defects.
    existing = find_open_duplicate_job(db, current_user.company_id, data.title)
    if existing:
        return {
            "job_id": str(existing.id),
            "job_title": existing.title,
            "reused": True,
            "message": f"Using the existing open job '{existing.title}' ({existing.status}) "
                       f"instead of creating a second one."
        }

    # Find interviewer's department
    interviewer_profile = db.query(InterviewerProfile).filter(
        InterviewerProfile.user_id == current_user.id
    ).first()

    dept_id = None
    if interviewer_profile and interviewer_profile.department_id:
        dept_id = interviewer_profile.department_id
    elif data.department:
        dept = db.query(Department).filter(
            Department.company_id == current_user.company_id,
            Department.name.ilike(f"%{data.department}%"),
        ).first()
        if dept:
            dept_id = dept.id

    # If no department found, use the company's first one
    if not dept_id:
        first_dept = db.query(Department).filter(
            Department.company_id == current_user.company_id
        ).first()
        if first_dept:
            dept_id = first_dept.id

    # Find an HR in the same department (or any HR in the SAME company).
    # The bare `.first()` previously would pick any HR from any tenant when
    # the department had no HR — a cross-tenant leak. Scope every fallback
    # path to the caller's company.
    # Active only on both paths: handing the job to a deactivated HR routes it to someone
    # who cannot log in to act on it.
    hr_profile = None
    if dept_id:
        hr_profile = active_hr_query(db, current_user.company_id).filter(
            HRProfile.department_id == dept_id,
        ).first()
    if not hr_profile:
        hr_profile = active_hr_query(db, current_user.company_id).first()

    job = JobPosting(
        company_id=current_user.company_id,
        title=data.title,
        description=data.description,
        requirements=data.requirements,
        department_id=dept_id,
        created_by_id=current_user.id,
        hr_id=hr_profile.id if hr_profile else None,
        interviewer_id=interviewer_profile.id if interviewer_profile else None,
        employment_type=data.employment_type,
        experience_years=data.experience_years,
        required_skills=json.dumps(data.required_skills) if data.required_skills else None,
        preferred_skills=dump_skills(data.preferred_skills),
        status="draft"
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    # Link any matching candidate request to this job
    matching_cr = db.query(CandidateRequest).filter(
        CandidateRequest.company_id == current_user.company_id,
        CandidateRequest.job_title.ilike(data.title.strip()),
        CandidateRequest.requested_by == current_user.id,
        CandidateRequest.job_id.is_(None)
    ).first()
    if matching_cr:
        matching_cr.job_id = str(job.id)
        if not matching_cr.company_id:
            matching_cr.company_id = current_user.company_id
        db.commit()

    return {
        "job_id": str(job.id),
        "job_title": job.title,
        "message": f"Job '{job.title}' created as draft — awaiting HR approval"
    }


# ==================== ALL JOBS FOR DROPDOWN ====================

@router.get("/all-jobs")
async def get_all_jobs_for_dropdown(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all job postings for the test creation dropdown (includes drafts awaiting HR approval)"""
    # Tenant isolation: scope to the caller's company. The before_compile
    # auto-filter would catch this too, but an explicit predicate keeps intent
    # obvious in code review and survives if the model ever loses the
    # TenantScoped marker.
    jobs = db.query(JobPosting).filter(
        JobPosting.company_id == current_user.company_id,
        JobPosting.status.in_(["active", "draft", "pending_approval"])
    ).order_by(JobPosting.created_at.desc()).all()

    # Check which ones already have a test (template, not candidate-specific)
    result = []
    for job in jobs:
        try:
            existing_test = db.query(Interview).filter(
                Interview.job_id == job.id,
                Interview.candidate_id.is_(None)
            ).first()
            skills = _parse_skills(job.required_skills)
            result.append({
                "job_id": str(job.id),
                "job_title": job.title,
                "department": job.department.name if job.department else "",
                "department_id": str(job.department_id) if job.department_id else None,
                "required_skills": skills,
                "status": job.status,
                "job_category": getattr(job, "job_category", None) or "technical",
                "has_test": existing_test is not None,
                "test_id": existing_test.id if existing_test else None,
                "test_status": existing_test.status if existing_test else None,
            })
        except Exception:
            # Skip jobs that fail to serialize rather than killing the whole response
            continue
    return result


# ==================== MY SCHEDULED MEETINGS ====================

@router.get("/my-meetings")
async def get_my_scheduled_meetings(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Get all interviews scheduled for this interviewer (by HR), with candidate + resume details"""
    # Email is unique per company, not globally, so the same address can exist in
    # another tenant: the company clause is what stops it matching their meetings.
    meetings = db.query(Interview).filter(
        Interview.company_id == current_user.company_id,
        Interview.status == "SCHEDULED",
        Interview.candidate_id.isnot(None),
        (Interview.interviewer_email == current_user.email) | (Interview.created_by == current_user.email)
    ).order_by(Interview.scheduled_datetime.asc()).all()

    result = []
    for iv in meetings:
        candidate_name = ""
        candidate_email = ""
        candidate_phone = ""
        resume_url = None
        ats_score = None
        candidate_skills = []
        if iv.candidate:
            candidate_name = f"{iv.candidate.first_name} {iv.candidate.last_name}"
            candidate_email = iv.candidate.email or ""
            candidate_phone = iv.candidate.phone or ""
            # Signed download link, never the storage key (src/services/file_access.py).
            from src.services.file_access import application_file_url
            resume_url = application_file_url(iv.candidate, "resume", current_user)
            ats_score = float(iv.candidate.ats_score) if iv.candidate.ats_score else None
            try:
                candidate_skills = json.loads(iv.candidate.extracted_skills) if iv.candidate.extracted_skills else []
            except:
                candidate_skills = []

        job_title = iv.job_title or (iv.job.title if iv.job else "")
        job_dept = iv.job.department.name if iv.job and iv.job.department else ""

        # Parse mode/location from notes
        mode = "online"
        location = ""
        if iv.notes:
            if "Mode: offline" in iv.notes:
                mode = "offline"
            for line in iv.notes.split("\n"):
                if line.startswith("Location:"):
                    location = line.replace("Location:", "").strip()

        result.append({
            "id": iv.id,
            "title": iv.title,
            "job_title": job_title,
            "job_id": iv.job_id,
            "department": job_dept,
            "candidate_id": iv.candidate_id,
            "candidate_name": candidate_name,
            "candidate_email": candidate_email,
            "candidate_phone": candidate_phone,
            "candidate_skills": candidate_skills,
            "ats_score": ats_score,
            "resume_url": resume_url,
            "interview_type": iv.interview_type,
            "difficulty_level": iv.difficulty_level,
            "scheduled_datetime": str(iv.scheduled_datetime) if iv.scheduled_datetime else None,
            "duration_minutes": iv.duration_minutes,
            "meeting_link": iv.meeting_link,
            "mode": mode,
            "location": location,
            "status": iv.status,
            "notes": iv.notes,
            "created_at": iv.created_at,
        })

    return result


# ==================== ASSIGNMENT ENDPOINTS ====================

@router.get("/my-assignments", response_model=List[AssignedJobResponse])
async def get_my_assigned_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(AuthMiddleware.get_current_interviewer)
):
    """Get all jobs assigned to this interviewer (auto-assigned by department match)"""
    profile = db.query(InterviewerProfile).filter(
        InterviewerProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=403, detail="Interviewer profile not found. Contact admin to set up your department profile.")

    jobs = db.query(JobPosting).filter(
        JobPosting.interviewer_id == profile.id
    ).all()

    result = []
    for job in jobs:
        candidate_count = db.query(Candidate).filter(Candidate.job_id == job.id).count()
        shortlisted_count = db.query(Candidate).filter(
            Candidate.job_id == job.id,
            Candidate.current_stage.in_(["shortlisted", "interview", "technical_interview"])
        ).count()
        interviews_scheduled = db.query(Interview).filter(
            Interview.job_id == job.id
        ).count()
        skills = _parse_skills(job.required_skills)
        result.append(AssignedJobResponse(
            job_id=str(job.id),
            job_title=job.title,
            department=job.department.name if job.department else "",
            department_id=str(job.department_id) if job.department_id else None,
            required_skills=skills,
            candidate_count=candidate_count,
            shortlisted_count=shortlisted_count,
            interviews_scheduled=interviews_scheduled,
            status=job.status,
            created_at=job.created_at
        ))
    return result


@router.get("/my-assignments/{job_id}/candidates", response_model=List[AssignedCandidateResponse])
async def get_candidates_for_assigned_job(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(AuthMiddleware.get_current_interviewer)
):
    """Get candidates for a job assigned to this interviewer"""
    profile = db.query(InterviewerProfile).filter(
        InterviewerProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=403, detail="Interviewer profile not found")

    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.interviewer_id == profile.id
    ).first()
    if not job:
        raise HTTPException(status_code=403, detail="Job not assigned to you or not found")

    # Show shortlisted and above candidates
    candidates = db.query(Candidate).filter(
        Candidate.job_id == job_id,
        Candidate.current_stage.in_(["shortlisted", "interview", "technical_interview", "screening", "hired"])
    ).all()

    result = []
    for c in candidates:
        existing = db.query(Interview).filter(
            Interview.job_id == job_id,
            Interview.candidate_id == c.id
        ).first()
        result.append(AssignedCandidateResponse(
            candidate_id=str(c.id),
            name=f"{c.first_name} {c.last_name}",
            email=c.email,
            phone=c.phone,
            current_stage=c.current_stage,
            ats_score=float(c.ats_score) if c.ats_score else None,
            applied_at=c.created_at,
            has_interview_scheduled=existing is not None,
            interview_id=existing.id if existing else None
        ))
    return result


@router.post("/schedule-interview")
async def schedule_interview_for_candidate(
    request: ScheduleInterviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(AuthMiddleware.get_current_interviewer)
):
    """Schedule a department-specific interview for a candidate, auto-linked to job and candidate"""
    profile = db.query(InterviewerProfile).filter(
        InterviewerProfile.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=403, detail="Interviewer profile not found")

    # Verify the job is assigned to this interviewer
    job = db.query(JobPosting).filter(
        JobPosting.id == request.job_id,
        JobPosting.company_id == current_user.company_id,
        JobPosting.interviewer_id == profile.id
    ).first()
    if not job:
        raise HTTPException(status_code=403, detail="Job not assigned to you")

    # Verify candidate belongs to this job
    candidate = db.query(Candidate).filter(
        Candidate.id == request.candidate_id,
        Candidate.company_id == current_user.company_id,
        Candidate.job_id == request.job_id
    ).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="Candidate not found for this job")

    # Check if an interview already exists for this job
    existing = db.query(Interview).filter(
        Interview.job_id == request.job_id
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"An interview already exists for this job (Interview ID: {existing.id}, Status: {existing.status}). Only one interview per job opening is allowed."
        )

    # Build skills config from job's required_skills
    job_skills = _parse_skills(job.required_skills)
    skills_config = [{"name": s, "weight": 1, "question_count": 2, "difficulty_override": request.difficulty_level} for s in job_skills]

    interview = Interview(
        company_id=job.company_id,
        title=request.title,
        description=f"Interview for {candidate.first_name} {candidate.last_name} - {job.title}",
        job_position=job.title,
        job_title=job.title,
        job_id=request.job_id,
        candidate_id=request.candidate_id,
        interview_type=request.interview_type,
        difficulty_level=request.difficulty_level,
        scheduled_datetime=request.scheduled_datetime,
        duration_minutes=request.duration_minutes,
        meeting_link=request.meeting_link,
        interviewer_name=f"{current_user.first_name} {current_user.last_name}",
        interviewer_email=current_user.email,
        skills_config_data=json.dumps({"skills": skills_config, "number_of_topics": len(job_skills)}),
        job_details_data=json.dumps({"job_position": job.title, "job_title": job.title, "role_type": "Full-time"}),
        notes=f"Mode: {'online' if request.meeting_link else 'offline'}\n{request.notes or ''}".strip(),
        is_scheduled_interview=True,
        status="SCHEDULED",
        created_by=current_user.email,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(interview)

    # Move candidate to interview stage
    candidate.current_stage = "interview"
    candidate.updated_at = datetime.utcnow()

    db.commit()
    db.refresh(interview)

    return {
        "id": interview.id,
        "message": f"Interview scheduled for {candidate.first_name} {candidate.last_name}",
        "candidate": f"{candidate.first_name} {candidate.last_name}",
        "candidate_email": candidate.email,
        "job": job.title,
        "department": job.department.name if job.department else "",
        "interviewer": current_user.email,
        "scheduled_at": request.scheduled_datetime,
        "skills_to_test": job_skills,
        "status": "SCHEDULED"
    }

# ==================== AI SERVICE ENDPOINTS ====================

# Staff only: each call spends Gemini quota, and the error text describes the backend.
@router.get("/ai/health", response_model=OllamaHealthCheck, dependencies=[Depends(require_role(["hr", "admin", "interviewer", "superadmin"]))])
async def check_ai_service():
    """Check AI service health"""
    try:
        is_healthy = await gemini_service.check_health()
        return OllamaHealthCheck(
            ollama_healthy=is_healthy,
            model=gemini_service.model,
            error=None if is_healthy else "AI service not available"
        )
    except Exception as e:
        return OllamaHealthCheck(
            ollama_healthy=False,
            model=gemini_service.model,
            error=str(e)
        )

@router.post("/ai/test-connection", dependencies=[Depends(require_role(["hr", "admin", "interviewer", "superadmin"]))])
async def test_ai_connection():
    """Test AI service connection"""
    try:
        is_healthy = await gemini_service.check_health()
        if is_healthy:
            # Test generation with a simple prompt
            test_prompt = "Generate one simple interview question about Python."
            questions = await gemini_service.generate_questions(test_prompt)
            return {
                "status": "success",
                "message": "AI service is working",
                "test_questions": questions[:3] if questions else []
            }
        else:
            return {
                "status": "error",
                "message": "AI service not available"
            }
    except Exception as e:
        return {
            "status": "error",
            "message": f"AI service error: {str(e)}"
        }

@router.get("/interviews/test-ollama", dependencies=[Depends(require_role(["hr", "admin", "interviewer", "superadmin"]))])
async def test_ollama():
    """Test Ollama AI service connection"""
    try:
        is_healthy = await gemini_service.check_health()
        if is_healthy:
            # Test generation with a simple prompt
            test_prompt = "Generate one simple interview question about Python."
            questions = await gemini_service.generate_questions(test_prompt)
            return {
                "status": "success",
                "ollama_healthy": True,
                "model": gemini_service.model,
                "test_questions": questions[:3] if questions else [],
                "message": "Ollama AI service is working correctly"
            }
        else:
            return {
                "status": "error",
                "ollama_healthy": False,
                "model": gemini_service.model,
                "error": "AI service not available"
            }
    except Exception as e:
        return {
            "status": "error",
            "ollama_healthy": False,
            "model": gemini_service.model,
            "error": str(e)
        }

# ==================== HELPERS ====================

def _build_interview_response(interview) -> "InterviewResponse":
    """Build InterviewResponse from DB Interview model, restoring JSON fields"""
    job_details = JobDetails(
        **json.loads(interview.job_details_data)
    ) if interview.job_details_data else JobDetails(
        job_position=interview.job_position or "Software Engineer",
        job_title=interview.job_title or "Developer",
        job_level=JobLevel.MID_LEVEL,
        role_type=RoleType.FULL_TIME
    )

    interview_config_raw = json.loads(interview.interview_config_data) if interview.interview_config_data else {}
    interview_config = InterviewConfiguration(
        interview_type=interview_config_raw.get("interview_type", interview.interview_type or "technical"),
        difficulty_level=interview_config_raw.get("difficulty_level", interview.difficulty_level or "Medium"),
        duration_minutes=interview_config_raw.get("duration_minutes", interview.duration_minutes or 60),
        scheduled_datetime=interview_config_raw.get("scheduled_datetime", str(interview.scheduled_datetime or interview.scheduled_datetime or datetime.now())),
        # Without this the map is dropped on read, so editing a test would silently reset it.
        time_per_question_type=interview_config_raw.get("time_per_question_type")
    ) if interview_config_raw else InterviewConfiguration(
        interview_type=interview.interview_type or "technical",
        difficulty_level=interview.difficulty_level or "Medium",
        duration_minutes=interview.duration_minutes or 60,
        scheduled_datetime=interview.scheduled_datetime or interview.scheduled_datetime or datetime.now()
    )

    skills_config = SkillsConfiguration(
        **json.loads(interview.skills_config_data)
    ) if interview.skills_config_data else SkillsConfiguration(skills=[], number_of_topics=5, topic_coverage_percent=80)

    question_format = QuestionFormat(
        **json.loads(interview.question_format_data)
    ) if interview.question_format_data else QuestionFormat()

    eval_rules = EvaluationRules(
        **json.loads(interview.evaluation_rules_data)
    ) if interview.evaluation_rules_data else EvaluationRules(total_marks=100, negative_marking=False, section_cutoff=30, overall_cutoff=50)

    ai_rules = AIGenerationRules(
        **json.loads(interview.ai_generation_rules_data)
    ) if interview.ai_generation_rules_data else AIGenerationRules(
        conceptual_vs_practical_ratio="80:20", real_world_scenarios=True,
        bloom_taxonomy_level=BloomTaxonomyLevel.APPLY, coding_focus_areas=[], avoid_topics=[]
    )

    security = SecuritySettings(
        **json.loads(interview.security_settings_data)
    ) if interview.security_settings_data else SecuritySettings(
        time_per_question=5, enable_webcam_monitoring=False,
        enable_tab_switch_detection=True, require_fullscreen=True,
        copy_paste_allowed=False
    )

    # meeting_link is a required str on InterviewerDetails, but it is legitimately NULL on
    # every offline interview and every draft — passing None raised a pydantic
    # ValidationError that surfaced as a 500, and because this builder also serves the
    # interviews LIST, a single link-less row broke the whole page. Coerce on both paths.
    if interview.interviewer_details_data:
        _details = json.loads(interview.interviewer_details_data)
        _details["meeting_link"] = _details.get("meeting_link") or ""
        interviewer_details = InterviewerDetails(**_details)
    else:
        interviewer_details = InterviewerDetails(
            name=interview.interviewer_name or "Interviewer",
            email=interview.interviewer_email or "interviewer@example.com",
            meeting_link=interview.meeting_link or "",
        )

    feedback_val = None
    if hasattr(interview, '_feedback_text'):
        feedback_val = interview._feedback_text
    elif isinstance(getattr(interview, 'feedback', None), str):
        feedback_val = interview.feedback

    return InterviewResponse(
        id=interview.id,
        title=interview.title or "",
        description=interview.description or "",
        job_details=job_details,
        interview_configuration=interview_config,
        skills_configuration=skills_config,
        question_format=question_format,
        evaluation_rules=eval_rules,
        ai_generation_rules=ai_rules,
        security_settings=security,
        interviewer_details=interviewer_details,
        notes=interview.notes,
        status=_coerce_status(interview.status),
        feedback=feedback_val,
        rating=interview.rating,
        created_by=interview.created_by or "",
        created_at=interview.created_at,
        updated_at=interview.updated_at,
        job_id=interview.job_id,
        job_position=interview.job_position
    )


# ==================== INTERVIEW MANAGEMENT ENDPOINTS ====================

@router.post("/interviews", response_model=InterviewResponse)
async def create_interview(
    interview_data: InterviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Create a new interview with comprehensive configuration"""
    try:
        # Only one interview per job allowed
        if interview_data.job_id:
            if not db.query(JobPosting.id).filter(
                JobPosting.id == interview_data.job_id,
                JobPosting.company_id == current_user.company_id,
            ).first():
                raise HTTPException(status_code=404, detail="Job not found")
            existing = db.query(Interview).filter(
                Interview.company_id == current_user.company_id,
                Interview.job_id == interview_data.job_id,
                Interview.candidate_id.is_(None)
            ).first()
            if existing:
                raise HTTPException(
                    status_code=409,
                    detail=f"An interview already exists for this job (Interview ID: {existing.id}, Status: {existing.status}). You can edit the existing test instead of creating a new one."
                )

        # Create new interview record (id is auto-increment)
        interview = Interview(
            company_id=current_user.company_id,
            title=interview_data.title,
            description=interview_data.description,
            job_id=interview_data.job_id,
            # Job details
            job_position=interview_data.job_details.job_position,
            job_title=interview_data.job_details.job_title,
            job_level=interview_data.job_details.job_level,
            role_type=interview_data.job_details.role_type,
            job_details_data=json.dumps(interview_data.job_details.model_dump(), default=str),
            # Interview config
            interview_type=interview_data.interview_configuration.interview_type,
            difficulty_level=interview_data.interview_configuration.difficulty_level,
            
            scheduled_datetime=interview_data.interview_configuration.scheduled_datetime,
            duration_minutes=interview_data.interview_configuration.duration_minutes,
            interview_config_data=json.dumps(interview_data.interview_configuration.model_dump(), default=str),
            # Complex configurations
            skills_config_data=json.dumps(interview_data.skills_configuration.model_dump(), default=str),
            question_format_data=json.dumps(interview_data.question_format.model_dump(), default=str),
            evaluation_rules_data=json.dumps(interview_data.evaluation_rules.model_dump(), default=str),
            ai_generation_rules_data=json.dumps(interview_data.ai_generation_rules.model_dump(), default=str),
            security_settings_data=json.dumps(interview_data.security_settings.model_dump(), default=str),
            # Interviewer details
            interviewer_name=interview_data.interviewer_details.name,
            interviewer_email=interview_data.interviewer_details.email,
            meeting_link=interview_data.interviewer_details.meeting_link,
            interviewer_details_data=json.dumps(interview_data.interviewer_details.model_dump(), default=str),
            # Other
            notes=interview_data.notes,
            status="DRAFT",
            created_by=current_user.email,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )

        db.add(interview)
        db.commit()
        db.refresh(interview)

        return _build_interview_response(interview)
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to create interview: {str(e)}"
        )

class DraftInterviewCreate(BaseModel):
    """Minimal payload for creating a DRAFT interview (used during per-skill question generation)"""
    title: str = ""
    job_id: Optional[str] = None
    job_position: Optional[str] = None
    job_title: Optional[str] = None
    interview_type: Optional[str] = "TECHNICAL"
    difficulty_level: Optional[str] = "MEDIUM"

    @field_validator('job_id', 'job_position', 'job_title', mode='before')
    @classmethod
    def empty_str_to_none(cls, v):
        if v == '' or v == 'null':
            return None
        return v


@router.post("/interviews/draft")
async def create_draft_interview(
    data: DraftInterviewCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Create a minimal DRAFT interview for per-skill question generation.
    The full config is applied later via PUT before assembling test sets."""
    try:
        # Defensive: ignore a job_id that isn't a real JobPosting (e.g. the wizard
        # passed a candidate-request id). Persisting it would violate the
        # interviews.job_id foreign key; the real job is attached later via PUT.
        if data.job_id:
            job_exists = db.query(JobPosting.id).filter(
                JobPosting.id == data.job_id,
                JobPosting.company_id == current_user.company_id,
            ).first()
            if not job_exists:
                data.job_id = None

        # Get-or-create per job: the wizard fires this once per skill, so repeated
        # calls for the same job must reuse the same DRAFT instead of piling up rows.
        if data.job_id:
            existing = db.query(Interview).filter(
                Interview.company_id == current_user.company_id,
                Interview.job_id == data.job_id,
                Interview.candidate_id.is_(None)
            ).first()
            if existing:
                if existing.status == "DRAFT":
                    # Idempotent: hand back the existing open draft for this job.
                    return {"id": existing.id, "status": existing.status, "title": existing.title}
                # A non-draft (e.g. READY/published) interview already owns this job.
                raise HTTPException(
                    status_code=409,
                    detail=f"An interview already exists for this job (Interview ID: {existing.id}, Status: {existing.status})."
                )

        interview = Interview(
            company_id=current_user.company_id,
            title=data.title or "Draft Test",
            job_id=data.job_id if data.job_id else None,
            job_position=data.job_position or None,
            job_title=data.job_title or None,
            interview_type=data.interview_type or "TECHNICAL",
            difficulty_level=data.difficulty_level or "MEDIUM",
            status="DRAFT",
            created_by=current_user.email,
            created_at=datetime.now(),
            updated_at=datetime.now()
        )
        db.add(interview)
        db.commit()
        db.refresh(interview)

        return {"id": interview.id, "status": interview.status, "title": interview.title}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to create draft: {str(e)}")


class PublishTestRequest(BaseModel):
    hr_id: str = Field(..., description="HR profile ID to assign for this test/job")


@router.post("/interviews/{interview_id}/publish")
async def publish_interview(
    interview_id: int,
    body: PublishTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """
    Publish a test/interview with HR assignment — validates:
    1. Interview exists and is in DRAFT/GENERATING status
    2. Interview is linked to a job
    3. Selected HR exists
    4. At least 1 approved question exists
    Then assigns HR to job + interview, sets status to READY,
    and creates a notification for the HR.
    """
    from src.models.question_models import Question as InterviewQuestion
    from src.models.models import HRProfile, Notification

    # Interview ids are sequential integers, so both lookups are company-scoped:
    # otherwise any user could publish another company's draft and reassign its job.
    interview = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == current_user.company_id,
    ).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if interview.status not in ("DRAFT", "GENERATING"):
        raise HTTPException(
            status_code=400,
            detail=f"Only DRAFT interviews can be published. Current status: {interview.status}"
        )

    # Check job link
    if not interview.job_id:
        raise HTTPException(
            status_code=400,
            detail="Interview must be linked to a job before publishing. Set job_id first."
        )

    job = db.query(JobPosting).filter(
        JobPosting.id == interview.job_id,
        JobPosting.company_id == current_user.company_id,
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Linked job not found")

    # Validate selected HR
    hr_profile = active_hr_query(db, current_user.company_id).filter(
        HRProfile.id == body.hr_id
    ).first()
    if not hr_profile:
        if db.query(HRProfile).filter(
            HRProfile.id == body.hr_id,
            HRProfile.company_id == current_user.company_id,
        ).first():
            raise HTTPException(
                status_code=400,
                detail="That HR user has been deactivated - pick someone else."
            )
        raise HTTPException(status_code=404, detail="Selected HR profile not found")

    # Check approved questions
    approved_count = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id,
        InterviewQuestion.is_approved == True
    ).count()

    total_count = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id
    ).count()

    if total_count == 0:
        raise HTTPException(
            status_code=400,
            detail="No questions found for this interview. Generate questions first."
        )

    if approved_count == 0:
        raise HTTPException(
            status_code=400,
            detail=f"No approved questions. {total_count} questions exist but none are approved. Approve at least 1 question before publishing."
        )

    # All validations passed — assign HR and publish
    hr_user = hr_profile.user
    job.hr_id = hr_profile.id
    job.updated_at = datetime.now()
    # If the chosen HR was a co-HR on this job, they are now its owner — never both.
    from src.services.job_access import remove_collaborator_if_owner, job_hr_user_ids
    remove_collaborator_if_owner(db, job)

    existing_request = db.query(CandidateRequest).filter(
        CandidateRequest.company_id == current_user.company_id,
        CandidateRequest.job_id == str(job.id),
        CandidateRequest.requested_by == current_user.id,
    ).first()
    if not existing_request:
        existing_request = db.query(CandidateRequest).filter(
            CandidateRequest.company_id == current_user.company_id,
            CandidateRequest.job_id.is_(None),
            CandidateRequest.requested_by == current_user.id,
            CandidateRequest.job_title.ilike(job.title.strip()),
        ).first()

    if existing_request:
        existing_request.job_id = str(job.id)
        if not existing_request.company_id:
            existing_request.company_id = job.company_id or current_user.company_id
        existing_request.updated_at = datetime.now()
    else:
        existing_request = CandidateRequest(
            company_id=job.company_id or current_user.company_id,
            requested_by=current_user.id,
            job_id=str(job.id),
            job_title=job.title,
            department=job.department.name if job.department else None,
            role_level=interview.job_level,
            employment_type=job.employment_type,
            num_candidates=1,
            required_skills=json.dumps(_parse_skills(job.required_skills)),
            preferred_skills=json.dumps(_parse_skills(job.preferred_skills)),
            min_experience_years=job.experience_years or 0,
            job_description=job.description,
            job_category=job.job_category or "technical",
            urgency="normal",
            notes=interview.notes,
            status="pending",
        )
        db.add(existing_request)

    db.flush()

    interview.assigned_hr_id = hr_profile.id
    interview.status = "READY"
    interview.published_at = datetime.now()
    interview.updated_at = datetime.now()

    # Create notification for the assigned HR — test published
    notification = Notification(
        company_id=job.company_id or current_user.company_id,
        user_id=hr_user.id,
        title="New Test Published — Action Required",
        message=f'Test "{interview.title}" for job "{job.title}" has been published by {current_user.first_name} {current_user.last_name}. Please review candidates and send test invitations.',
        type="test_published",
        reference_type="interview",
        reference_id=str(interview.id)
    )
    db.add(notification)

    # Co-HRs on the job get the same "test published" alert (the owner already has one).
    for co_user_id in job_hr_user_ids(db, job):
        if co_user_id != hr_user.id:
            db.add(Notification(
                company_id=job.company_id or current_user.company_id,
                user_id=co_user_id,
                title=notification.title,
                message=notification.message,
                type="test_published",
                reference_type="interview",
                reference_id=str(interview.id),
            ))

    # Also notify about the job requirement so it appears in My Job Requirements
    job_notif = Notification(
        company_id=job.company_id or current_user.company_id,
        user_id=hr_user.id,
        title="New Job Requirement Assigned",
        message=f'Job "{job.title}" has been assigned to you by {current_user.first_name} {current_user.last_name}. Please review the candidate requirements and approve the job posting.',
        type="candidate_request_new",
        reference_type="candidate_request",
        reference_id=str(existing_request.id)
    )
    db.add(job_notif)

    # Now that a real test is finalized for this job, remove leftover EMPTY draft
    # interviews for the same job so they don't linger or show up as assignable.
    # Protect any draft that has real work (questions / set assignments).
    try:
        from src.models.question_models import Question as _IQ, QuestionSetAssignment as _QSA
        sibling_drafts = db.query(Interview).filter(
            Interview.company_id == current_user.company_id,
            Interview.job_id == job.id,
            Interview.candidate_id.is_(None),
            Interview.status == "DRAFT",
            Interview.id != interview.id,
        ).all()
        for d in sibling_drafts:
            has_q = db.query(_IQ.id).filter(_IQ.interview_id == d.id).first()
            has_s = db.query(_QSA.id).filter(_QSA.interview_id == d.id).first()
            if not has_q and not has_s:
                db.delete(d)
    except Exception as e:
        logging.getLogger(__name__).warning("Sibling-draft cleanup skipped: %s", e)

    db.commit()
    db.refresh(interview)

    return {
        "message": "Test published and HR notified successfully",
        "interview_id": interview.id,
        "title": interview.title,
        "status": "READY",
        "job_id": str(job.id),
        "job_title": job.title,
        "assigned_hr": f"{hr_user.first_name} {hr_user.last_name}",
        "hr_email": hr_user.email,
        "approved_questions": approved_count,
        "total_questions": total_count
    }


@router.get("/hr-list")
async def get_hr_list_for_assignment(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get HR profiles for the select-HR dropdown — scoped to the caller's company.

    Tenant isolation: the explicit join on User.company_id keeps the intent
    visible in code review.  The before_compile auto-filter
    (src/tenancy/events.py) also injects HRProfile.company_id == <bound>
    because HRProfile is TenantScoped — belt and braces.
    """
    profiles = active_hr_query(db, current_user.company_id).filter(
        User.company_id == current_user.company_id
    ).all()
    return [
        {
            "id": str(p.id),
            "name": f"{p.user.first_name} {p.user.last_name}",
            "email": p.user.email,
            "department": p.department.name if p.department else "—",
            "employee_id": p.employee_id
        }
        for p in profiles if p.user
    ]


@router.get("/interviews")
async def get_interviews(
    page: int = Query(1, ge=1),
    size: int = Query(10, ge=1, le=100),
    status_filter: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Get all interviews with pagination and filtering"""
    try:
        # Build query — the caller's company only (interview ids are sequential, and
        # this list used to return every tenant's tests to any logged-in user).
        query = _company_scope(db.query(Interview), Interview, current_user)
        
        if status_filter:
            # Stored uppercase; normalise so a lowercase filter is not silently empty.
            query = query.filter(func.upper(Interview.status) == status_filter.upper())
        
        # Get total count
        total = query.count()
        
        # Apply pagination
        interviews = query.offset((page - 1) * size).limit(size).all()
        
        # Convert to response format (skip any that fail serialization)
        interview_responses = []
        for interview in interviews:
            try:
                interview_responses.append(_build_interview_response(interview))
            except Exception as build_err:
                logging.warning(f"Skipping interview {interview.id}: {build_err}")
                # Return a minimal response so it still appears in the list
                interview_responses.append({
                    "id": interview.id,
                    "title": interview.title or "",
                    "description": interview.description or "",
                    "job_details": {"job_position": interview.job_position or "", "job_title": interview.job_title or "", "job_level": "Mid-level", "role_type": "Full-time"},
                    "interview_configuration": {"interview_type": interview.interview_type or "TECHNICAL", "difficulty_level": interview.difficulty_level or "MEDIUM", "duration_minutes": interview.duration_minutes or 60, "scheduled_datetime": str(interview.scheduled_datetime or datetime.now())},
                    "skills_configuration": {"skills": [], "number_of_topics": 0, "topic_coverage_percent": 80},
                    "question_format": {"total_questions": 10, "mcq": 3, "coding": 2, "descriptive": 3, "case_study": 2, "aptitude": 0},
                    "evaluation_rules": {"total_marks": 100, "negative_marking": False, "section_cutoff": 30, "overall_cutoff": 50},
                    "ai_generation_rules": {"conceptual_vs_practical_ratio": "60:40", "real_world_scenarios": True, "bloom_taxonomy_level": "Apply", "coding_focus_areas": [], "avoid_topics": []},
                    "security_settings": {"time_per_question": 5, "enable_webcam_monitoring": False, "enable_tab_switch_detection": True, "require_fullscreen": True, "copy_paste_allowed": False},
                    "interviewer_details": {"name": interview.interviewer_name or "", "email": interview.interviewer_email or "", "meeting_link": ""},
                    "notes": interview.notes,
                    "status": interview.status or "DRAFT",
                    "created_by": interview.created_by or "",
                    "created_at": interview.created_at,
                    "updated_at": interview.updated_at,
                    "job_id": interview.job_id,
                    "job_position": interview.job_position
                })
        
        return {
            "interviews": interview_responses,
            "total": total,
            "page": page,
            "size": size
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get interviews: {str(e)}"
        )

@router.get("/interviews/{interview_id}", response_model=InterviewResponse)
async def get_interview(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Get a specific interview by ID"""
    try:
        interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id), Interview, current_user
        ).first()
        
        if not interview:
            raise HTTPException(
                status_code=404,
                detail="Interview not found"
            )
        
        return _build_interview_response(interview)
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get interview: {str(e)}"
        )

# Config blobs that decide how a test is run and marked. Editable while a test is still a
# template; never on a live row, because "security_settings" is the proctoring switchboard
# (webcam, fullscreen, tab-switch) and "evaluation_rules" holds the cutoffs.
_TEMPLATE_ONLY_FIELDS = (
    "job_details", "interview_configuration", "skills_configuration", "question_format",
    "evaluation_rules", "ai_generation_rules", "security_settings", "interviewer_details",
    "job_id", "num_sets", "title", "description",
)

# What may still change once an interview is a scheduled, real-world meeting.
_SCHEDULED_EDITABLE_FIELDS = ("status", "notes", "rating")


def _may_edit_scheduled(interview, current_user) -> bool:
    """HR/admin, the creator, or someone on the panel."""
    if current_user.role in ("hr", "admin", "superadmin"):
        return True
    email = (current_user.email or "").strip().lower()
    if (interview.created_by or "").strip().lower() == email:
        return True
    if (interview.interviewer_email or "").strip().lower() == email:
        return True
    try:
        panel = json.loads(interview.panel_members) if interview.panel_members else []
    except (ValueError, TypeError):
        panel = []
    return any((m.get("email") or "").strip().lower() == email for m in panel)


@router.put("/interviews/{interview_id}", response_model=InterviewResponse)
async def update_interview(
    interview_id: int,
    interview_update: InterviewUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["interviewer", "hr", "admin", "superadmin"]))
):
    """Update an existing interview.

    Same three guards as delete_interview below, which documents why each is load-bearing.
    This endpoint had none of them: it took only ``get_current_user`` and looked the row up
    by id alone.

    * Role. Any authenticated user - a CANDIDATE included - could rewrite any interview.
      The sharp edge was ``security_settings_data``: a candidate could disable webcam
      monitoring, tab-switch detection and fullscreen enforcement on their own in-flight
      attempt, then sit the exam unproctored. ``evaluation_rules_data`` (cutoffs, total
      marks) was equally open.
    * Company, explicitly via _company_scope. Interview is TenantScoped but the auto-filter
      does NOT reach this handler - the same reason delete_interview scopes by hand - and
      interview ids are sequential integers, so enumeration was trivial.
    * State. What may be edited depends on what the row IS. The three callers are not
      equivalent: the create-test wizard (both the interviewer copy and the shared one HR
      uses) saves whole configs onto DRAFT templates, while submitMeetingFeedback flips a
      scheduled meeting to COMPLETED. Gating on templates alone would 404 the latter and
      silently break "submit feedback -> mark completed", so the two are split rather than
      locked to one shape.
    """
    try:
        interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id),
            Interview,
            current_user,
        ).first()

        if not interview:
            raise HTTPException(
                status_code=404,
                detail="Interview not found"
            )

        update_data = interview_update.model_dump(exclude_unset=True)

        is_template = interview.candidate_id is None and not interview.is_scheduled_interview
        if not is_template:
            if not interview.is_scheduled_interview:
                # A candidate's test attempt, with their answers and proctoring settings.
                # Not editable here by anyone; it needs a deliberate flow, not this endpoint.
                raise HTTPException(
                    status_code=409,
                    detail="This is a candidate's test attempt and cannot be edited here.",
                )
            if not _may_edit_scheduled(interview, current_user):
                raise HTTPException(
                    status_code=403,
                    detail="Only HR, the organiser, or a panel member can edit this interview.",
                )
            blocked = [f for f in _TEMPLATE_ONLY_FIELDS if update_data.get(f) is not None]
            if blocked:
                raise HTTPException(
                    status_code=400,
                    detail=(
                        "A scheduled interview only accepts "
                        f"{', '.join(_SCHEDULED_EDITABLE_FIELDS)}; refused: {', '.join(blocked)}."
                    ),
                )

        if "title" in update_data:
            interview.title = update_data["title"]
        if "description" in update_data:
            interview.description = update_data["description"]
        if "job_id" in update_data:
            new_job_id = update_data["job_id"] or None
            if new_job_id and not db.query(JobPosting.id).filter(
                JobPosting.id == new_job_id,
                JobPosting.company_id == interview.company_id,
            ).first():
                raise HTTPException(status_code=404, detail="Job not found")
            interview.job_id = new_job_id
        if "notes" in update_data:
            interview.notes = update_data["notes"]
        if "status" in update_data:
            interview.status = update_data["status"]
        if "rating" in update_data:
            interview.rating = update_data["rating"]

        # Update JSON-stored complex fields
        if interview_update.job_details:
            interview.job_position = interview_update.job_details.job_position
            interview.job_title = interview_update.job_details.job_title
            interview.job_level = interview_update.job_details.job_level
            interview.role_type = interview_update.job_details.role_type
            interview.job_details_data = json.dumps(interview_update.job_details.model_dump(), default=str)

        if interview_update.interview_configuration:
            cfg = interview_update.interview_configuration
            interview.interview_type = cfg.interview_type
            interview.difficulty_level = cfg.difficulty_level
            interview.duration_minutes = cfg.duration_minutes
            interview.scheduled_datetime = cfg.scheduled_datetime
            interview.interview_config_data = json.dumps(cfg.model_dump(), default=str)

        if interview_update.skills_configuration:
            interview.skills_config_data = json.dumps(interview_update.skills_configuration.model_dump(), default=str)
        if interview_update.question_format:
            interview.question_format_data = json.dumps(interview_update.question_format.model_dump(), default=str)
        if interview_update.evaluation_rules:
            interview.evaluation_rules_data = json.dumps(interview_update.evaluation_rules.model_dump(), default=str)
        if interview_update.ai_generation_rules:
            interview.ai_generation_rules_data = json.dumps(interview_update.ai_generation_rules.model_dump(), default=str)
        if interview_update.security_settings:
            interview.security_settings_data = json.dumps(interview_update.security_settings.model_dump(), default=str)
        if interview_update.interviewer_details:
            interview.interviewer_name = interview_update.interviewer_details.name
            interview.interviewer_email = interview_update.interviewer_details.email
            interview.meeting_link = interview_update.interviewer_details.meeting_link
            interview.interviewer_details_data = json.dumps(interview_update.interviewer_details.model_dump(), default=str)

        if interview_update.num_sets is not None:
            interview.num_sets = interview_update.num_sets

        interview.updated_at = datetime.now()
        db.commit()
        db.refresh(interview)

        return _build_interview_response(interview)

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update interview: {str(e)}"
        )

@router.delete("/interviews/{interview_id}")
async def delete_interview(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(["interviewer", "hr", "admin", "superadmin"]))
):
    """Delete an unfinished DRAFT test.

    Three guards, all load-bearing. This endpoint used to have none of them: it took only
    get_current_user and looked the interview up by id alone.

    * Role. Any authenticated user — a CANDIDATE included — could delete any interview,
      template or live attempt with its answers.
    * Company, explicitly via _company_scope. Interview is TenantScoped, but the auto-filter
      does NOT reach this handler: against a bare `Interview.id ==` query, Company B's HR
      successfully deleted Company A's draft (see test_delete_blocks_cross_tenant). That is
      the same reason every other tenant-safe route in this file filters by hand.
    * DRAFT templates only. A published test and a candidate's in-flight attempt are not
      one-click deletable; those need a deliberate flow, not this endpoint.
    """
    try:
        interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id),
            Interview,
            current_user,
        ).first()

        if not interview:
            raise HTTPException(
                status_code=404,
                detail="Interview not found"
            )

        if interview.candidate_id is not None:
            raise HTTPException(
                status_code=409,
                detail="This is a candidate's test attempt, not a draft. It cannot be deleted here."
            )
        if (interview.status or "").upper() != "DRAFT":
            raise HTTPException(
                status_code=409,
                detail=f"Only DRAFT tests can be deleted (this one is {interview.status})."
            )

        # Children are deleted explicitly, not by cascade. Neither backref declares one
        # (question_models.py:70, :85), so SQLAlchemy's default is to NULL the child FK on
        # parent delete — which orphans interview_questions into the interview_id IS NULL
        # state that has no derivable tenant owner, and outright fails on
        # question_set_assignments, whose interview_id is NOT NULL.
        from src.models.question_models import (
            Question as _DelQ,
            QuestionSetAssignment as _DelQSA,
            CandidateQuestionHistory as _DelCQH,
        )
        db.query(_DelQSA).filter(_DelQSA.interview_id == interview_id).delete(synchronize_session=False)
        db.query(_DelCQH).filter(_DelCQH.interview_id == interview_id).delete(synchronize_session=False)
        db.query(_DelQ).filter(_DelQ.interview_id == interview_id).delete(synchronize_session=False)

        db.delete(interview)
        db.commit()

        return {"message": "Draft deleted successfully"}

    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete interview: {str(e)}"
        )

# ==================== QUESTION GENERATION ENDPOINTS ====================

@router.post("/interviews/{interview_id}/generate-questions", response_model=InterviewQuestionResponse)
async def generate_interview_questions(
    interview_id: int,
    request: InterviewQuestionGeneration,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Generate AI questions for an interview"""
    
    # Get interview details
    interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id), Interview, current_user
        ).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    
    # Check if interviewer has access to this interview
    if interview.created_by != current_user.email:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Delete existing questions if regenerate_all is True
    if request.regenerate_all:
        # For now, we'll skip question deletion as we're not using InterviewQuestion model
        pass
    
    try:
        # Check AI service health
        is_healthy = await gemini_service.check_health()
        if not is_healthy:
            raise HTTPException(status_code=503, detail="AI service not available")
        
        # Auto-resolve skills from linked job if focus_areas not provided
        focus_areas = request.focus_areas or []
        if not focus_areas and interview.job_id:
            linked_job = db.query(JobPosting).filter(
                JobPosting.id == interview.job_id,
                JobPosting.company_id == interview.company_id,
            ).first()
            if linked_job and linked_job.required_skills:
                focus_areas = json.loads(linked_job.required_skills) if isinstance(linked_job.required_skills, str) else linked_job.required_skills
        if not focus_areas and interview.skills_config_data:
            skills_data = json.loads(interview.skills_config_data)
            focus_areas = [s["name"] for s in skills_data.get("skills", [])]

        # For non-technical interviews, strip coding-style question types regardless
        # of what the frontend sent — defense in depth against stale tabs.
        effective_question_types = list(request.question_types or [])
        if (interview.interview_type or "").lower().replace("_", "-") == "non-technical":
            effective_question_types = [qt for qt in effective_question_types if qt not in ("coding", "technical")]
            if not effective_question_types:
                effective_question_types = ["multiple_choice"]

        # Build configuration for prompt builder
        config = {
            'num_questions': request.num_questions,
            'difficulty': request.difficulty_level,
            'skills': focus_areas,
            'question_types': effective_question_types
        }
        
        # Generate prompt
        prompt = prompt_builder.build_interview_prompt(config)
        
        # Generate questions using AI
        questions = await gemini_service.generate_questions(prompt)
        
        # Build response questions from QuestionCreate objects
        now = datetime.now()
        saved_questions = []
        for i, q in enumerate(questions[:request.num_questions]):
            saved_questions.append({
                "id": str(uuid.uuid4()),
                "interview_id": interview_id,
                "question_text": q.question_text,
                "question_type": q.question_type or effective_question_types[i % len(effective_question_types)],
                "difficulty_level": q.difficulty_level or request.difficulty_level,
                "options": q.options,
                "correct_answer": q.correct_answer,
                "sample_answer": q.sample_answer,
                "explanation": q.explanation,
                "time_limit_minutes": q.time_limit_minutes or 5,
                "points": q.points or 10,
                "status": "generated",
                "created_at": now,
                "updated_at": None
            })

        return InterviewQuestionResponse(
            success=True,
            message=f"Successfully generated {len(saved_questions)} questions",
            interview_id=interview_id,
            questions_generated=len(saved_questions),
            questions=saved_questions
        )
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to generate questions: {str(e)}"
        )

@router.get("/interviews/{interview_id}/questions")
async def get_interview_questions(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Get all questions for an interview"""
    try:
        # Get interview
        interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id), Interview, current_user
        ).first()
        if not interview:
            raise HTTPException(status_code=404, detail="Interview not found")
        
        # Check access
        if interview.created_by != current_user.email:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Query actual questions from DB
        from src.models.question_models import Question as InterviewQuestion
        questions = db.query(InterviewQuestion).filter(
            InterviewQuestion.interview_id == interview_id
        ).all()

        question_list = []
        for q in questions:
            qtype = q.question_type or "multiple_choice"
            # Normalize question_type to valid enum value
            valid_types = ["multiple_choice", "short_answer", "coding", "behavioral", "technical", "situational"]
            if qtype not in valid_types:
                qtype = "multiple_choice"
            import json as _json
            raw_opts = q.options
            try:
                parsed_opts = _json.loads(raw_opts) if raw_opts and isinstance(raw_opts, str) else raw_opts
            except (ValueError, TypeError):
                parsed_opts = raw_opts
            question_list.append({
                "id": str(q.id),
                "question_text": q.question_text,
                "question_type": qtype,
                "difficulty_level": q.difficulty_level or "Medium",
                "category": q.category,
                "options": parsed_opts,
                "correct_answer": q.correct_answer,
                "sample_answer": q.sample_answer,
                "code_template": q.code_template,
                "test_cases": _json.loads(q.test_cases) if q.test_cases else None,
                "time_limit_minutes": q.time_limit_minutes or 5,
                "points": q.points or 10,
                "status": q.status or "generated",
                "is_approved": q.is_approved or False,
                "generation_model": q.generation_model,
                "explanation": q.approval_notes,
                "created_at": q.created_at,
                "skill_name": q.skill_name,
                "is_pool_question": q.is_pool_question or False
            })

        return question_list
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get questions: {str(e)}"
        )

@router.delete("/interviews/{interview_id}/questions")
async def delete_all_questions(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Delete all questions for an interview"""
    try:
        # Get interview
        interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id), Interview, current_user
        ).first()
        if not interview:
            raise HTTPException(status_code=404, detail="Interview not found")
        
        # Check access
        if interview.created_by != current_user.email:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Delete questions (simplified - no actual deletion since we're not using InterviewQuestion model)
        deleted_count = 0  # We'll return 0 since we're not using InterviewQuestion model
        
        db.commit()
        
        return {"message": f"Deleted {deleted_count} questions"}
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete questions: {str(e)}"
        )

@router.put("/interviews/{interview_id}/questions/{question_id}")
async def update_question(
    interview_id: int,
    question_id: str,
    question_update: QuestionUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Update a specific question"""
    try:
        interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id), Interview, current_user
        ).first()
        if not interview:
            raise HTTPException(status_code=404, detail="Interview not found")
        if interview.created_by != current_user.email:
            raise HTTPException(status_code=403, detail="Access denied")

        from src.models.question_models import Question as InterviewQuestion
        q = db.query(InterviewQuestion).filter(
            InterviewQuestion.id == question_id,
            InterviewQuestion.interview_id == interview_id
        ).first()
        if not q:
            raise HTTPException(status_code=404, detail="Question not found")

        update_data = question_update.model_dump(exclude_unset=True)
        if "question_text" in update_data:
            q.question_text = update_data["question_text"]
        if "question_type" in update_data:
            q.question_type = update_data["question_type"]
        if "difficulty_level" in update_data:
            q.difficulty_level = update_data["difficulty_level"]
        if "options" in update_data and update_data["options"] is not None:
            import json as _json
            q.options = _json.dumps(update_data["options"])
        if "correct_answer" in update_data:
            q.correct_answer = update_data["correct_answer"]
        if "sample_answer" in update_data:
            q.sample_answer = update_data["sample_answer"]
        if "code_template" in update_data:
            q.code_template = update_data["code_template"]
        if "category" in update_data:
            q.category = update_data["category"]
        if "status" in update_data:
            q.status = update_data["status"]
        if "is_approved" in update_data:
            q.is_approved = update_data["is_approved"]
        if "points" in update_data:
            q.points = update_data["points"]
        if "time_limit_minutes" in update_data:
            q.time_limit_minutes = update_data["time_limit_minutes"]

        db.commit()
        db.refresh(q)

        return {
            "id": str(q.id),
            "question_text": q.question_text,
            "question_type": q.question_type,
            "difficulty_level": q.difficulty_level,
            "status": q.status,
            "is_approved": q.is_approved,
            "updated_at": datetime.now()
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to update question: {str(e)}"
        )

@router.post("/interviews/{interview_id}/questions/{question_id}/regenerate")
async def regenerate_question(
    interview_id: int,
    question_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Regenerate a specific question"""
    try:
        # Get interview
        interview = _company_scope(
            db.query(Interview).filter(Interview.id == interview_id), Interview, current_user
        ).first()
        if not interview:
            raise HTTPException(status_code=404, detail="Interview not found")
        
        # Check access
        if interview.created_by != current_user.email:
            raise HTTPException(status_code=403, detail="Access denied")
        
        # Get question (simplified - return mock response since we're not using InterviewQuestion model)
        question = {
            "id": question_id,
            "interview_id": interview_id,
            "question_text": "Sample question - InterviewQuestion model not integrated",
            "question_type": "descriptive",
            "difficulty": "Medium",
            "updated_at": datetime.now()
        }
        
        # Check AI service health
        is_healthy = await gemini_service.check_health()
        if not is_healthy:
            raise HTTPException(status_code=503, detail="AI service not available")
        
        # Generate new question
        prompt = f"Generate a new {question['difficulty']} {question['question_type']} question for a {interview.interview_type} interview."
        new_questions = await gemini_service.generate_questions(prompt)
        
        if new_questions:
            question["question_text"] = new_questions[0]
            question["updated_at"] = datetime.now()
        
        return {
            "id": question["id"],
            "question_text": question["question_text"],
            "question_type": question["question_type"],
            "difficulty": question["difficulty"],
            "updated_at": question["updated_at"]
        }
        
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail=f"Failed to regenerate question: {str(e)}"
        )

# ==================== STATISTICS ENDPOINTS ====================

@router.get("/stats/questions")
async def get_question_statistics(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_role(_STAFF_ROLES))
):
    """Get question generation statistics"""
    try:
        # Get user's interviews
        interviews = db.query(Interview).filter(
            Interview.company_id == current_user.company_id,
            Interview.created_by == current_user.email
        ).all()
        
        interview_ids = [i.id for i in interviews]
        
        # Get question counts (simplified - return mock data since we're not using InterviewQuestion model)
        total_questions = 0  # We'll return 0 since we're not using InterviewQuestion model
        
        # Get questions by type (mock data)
        questions_by_type = [("descriptive", 0), ("coding", 0), ("mcq", 0)]
        
        # Get questions by difficulty (mock data)
        questions_by_difficulty = [("Easy", 0), ("Medium", 0), ("Hard", 0)]
        
        return {
            "total_interviews": len(interviews),
            "total_questions": total_questions,
            "questions_by_type": dict(questions_by_type),
            "questions_by_difficulty": dict(questions_by_difficulty),
            "ai_service_healthy": await gemini_service.check_health()
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to get statistics: {str(e)}"
        )


# ==================== CANDIDATE REQUEST ENDPOINTS ====================

class CandidateRequestCreate(BaseModel):
    job_title: str
    department: Optional[str] = None
    role_level: Optional[str] = None
    employment_type: Optional[str] = None
    num_candidates: int = 1
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    min_experience_years: int = 0
    max_experience_years: Optional[int] = None
    education_requirement: Optional[str] = None
    job_description: Optional[str] = None
    job_category: str = "technical"   # technical | non_technical | mixed
    urgency: str = "normal"
    notes: Optional[str] = None


@router.post("/candidate-requests")
async def create_candidate_request(
    request: CandidateRequestCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Interviewer submits a request for candidates with role/skill requirements"""
    # An interviewer could previously raise the same role over and over, and each request
    # could become another job posting. Refuse a second one while the first is still
    # unfinished, and say which one is in the way — the role they want is usually already
    # in flight, so pointing at it is more use than a bare rejection.
    duplicate = find_open_duplicate_request(
        db, current_user.company_id, request.job_title, request.department)
    if duplicate:
        # A plain string: the interviewer UI puts response.data.detail straight into React
        # state, so a list-of-objects detail would crash the page rather than show.
        raise HTTPException(
            status_code=409,
            detail=duplicate_request_detail(
                duplicate,
                db.query(User).filter(User.id == duplicate.requested_by).first()),
        )

    cr = CandidateRequest(
        company_id=current_user.company_id,
        requested_by=current_user.id,
        job_title=request.job_title,
        department=request.department,
        role_level=request.role_level,
        employment_type=request.employment_type,
        num_candidates=request.num_candidates,
        required_skills=json.dumps(request.required_skills) if request.required_skills else None,
        preferred_skills=json.dumps(request.preferred_skills) if request.preferred_skills else None,
        min_experience_years=request.min_experience_years,
        max_experience_years=request.max_experience_years,
        education_requirement=request.education_requirement,
        job_description=request.job_description,
        job_category=request.job_category or "technical",
        urgency=request.urgency,
        notes=request.notes,
        status="pending",
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)

    return {
        "id": cr.id,
        "message": "Candidate request submitted. HR will be notified when you publish the test.",
        "status": cr.status,
    }


class GenerateJDRequest(BaseModel):
    job_title: str
    experience_level: str = "Mid-level"
    key_skills: Optional[str] = ""
    focus_areas: Optional[str] = ""


@router.post("/generate-jd")
async def generate_job_description(
    request: GenerateJDRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Generate a structured job description using Gemini AI"""
    prompt = f"""Generate a professional job description for the following role.
Respond ONLY with valid JSON in this exact format, nothing else:
{{
  "job_description": "3-4 sentence paragraph describing key responsibilities and what the role involves",
  "required_skills": ["skill1", "skill2", "skill3", "skill4", "skill5"],
  "preferred_skills": ["skill1", "skill2", "skill3"],
  "min_experience_years": 2,
  "role_level": "Mid-level"
}}

Role: {request.job_title}
Experience Level: {request.experience_level}
Key Skills Hint: {request.key_skills or 'not specified'}
Focus Areas: {request.focus_areas or 'general software engineering'}

Rules:
- required_skills: 5-8 items
- preferred_skills: 3-5 items
- min_experience_years: integer matching the experience level (Junior=0-1, Mid-level=2-4, Senior=5-8, Lead=8+)
- role_level must be one of: Junior, Mid-level, Senior, Lead
- Return ONLY the JSON object, no markdown fences, no explanation"""

    try:
        # thinking_budget=0: gemini-2.5-flash otherwise spends max_output_tokens on
        # internal "thinking" and truncates the JSON mid-string. Disable it and give
        # ample room so the small JSON object always completes.
        raw = await gemini_service.generate_text(
            prompt, temperature=0.3, num_predict=2048, thinking_budget=0
        )

        # Strip markdown fences if present
        text = raw.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        if text.endswith("```"):
            text = text[:-3].strip()

        # Strip <think>...</think> blocks some models emit
        import re
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

        # Tolerate any leading/trailing prose around the JSON object.
        if "{" in text and "}" in text:
            text = text[text.find("{"): text.rfind("}") + 1]

        parsed = json.loads(text)
        return {
            "job_description": parsed.get("job_description", ""),
            "required_skills": parsed.get("required_skills", []),
            "preferred_skills": parsed.get("preferred_skills", []),
            "min_experience_years": parsed.get("min_experience_years", 0),
            "role_level": parsed.get("role_level", request.experience_level),
        }

    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail=f"AI returned non-JSON response. Raw: {raw[:300]}")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"AI generation failed: {str(e)}")


@router.get("/candidate-requests")
async def list_candidate_requests(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    """Get all candidate requests submitted by the current interviewer"""
    requests = db.query(CandidateRequest).filter(
        CandidateRequest.requested_by == current_user.id
    ).order_by(CandidateRequest.created_at.desc()).all()

    return [
        {
            "id": r.id,
            "job_id": r.job_id,
            "job_title": r.job_title,
            "department": r.department,
            "role_level": r.role_level,
            "employment_type": r.employment_type,
            "num_candidates": r.num_candidates,
            "required_skills": json.loads(r.required_skills) if r.required_skills else [],
            "preferred_skills": json.loads(r.preferred_skills) if r.preferred_skills else [],
            "min_experience_years": r.min_experience_years,
            "max_experience_years": r.max_experience_years,
            "education_requirement": r.education_requirement,
            "job_description": r.job_description,
            "job_category": r.job_category or "technical",
            "urgency": r.urgency,
            "notes": r.notes,
            "status": r.status,
            "hr_notes": r.hr_notes,
            "created_at": r.created_at,
            "updated_at": r.updated_at,
        }
        for r in requests
    ]


# ==================== JD TEMPLATE ENDPOINTS ====================

_ALLOWED_TEMPLATE_ROLES = {"interviewer", "hr", "admin", "superadmin"}


def _require_template_role(user: User):
    if user.role not in _ALLOWED_TEMPLATE_ROLES:
        raise HTTPException(status_code=403, detail="Not allowed to manage templates")


def _serialize_template(t: JDTemplate) -> dict:
    return {
        "id": t.id,
        "company_id": t.company_id,
        "created_by": t.created_by,
        "name": t.name,
        "category": t.category,
        "job_category": t.job_category or "technical",
        "role_level": t.role_level,
        "employment_type": t.employment_type,
        "min_experience_years": t.min_experience_years or 0,
        "required_skills": json.loads(t.required_skills) if t.required_skills else [],
        "preferred_skills": json.loads(t.preferred_skills) if t.preferred_skills else [],
        "job_description": t.job_description or "",
        "is_builtin": bool(t.is_builtin),
        "is_shared": bool(t.is_shared),
        "source": t.source or "manual",
        "created_at": t.created_at,
        "updated_at": t.updated_at,
    }


class TemplateCreate(BaseModel):
    name: str
    category: Optional[str] = None
    job_category: str = "technical"
    role_level: Optional[str] = None
    employment_type: Optional[str] = None
    min_experience_years: int = 0
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    job_description: Optional[str] = None
    source: str = "manual"  # manual | ai


class TemplateUpdate(BaseModel):
    name: Optional[str] = None
    category: Optional[str] = None
    job_category: Optional[str] = None
    role_level: Optional[str] = None
    employment_type: Optional[str] = None
    min_experience_years: Optional[int] = None
    required_skills: Optional[List[str]] = None
    preferred_skills: Optional[List[str]] = None
    job_description: Optional[str] = None


@router.get("/templates")
async def list_templates(
    job_category: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List templates visible to this user — builtins (company_id IS NULL) plus the
    user's company-shared and personal templates. Optionally filter by job_category.
    """
    _require_template_role(current_user)
    # Built-ins are platform-wide rows with no company, which the tenant auto-filter
    # (src/tenancy/events.py) hides from every tenant. This read opts out of it, so the
    # or_ below is the only scoping: built-ins, or the caller's own company.
    q = db.query(JDTemplate).execution_options(_tenant_bypass=True).filter(
        (JDTemplate.company_id.is_(None) & (JDTemplate.is_builtin == True))  # noqa: E712
        | (JDTemplate.company_id == current_user.company_id)
    )
    if job_category:
        q = q.filter(JDTemplate.job_category == job_category)
    rows = q.order_by(JDTemplate.is_builtin.desc(), JDTemplate.name.asc()).all()
    return [_serialize_template(t) for t in rows]


@router.post("/templates", status_code=201)
async def create_template(
    body: TemplateCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a new template owned by the current user's company."""
    _require_template_role(current_user)
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="Template name is required")

    t = JDTemplate(
        company_id=current_user.company_id,
        created_by=current_user.id,
        name=body.name.strip(),
        category=body.category,
        job_category=body.job_category or "technical",
        role_level=body.role_level,
        employment_type=body.employment_type,
        min_experience_years=body.min_experience_years or 0,
        required_skills=json.dumps(body.required_skills) if body.required_skills else None,
        preferred_skills=json.dumps(body.preferred_skills) if body.preferred_skills else None,
        job_description=body.job_description,
        is_builtin=False,
        is_shared=True,
        source=body.source if body.source in ("manual", "ai") else "manual",
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return _serialize_template(t)


@router.put("/templates/{template_id}")
async def update_template(
    template_id: str,
    body: TemplateUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update a user-owned template. Builtins cannot be edited."""
    _require_template_role(current_user)
    t = db.query(JDTemplate).filter(
        JDTemplate.id == template_id,
        JDTemplate.company_id == current_user.company_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if t.is_builtin:
        raise HTTPException(status_code=403, detail="Built-in templates cannot be edited")

    if body.name is not None:
        t.name = body.name.strip()
    if body.category is not None:
        t.category = body.category
    if body.job_category is not None:
        t.job_category = body.job_category
    if body.role_level is not None:
        t.role_level = body.role_level
    if body.employment_type is not None:
        t.employment_type = body.employment_type
    if body.min_experience_years is not None:
        t.min_experience_years = body.min_experience_years
    if body.required_skills is not None:
        t.required_skills = json.dumps(body.required_skills)
    if body.preferred_skills is not None:
        t.preferred_skills = json.dumps(body.preferred_skills)
    if body.job_description is not None:
        t.job_description = body.job_description

    db.commit()
    db.refresh(t)
    return _serialize_template(t)


@router.delete("/templates/{template_id}")
async def delete_template(
    template_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Delete a user-owned template. Builtins cannot be deleted."""
    _require_template_role(current_user)
    t = db.query(JDTemplate).filter(
        JDTemplate.id == template_id,
        JDTemplate.company_id == current_user.company_id,
    ).first()
    if not t:
        raise HTTPException(status_code=404, detail="Template not found")
    if t.is_builtin:
        raise HTTPException(status_code=403, detail="Built-in templates cannot be deleted")

    db.delete(t)
    db.commit()
    return {"deleted": True, "id": template_id}


@router.post("/templates/from-ai", status_code=201)
async def save_ai_template(
    body: TemplateCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Convenience endpoint to save an AI-generated JD as a reusable template.
    Identical to POST /templates but forces source='ai'.
    """
    body.source = "ai"
    return await create_template(body, current_user, db)


# ==================== EDIT EXAM / QUESTIONS (interviewer tab) ====================
#
# These routes back the interviewer's "Edit exam/questions" tab. They deliberately live
# under /tests/* and /skills/* rather than reusing /interviews, because GET
# /api/v1/interviewer/interviews is route-shadowed by routes/interviewer_routes.py (both
# routers share this prefix and that one is registered first). Its response model requires
# scheduled_datetime and duration_minutes as non-Optional, so a single DRAFT — which leaves
# both NULL — makes the whole list 500.
#
# They are also explicitly tenant-scoped: questions are scoped by joining to
# interviews.company_id, the owner of record (same idea as mcq_api._assert_question_in_tenant).

from sqlalchemy import func as _sa_func, text as _sa_text
from src.auth import require_role as _require_role
from src.models.question_models import (
    Question as _Q,
    QuestionSetAssignment as _QSA,
    CandidateQuestionHistory as _CQH,
    CommunicationAnswer as _CA,
)

# Defaults for proctoring keys absent from security_settings_data (drafts and older tests
# leave the column NULL). Tab-switch defaults ON so those keep today's stricter behaviour;
# copy/paste defaults to blocked for the same reason.
_PROCTOR_DEFAULTS = {
    "enable_webcam_monitoring": False,
    "enable_tab_switch_detection": True,
    "require_fullscreen": True,
    "copy_paste_allowed": False,
}

# A candidate's copy that has been invited but not begun. Settings edits cascade onto these;
# IN_PROGRESS / COMPLETED attempts are never touched.
_PENDING_CANDIDATE_STATUSES = ("SCHEDULED", "READY")


def _safe_json(raw, default=None):
    """Parse a JSON TEXT column, returning `default` on NULL or malformed content."""
    fallback = {} if default is None else default
    if not raw:
        return fallback
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, (dict, list)) else fallback
    except (json.JSONDecodeError, TypeError, ValueError):
        return fallback


def _security_payload(raw):
    """The four security settings from a blob, with the defaults above applied."""
    sec = _safe_json(raw)
    out = {"time_per_question": int(sec.get("time_per_question") or 5)}
    for key, default in _PROCTOR_DEFAULTS.items():
        out[key] = bool(sec.get(key, default))
    return out


def _question_payload(q):
    return {
        "id": q.id,
        "interview_id": q.interview_id,
        "question_text": q.question_text,
        "question_type": q.question_type,
        "difficulty_level": q.difficulty_level,
        "category": q.category,
        "skill_name": q.skill_name,
        "options": _safe_json(q.options, []),
        "correct_answer": q.correct_answer,
        "sample_answer": q.sample_answer,
        "code_template": q.code_template,
        "test_cases": _safe_json(q.test_cases, []),
        "language": getattr(q, "language", None),
        "points": q.points,
        "time_limit_minutes": q.time_limit_minutes,
        "status": q.status,
        "is_approved": q.is_approved,
        "created_at": q.created_at,
        "updated_at": q.updated_at,
    }


def _test_payload(iv, question_count=0):
    """Lean, defensively-parsed view of a test.

    Deliberately not _build_interview_response(): that splats **json.loads(...) into
    pydantic models, so one unexpected key in any blob raises. The nested key names match
    it, because the wizard's loadDraftIntoWizard() reads that shape when resuming a draft.
    """
    cfg = _safe_json(iv.interview_config_data)
    duration = iv.duration_minutes if iv.duration_minutes is not None else (cfg.get("duration_minutes") or 60)
    return {
        "id": iv.id,
        "title": iv.title or "",
        "description": iv.description or "",
        "status": iv.status,
        "job_id": iv.job_id,
        "job_position": iv.job_position or "",
        "job_title": iv.job_title or "",
        "num_sets": iv.num_sets or 1,
        "question_count": question_count,
        "created_at": iv.created_at,
        "updated_at": iv.updated_at,
        "interview_configuration": {
            "interview_type": cfg.get("interview_type") or iv.interview_type or "TECHNICAL",
            "difficulty_level": cfg.get("difficulty_level") or iv.difficulty_level or "MEDIUM",
            "duration_minutes": duration,
            # Duration is derived from this map; the editor changes the map, not the total.
            "time_per_question_type": cfg.get("time_per_question_type") or {},
            "scheduled_datetime": iv.scheduled_datetime,
        },
        "security_settings": _security_payload(iv.security_settings_data),
        "job_details": _safe_json(iv.job_details_data),
        "skills_configuration": _safe_json(iv.skills_config_data),
        "question_format": _safe_json(iv.question_format_data),
        "evaluation_rules": _safe_json(iv.evaluation_rules_data),
        "ai_generation_rules": _safe_json(iv.ai_generation_rules_data),
        "interviewer_details": _safe_json(iv.interviewer_details_data),
        "notes": iv.notes,
    }


def _company_scope(q, model, current_user):
    """Scope a query to the caller's company.

    A row with a NULL company_id belongs to no tenant and matches nobody: it used to be
    visible to every company, which exposed drafts, questions and answer keys across
    tenants. Legacy NULL rows are repaired by scripts/backfill_orphan_company_ids.py,
    not tolerated here. Only the superadmin, who has no company, reads unscoped; any
    other company-less caller gets nothing.
    """
    if not current_user.company_id:
        return q if current_user.role == "superadmin" else q.filter(False)
    return q.filter(model.company_id == current_user.company_id)


def _owned_templates_query(db, current_user):
    """Template tests the caller may edit: everything in their company that is a template.

    candidate_id IS NULL excludes per-candidate clones and is_scheduled_interview excludes
    scheduled meetings — together that is what makes this a template list. There is
    deliberately no created_by/interviewer_email restriction: HR and interviewers share the
    create-test wizard, so filtering by author hides HR-created tests from the interviewer
    who actually runs them.
    """
    q = db.query(Interview).filter(
        Interview.candidate_id.is_(None),
        Interview.is_scheduled_interview == False,  # noqa: E712
    )
    return _company_scope(q, Interview, current_user)


def _question_counts(db, interview_ids):
    """{interview_id: question_count} in a single query."""
    if not interview_ids:
        return {}
    rows = (
        db.query(_Q.interview_id, _sa_func.count(_Q.id))
        .filter(_Q.interview_id.in_(interview_ids))
        .group_by(_Q.interview_id)
        .all()
    )
    return {iv_id: cnt for iv_id, cnt in rows}


@router.get("/tests")
async def list_editable_tests(
    include_drafts: bool = Query(True),
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Template tests the interviewer can edit (Edit exam section)."""
    q = _owned_templates_query(db, current_user)
    if not include_drafts:
        q = q.filter(Interview.status != "DRAFT")
    tests = q.order_by(Interview.created_at.desc()).all()
    counts = _question_counts(db, [t.id for t in tests])
    return {"tests": [_test_payload(t, counts.get(t.id, 0)) for t in tests]}


@router.get("/tests/drafts")
async def list_draft_tests(
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Unfinished tests (Exam drafts section).

    question_count separates a draft with real work from an abandoned empty one — the same
    signal publish_interview() uses when it garbage-collects sibling drafts.
    """
    drafts = (
        _owned_templates_query(db, current_user)
        .filter(Interview.status == "DRAFT")
        .order_by(Interview.updated_at.desc())
        .all()
    )
    counts = _question_counts(db, [d.id for d in drafts])
    return {"drafts": [_test_payload(d, counts.get(d.id, 0)) for d in drafts]}


# Minutes per question type when a test has none configured. Mirrors TYPE_TIME_DEFAULTS in
# CreateTestWizard.jsx so the duration computed here matches what the wizard displays.
_TYPE_TIME_DEFAULTS_MIN = {
    "multiple_choice": 1, "coding": 15, "short_answer": 5,
    "case_study": 5, "aptitude": 5, "communication": 5,
}


def _compute_duration(skills_cfg, time_map):
    """Total minutes = every question in a set at its type's configured time.

    Server-side mirror of computeDuration() in CreateTestWizard.jsx. Duration is derived,
    never entered directly, so the advertised duration and the real clock cannot drift.
    """
    total = 0
    for skill in (skills_cfg or {}).get("skills", []) or []:
        if not str(skill.get("name") or "").strip():
            continue
        for qt in skill.get("question_types", []) or []:
            per = (time_map or {}).get(qt.get("type"))
            if not per:
                per = _TYPE_TIME_DEFAULTS_MIN.get(qt.get("type"), 5)
            total += int(qt.get("count_per_set") or 0) * int(per)
    return total


class TestSettingsUpdate(BaseModel):
    """Partial settings edit. Every field optional — unlike PUT /interviews/{id}, whose
    InterviewConfiguration/SecuritySettings require every field, so a settings-only edit
    there silently rewrites interview_type and difficulty_level.

    Note there is no duration_minutes: duration is computed from time_per_question_type
    times the per-type question counts, so accepting a direct value would reintroduce the
    divergence that computeDuration() exists to prevent.
    """
    time_per_question_type: Optional[Dict[str, int]] = None
    enable_webcam_monitoring: Optional[bool] = None
    enable_tab_switch_detection: Optional[bool] = None
    require_fullscreen: Optional[bool] = None
    copy_paste_allowed: Optional[bool] = None
    apply_to_pending_invites: bool = True


@router.patch("/tests/{interview_id}/settings")
async def update_test_settings(
    interview_id: int,
    body: TestSettingsUpdate,
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Edit a test's duration and proctoring flags, cascading to candidates who have been
    invited but have not started."""
    interview = _owned_templates_query(db, current_user).filter(Interview.id == interview_id).first()
    if not interview:
        raise HTTPException(status_code=404, detail="Test not found")

    fields = body.model_dump(exclude_unset=True)
    fields.pop("apply_to_pending_invites", None)
    if not fields:
        raise HTTPException(status_code=400, detail="No settings supplied")

    # Per-type time: store the map and RECOMPUTE the duration from it, so the advertised
    # duration always equals the sum of the per-question clocks.
    new_duration = None
    if "time_per_question_type" in fields:
        time_map = fields["time_per_question_type"] or {}
        cfg = _safe_json(interview.interview_config_data)
        cfg["time_per_question_type"] = time_map
        computed = _compute_duration(_safe_json(interview.skills_config_data), time_map)
        # A test with no skills configuration would compute 0 — keep its existing duration.
        if computed > 0:
            cfg["duration_minutes"] = computed
            interview.duration_minutes = computed
            new_duration = computed
        interview.interview_config_data = json.dumps(cfg, default=str)

    # security: read-modify-write so untouched keys survive
    sec_keys = {k: v for k, v in fields.items() if k != "time_per_question_type"}
    if sec_keys:
        sec = _safe_json(interview.security_settings_data) or _security_payload(None)
        sec.update(sec_keys)
        interview.security_settings_data = json.dumps(sec, default=str)

    interview.updated_at = datetime.utcnow()

    # Cascade onto invited-but-not-started candidate copies. _clone_test_for_candidate
    # stamps interview_config_data["_source_test_id"] — the only link back to the template.
    updated_pending = 0
    if body.apply_to_pending_invites:
        clones = db.query(Interview).filter(
            Interview.company_id == current_user.company_id,
            Interview.candidate_id.isnot(None),
            Interview.status.in_(_PENDING_CANDIDATE_STATUSES),
        ).all()
        for clone in clones:
            clone_cfg = _safe_json(clone.interview_config_data)
            if clone_cfg.get("_source_test_id") != interview_id:
                continue
            if "time_per_question_type" in fields:
                clone_cfg["time_per_question_type"] = fields["time_per_question_type"] or {}
                if new_duration is not None:
                    clone_cfg["duration_minutes"] = new_duration
                    clone.duration_minutes = new_duration
                clone.interview_config_data = json.dumps(clone_cfg, default=str)
            if sec_keys:
                csec = _safe_json(clone.security_settings_data) or _security_payload(None)
                csec.update(sec_keys)
                clone.security_settings_data = json.dumps(csec, default=str)
            clone.updated_at = datetime.utcnow()
            updated_pending += 1

    db.commit()
    db.refresh(interview)
    return {"test": _test_payload(interview), "updated_pending_invites": updated_pending}


# ---------------------------- skills & question bank ----------------------------

def _tenant_pool_questions(db, current_user):
    """Pool (bank) questions belonging to the caller's company.

    Scoped by joining interviews.company_id — the owning interview is the source of
    truth for a question's tenant (its own company_id is copied from it).
    """
    q = (
        db.query(_Q)
        .join(Interview, _Q.interview_id == Interview.id)
        .filter(_Q.is_pool_question == True)  # noqa: E712
    )
    return _company_scope(q, Interview, current_user)


@router.get("/skills")
async def list_question_skills(
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Distinct skills in the question bank with per-skill counts (Edit questions section)."""
    base = (
        db.query(_Q.skill_name, _sa_func.count(_Q.id))
        .join(Interview, _Q.interview_id == Interview.id)
        .filter(_Q.is_pool_question == True)  # noqa: E712
    )
    rows = _company_scope(base, Interview, current_user).group_by(_Q.skill_name).all()

    # Only generate-skill-questions marks questions as pool masters; the bulk
    # generate-questions path never does. If a company has none, fall back to grouping
    # every question on its tests so the bank is not silently empty.
    if not rows:
        fallback = (
            db.query(_Q.skill_name, _sa_func.count(_Q.id))
            .join(Interview, _Q.interview_id == Interview.id)
        )
        rows = _company_scope(fallback, Interview, current_user).group_by(_Q.skill_name).all()
    skills = [
        {"skill_name": (name or "General"), "question_count": count}
        for name, count in rows if count
    ]
    skills.sort(key=lambda s: (-s["question_count"], s["skill_name"].lower()))
    return {"skills": skills}


@router.get("/skills/{skill_name}/questions")
async def list_questions_for_skill(
    skill_name: str,
    question_type: Optional[str] = Query(None),
    difficulty: Optional[str] = Query(None),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Bank questions for one skill. Tenant-scoped, unlike /interviews/questions/search."""

    def _for(pool_only):
        q = db.query(_Q).join(Interview, _Q.interview_id == Interview.id)
        if pool_only:
            q = q.filter(_Q.is_pool_question == True)  # noqa: E712
        q = _company_scope(q, Interview, current_user)
        if skill_name in ("General", "_unknown"):
            q = q.filter((_Q.skill_name.is_(None)) | (_Q.skill_name == ""))
        else:
            q = q.filter(_Q.skill_name == skill_name)
        if question_type:
            q = q.filter(_Q.question_type == question_type)
        if difficulty:
            q = q.filter(_Q.difficulty_level.ilike(difficulty))
        return q.order_by(_Q.created_at.desc()).limit(limit).all()

    # Mirror the fallback in /skills: if this company has no pool masters, the skill list was
    # built from all questions, so look them up the same way or the popup would come back empty.
    rows = _for(pool_only=True) or _for(pool_only=False)
    return {"questions": [_question_payload(r) for r in rows]}


class BankQuestionUpdate(BaseModel):
    question_text: Optional[str] = None
    question_type: Optional[str] = None
    difficulty_level: Optional[str] = None
    category: Optional[str] = None
    skill_name: Optional[str] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    code_template: Optional[str] = None
    test_cases: Optional[List[dict]] = None
    language: Optional[str] = None
    points: Optional[int] = Field(default=None, ge=1, le=100)
    time_limit_minutes: Optional[int] = Field(default=None, ge=1, le=180)


def _get_tenant_question(db, question_id, current_user):
    """Fetch a question, 404ing unless it belongs to the caller's company.

    Ownership is the interview the question hangs off. A question with no interview, or
    whose interview has no company, belongs to no tenant and is refused.
    """
    q = (
        _company_scope(
            db.query(_Q).join(Interview, _Q.interview_id == Interview.id),
            Interview,
            current_user,
        )
        .filter(_Q.id == question_id)
        .first()
    )
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    return q


def _count_question_references(db, question_id):
    """Child rows pointing at this question. All four FKs lack ON DELETE CASCADE, so a
    delete with any present raises MySQL 1451 and surfaces as an opaque 500."""
    refs = {
        "test_sets": db.query(_QSA).filter(_QSA.question_id == question_id).count(),
        "candidate_history": db.query(_CQH).filter(_CQH.source_question_id == question_id).count(),
        "voice_answers": db.query(_CA).filter(_CA.question_id == question_id).count(),
    }
    # interview_code_submissions has no ORM model imported here; count defensively.
    try:
        refs["code_submissions"] = db.execute(
            _sa_text("SELECT COUNT(*) FROM interview_code_submissions WHERE question_id = :qid"),
            {"qid": question_id},
        ).scalar() or 0
    except Exception:
        refs["code_submissions"] = 0
    return refs


@router.put("/questions/{question_id}")
async def update_bank_question(
    question_id: str,
    body: BankQuestionUpdate,
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Edit a question in the bank."""
    q = _get_tenant_question(db, question_id, current_user)
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields supplied")
    for field, value in data.items():
        if field in ("options", "test_cases"):
            setattr(q, field, json.dumps(value) if value is not None else None)
        else:
            setattr(q, field, value)
    q.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(q)
    return _question_payload(q)


@router.get("/questions/{question_id}/usage")
async def question_usage(
    question_id: str,
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Where a question is referenced — drives the delete confirmation."""
    _get_tenant_question(db, question_id, current_user)
    return {"references": _count_question_references(db, question_id)}


@router.delete("/questions/{question_id}")
async def delete_bank_question(
    question_id: str,
    current_user: User = Depends(_require_role(["interviewer", "hr", "admin"])),
    db: Session = Depends(get_db),
):
    """Permanently delete a bank question, refusing while it is still referenced."""
    q = _get_tenant_question(db, question_id, current_user)
    refs = _count_question_references(db, question_id)
    in_use = {k: v for k, v in refs.items() if v}
    if in_use:
        parts = ", ".join(f"{v} {k.replace('_', ' ')}" for k, v in in_use.items())
        raise HTTPException(
            status_code=409,
            detail=f"This question is in use ({parts}) and cannot be deleted. "
                   f"Remove it from those tests first.",
        )
    db.delete(q)  # ORM delete so the set_assignments cascade applies
    db.commit()
    return {"deleted": True, "id": question_id}
