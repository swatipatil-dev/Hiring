"""
🎓 ENHANCED CANDIDATE API ROUTES
Handles job browsing, applications with OCR support, and candidate-specific operations
"""

import asyncio
import logging
from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form, Query
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from src.config.database_config_mysql import get_db

logger = logging.getLogger(__name__)
from src.auth import get_current_user, require_role, AuthMiddleware
from src.models.models import (
    User, JobPosting, Candidate, ATSResult, CandidateStageHistory,
    Department, Interview, CandidateProfileExt, HRProfile,
)
from .candidate_profile_routes import (
    _get_or_create_profile, _serialize_profile, build_profile_text
)
from src.services.enhanced_ats_service import EnhancedATSScoringService
from pydantic import BaseModel, EmailStr, Field, field_validator
from datetime import datetime
from pathlib import Path
import uuid
import os
import json

def _parse_skills(raw_skills):
    """Parse required_skills from various formats (JSON array, comma-separated, list, None)"""
    if isinstance(raw_skills, list):
        return raw_skills
    if isinstance(raw_skills, str) and raw_skills.strip():
        try:
            parsed = json.loads(raw_skills)
            if isinstance(parsed, list):
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass
        return [s.strip() for s in raw_skills.split(",") if s.strip()]
    return []

# Import shared utilities
from src.utils import (
    validate_file_upload, save_file_securely, extract_text_from_file,
    get_ats_interpretation, get_job_statistics,
    validate_pagination_params, validate_sort_params, validate_order_params,
    create_error_response, create_success_response
)
from src.services.job_dedup import duplicate_as_conflict
from src.services.ats_scoring_pipeline import (
    build_inputs_for_candidate, score_for_job, persist_ats_result, ATS_REVIEW_THRESHOLD,
)
from src.services import parse_quality
from src.services.assessment_fairness import (
    approved_accommodations, describe as describe_accommodations,
)

router = APIRouter(prefix="/api/v1/candidate", tags=["Candidates"])

# ==================== PYDANTIC MODELS ====================

class JobPostingResponse(BaseModel):
    id: str
    title: str
    description: str
    requirements: str
    department: str
    location: Optional[str] = ""
    employment_type: Optional[str] = ""
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    experience_years: Optional[int] = 0
    required_skills: List[str] = []
    status: str
    created_at: datetime
    application_count: Optional[int] = 0
    average_ats_score: Optional[float] = None

class CandidateApplication(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    phone: Optional[str] = None
    current_salary: Optional[float] = None
    expected_salary: Optional[float] = None
    notice_period: Optional[str] = None
    linkedin_url: Optional[str] = None
    github_url: Optional[str] = None
    portfolio_url: Optional[str] = None
    additional_info: Optional[str] = None
    cover_letter: Optional[str] = None

class CareerPageResponse(BaseModel):
    jobs: List[JobPostingResponse]
    total_jobs: int
    page: int
    per_page: int
    has_next: bool
    has_prev: bool
    portfolio_url: Optional[str] = None
    additional_info: Optional[str] = None

class CandidateResponse(BaseModel):
    id: str
    first_name: str
    last_name: str
    email: str
    phone: Optional[str]
    current_stage: str
    ats_score: Optional[float]
    applied_at: datetime

class ApplicationResponse(BaseModel):
    id: str
    job_title: str
    company: str
    stage: str
    ats_score: Optional[float]
    applied_at: datetime
    status: str

# ==================== HELPER FUNCTIONS ====================

def create_job_response(job: JobPosting, db: Session) -> JobPostingResponse:
    """Create enhanced job posting response with optimized statistics"""
    try:
        # Use shared utility for statistics
        stats = get_job_statistics(db, str(job.id))
        
        return JobPostingResponse(
            id=str(job.id),
            title=job.title,
            description=job.description or "",
            requirements=job.requirements or "",
            department=job.department.name if job.department else "",
            location=job.location or "",
            employment_type=job.employment_type or "",
            salary_min=float(job.salary_min) if job.salary_min else None,
            salary_max=float(job.salary_max) if job.salary_max else None,
            experience_years=job.experience_years or 0,
            required_skills=_parse_skills(job.required_skills),
            status=job.status,
            created_at=job.created_at,
            application_count=stats['application_count'],
            average_ats_score=stats['average_ats_score']
        )
    except Exception as e:
        print(f"Error creating job response: {e}")
        # Fallback to basic response without stats
        return JobPostingResponse(
            id=str(job.id),
            title=job.title,
            description=job.description,
            requirements=job.requirements,
            department=job.department.name if job.department else "",
            location=job.location,
            employment_type=job.employment_type,
            salary_min=float(job.salary_min) if job.salary_min else None,
            salary_max=float(job.salary_max) if job.salary_max else None,
            experience_years=job.experience_years,
            required_skills=_parse_skills(job.required_skills),
            status=job.status,
            created_at=job.created_at,
            application_count=0,
            average_ats_score=None
        )

def save_resume_file(resume: UploadFile, candidate_email: str, job_id: str) -> str:
    """Save resume file to storage and return its key"""
    import uuid as _uuid
    from src.services.storage import storage
    try:
        file_ext = Path(resume.filename).suffix.lower() if resume.filename else ".txt"
        unique_name = f"{candidate_email}_{job_id}_{_uuid.uuid4().hex[:8]}{file_ext}"
        key = f"resumes/{unique_name}"
        content = resume.file.read()
        storage.save_bytes(content, key)
        resume.file.seek(0)  # Reset for later reading
        return key
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save resume: {str(e)}")

# ==================== JOB BROWSING ENDPOINTS ====================

@router.get("/jobs", response_model=CareerPageResponse)
async def browse_jobs_enhanced(
    page: int = Query(1, ge=1, description="Page number"),
    per_page: int = Query(10, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search term for job titles"),
    department: Optional[str] = Query(None, description="Filter by department"),
    employment_type: Optional[str] = Query(None, description="Filter by employment type"),
    min_salary: Optional[float] = Query(None, description="Filter by minimum salary"),
    max_salary: Optional[float] = Query(None, description="Filter by maximum salary"),
    location: Optional[str] = Query(None, description="Filter by location"),
    remote: Optional[bool] = Query(None, description="Filter for remote jobs"),
    sort_by: Optional[str] = Query("created_at", description="Sort by field"),
    sort_order: Optional[str] = Query("desc", description="Sort order"),
    db: Session = Depends(get_db),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
):
    """
    Enhanced job browsing with pagination, advanced filtering, and statistics
    
    - **page**: Page number for pagination
    - **per_page**: Number of jobs per page (max 100)
    - **search**: Search in job titles and descriptions
    - **department**: Filter by department name
    - **employment_type**: Filter by employment type
    - **min_salary/max_salary**: Filter by salary range
    - **location**: Filter by location
    - **remote**: Filter for remote jobs
    - **sort_by**: Sort field (created_at, title, salary)
    - **sort_order**: Sort order (asc, desc)
    """
    try:
        # Validate pagination parameters
        page, per_page = validate_pagination_params(page, per_page, 100)
        
        # Validate sort parameters
        valid_sort_fields = ["created_at", "title", "salary"]
        sort_by = validate_sort_params(sort_by, valid_sort_fields, "created_at")
        sort_order = validate_order_params(sort_order)
        
        # Build base query — scoped to this candidate's company tenant
        query = db.query(JobPosting).filter(
            JobPosting.status == "active",
            JobPosting.company_id == current_user.company_id,
        )
        
        # Apply filters
        if search:
            search_filter = or_(
                JobPosting.title.ilike(f"%{search}%"),
                JobPosting.description.ilike(f"%{search}%"),
                JobPosting.requirements.ilike(f"%{search}%")
            )
            query = query.filter(search_filter)
        
        if department:
            query = query.join(Department).filter(Department.name.ilike(f"%{department}%"))
        
        if employment_type:
            query = query.filter(JobPosting.employment_type == employment_type)
        
        if min_salary:
            query = query.filter(JobPosting.salary_min >= min_salary)
        
        if max_salary:
            query = query.filter(JobPosting.salary_max <= max_salary)
        
        if location:
            query = query.filter(JobPosting.location.ilike(f"%{location}%"))
        
        if remote:
            query = query.filter(
                or_(
                    JobPosting.location.ilike("%remote%"),
                    JobPosting.location.ilike("%work from home%")
                )
            )
        
        # Apply sorting
        if sort_by == "created_at":
            if sort_order == "desc":
                query = query.order_by(desc(JobPosting.created_at))
            else:
                query = query.order_by(JobPosting.created_at)
        elif sort_by == "title":
            if sort_order == "desc":
                query = query.order_by(desc(JobPosting.title))
            else:
                query = query.order_by(JobPosting.title)
        elif sort_by == "salary":
            if sort_order == "desc":
                query = query.order_by(desc(JobPosting.salary_max))
            else:
                query = query.order_by(JobPosting.salary_min)
        
        # Get total count for pagination
        total_jobs = query.count()
        
        # Apply pagination
        offset = (page - 1) * per_page
        jobs = query.offset(offset).limit(per_page).all()
        
        # Create enhanced responses
        job_responses = [create_job_response(job, db) for job in jobs]
        
        # Calculate pagination info
        has_next = offset + per_page < total_jobs
        has_prev = page > 1
        
        return CareerPageResponse(
            jobs=job_responses,
            total_jobs=total_jobs,
            page=page,
            per_page=per_page,
            has_next=has_next,
            has_prev=has_prev
        )
        
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error in browse_jobs_enhanced: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")

@router.get("/jobs/{job_id}", response_model=JobPostingResponse)
async def get_job_details(
    job_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
):
    """Get detailed information about a specific job posting.

    Scoped to `active` to match the browse list above — a closed, draft or
    pending-approval job must not stay readable through a saved link."""
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id,
        JobPosting.status == "active",
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    return create_job_response(job, db)

# ==================== APPLICATION ENDPOINTS ====================

@router.post("/jobs/{job_id}/apply")
async def apply_for_job_enhanced(
    job_id: str,
    resume: UploadFile = File(...),
    cover_letter: Optional[UploadFile] = File(None),
    photo: Optional[UploadFile] = File(None, description="Candidate photo for identity verification"),
    application: Optional[str] = Form(None),
    # Individual field fallbacks — used when application JSON is not provided
    first_name: Optional[str] = Form(None),
    last_name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    phone: Optional[str] = Form(None),
    years_experience: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Enhanced job application with OCR support and detailed ATS scoring

    - **job_id**: ID of job to apply for
    - **resume**: PDF/DOCX resume file (OCR enabled)
    - **cover_letter**: Optional cover letter file
    - **application**: Application details (JSON string)
    
    Enhanced Features:
    - OCR support for scanned PDFs
    - DOCX file support
    - Real-time ATS scoring with interpretation
    - Detailed candidate analysis
    - Personalized recommendations
    """
    # Check the job exists, is active, and belongs to the caller's company.
    #
    # The company clause IS the guard here — do not remove it on the assumption that
    # the TenantScoped auto-filter (src/tenancy/events.py:71) covers it. Measured: with
    # this clause absent, a candidate in one company could apply to another company's
    # active job by id and receive a 200. Whatever the auto-filter does for other
    # queries, it did not stop this one.
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id,
        JobPosting.status == "active"
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not active")

    # Parse application data — accept either JSON string or individual form fields
    try:
        if application:
            raw = json.loads(application)
            application_data = CandidateApplication(**raw)
        else:
            application_data = CandidateApplication(
                first_name=first_name or current_user.first_name or "Unknown",
                last_name=last_name or current_user.last_name or "Unknown",
                email=email or current_user.email,
                phone=phone,
            )
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid application data: {str(e)}")
    
    # Check if already applied
    existing_application = db.query(Candidate).filter(
        Candidate.job_id == job_id,
        Candidate.email == application_data.email
    ).first()
    if existing_application:
        raise HTTPException(status_code=400, detail="Already applied for this job")
    
    # Save resume file using shared utility
    try:
        resume_path = save_file_securely(resume, "resumes")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save resume: {str(e)}")
    
    # Handle cover letter if provided
    cover_letter_text = None
    if cover_letter:
        try:
            # Validate cover letter
            allowed_cover_extensions = ['.pdf', '.docx', '.doc', '.txt']
            content, file_size = validate_file_upload(cover_letter, allowed_cover_extensions)
            
            # Save cover letter
            cover_letter_path = save_file_securely(cover_letter, "cover_letters")
            
            # Extract text from cover letter using shared utility
            cover_letter_text, _ = extract_text_from_file(cover_letter_path)
                
        except Exception as e:
            print(f"Failed to process cover letter: {e}")
            cover_letter_text = None
    
    # Save candidate photo if provided
    photo_path = None
    if photo:
        try:
            allowed_photo_ext = ['.jpg', '.jpeg', '.png', '.webp']
            ext = Path(photo.filename).suffix.lower() if photo.filename else '.jpg'
            if ext not in allowed_photo_ext:
                raise HTTPException(status_code=400, detail="Photo must be JPG, PNG, or WEBP")
            photo_name = f"{current_user.id}_{job_id}_{uuid.uuid4().hex[:8]}{ext}"
            photo_path = f"uploads/photos/{photo_name}"
            content = await photo.read()
            from src.services.storage import storage
            storage.save_bytes(content, photo_path)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save photo: {e}")

    # Resume parsing and ATS scoring: the shared pipeline (src/services/ats_scoring_pipeline).
    # It never raises: a resume that cannot be read reliably comes back `needs_review`
    # instead of as a silent 0, and goes to a person rather than into the ranking.
    inputs = build_inputs_for_candidate(db, user_id=current_user.id, resume_path=resume_path)
    profile = inputs.profile
    resume_text = inputs.resume_text
    confidence = float(inputs.extraction.get("confidence") or 0.0)
    outcome = score_for_job(inputs, job)
    ats_result = outcome.result
    needs_review = outcome.parse["status"] == "needs_review"
    ats_interpretation = ({"level": "unknown", "color": "#888"} if needs_review
                          else get_ats_interpretation(ats_result.ats_score))

    # Resolve company_id with a fallback chain so multi-tenant isolation never
    # silently breaks. Use raw SQL to defeat any stale ORM identity-map state,
    # then fall back through HR profile / HR user / department if needed.
    from sqlalchemy import text as _text
    row = db.execute(
        _text("SELECT company_id, hr_id, department_id FROM job_postings WHERE id = :jid"),
        {"jid": job_id},
    ).fetchone()
    resolved_company_id = row[0] if row else None
    job_hr_id = row[1] if row else None
    job_dept_id = row[2] if row else None
    if not resolved_company_id and job_hr_id:
        hr_profile = db.query(HRProfile).filter(HRProfile.id == job_hr_id).first()
        if hr_profile:
            hr_user = db.query(User).filter(User.id == hr_profile.user_id).first()
            if hr_user:
                resolved_company_id = hr_user.company_id
    if not resolved_company_id and job_dept_id:
        dept = db.query(Department).filter(Department.id == job_dept_id).first()
        if dept:
            resolved_company_id = dept.company_id

    # Create candidate record
    candidate = Candidate(
        user_id=current_user.id,
        company_id=resolved_company_id,
        job_id=job_id,
        email=application_data.email,
        first_name=application_data.first_name,
        last_name=application_data.last_name,
        phone=application_data.phone,
        resume_path=resume_path,
        resume_text=resume_text,
        extracted_skills=json.dumps(ats_result.extracted_skills) if ats_result.extracted_skills else None,
        experience_years=ats_result.extracted_experience,
        ats_score=ats_result.ats_score,
        current_salary=application_data.current_salary,
        expected_salary=application_data.expected_salary,
        notice_period=application_data.notice_period,
        linkedin_url=application_data.linkedin_url,
        github_url=application_data.github_url,
        portfolio_url=application_data.portfolio_url,
        additional_info=application_data.additional_info,
        photo_path=photo_path
    )
    
    db.add(candidate)
    # The `(job_id, email)` check above is what produces a good message, but it cannot
    # be the whole story: two concurrent applications both read "not present" before
    # either writes, and the loser then meets uq_candidates_company_job_email. Without
    # this that race reaches the candidate as a 500 on the apply button.
    with duplicate_as_conflict(db, "You have already applied for this job. If you meant to update your application, contact the recruiter rather than applying again."):
        db.commit()
    db.refresh(candidate)
    
    # ATS result with provenance, and the first entry in its score history.
    persist_ats_result(db, candidate=candidate, job=job, outcome=outcome, event_type="scored")

    # Create stage history: applied
    stage_applied = CandidateStageHistory(
        candidate_id=candidate.id,
        from_stage=None,
        to_stage="applied",
        notes=f"Applied via enhanced career portal (Confidence: {confidence:.2f})"
    )
    db.add(stage_applied)

    # Auto-advance to ats_screening since ATS scoring is already done
    candidate.current_stage = "ats_screening"
    stage_ats = CandidateStageHistory(
        candidate_id=candidate.id,
        from_stage="applied",
        to_stage="ats_screening",
        notes=f"ATS screening completed automatically. Score: {ats_result.ats_score}/100"
    )
    db.add(stage_ats)

    # A resume that could not be read reliably goes straight to a person; it must never
    # sit below the threshold looking like a weak candidate.
    if needs_review:
        candidate.current_stage = "hr_review"
        db.add(CandidateStageHistory(
            candidate_id=candidate.id, from_stage="ats_screening", to_stage="hr_review",
            notes="Resume could not be read reliably, so it was not ranked: manual review. "
                  + " ".join(parse_quality.describe(outcome.parse["issues"])),
        ))
    elif ats_result.ats_score and float(ats_result.ats_score) >= ATS_REVIEW_THRESHOLD:
        candidate.current_stage = "hr_review"
        stage_hr = CandidateStageHistory(
            candidate_id=candidate.id,
            from_stage="ats_screening",
            to_stage="hr_review",
            notes=f"Auto-promoted to HR review (ATS score {ats_result.ats_score} >= {ATS_REVIEW_THRESHOLD} threshold)"
        )
        db.add(stage_hr)

    db.commit()

    # Fold this application into the HR talent pool so the applicant is browsable there
    # immediately. Reuses the score we just computed — no re-scoring — and never raises,
    # because a pool write must not be able to fail an application.
    from src.services.resume_pool_import import sync_pool_on_apply
    sync_pool_on_apply(db, candidate=candidate, job=job, ats_result=ats_result,
                       profile=profile, resume_text=resume_text)

    from src.services.email_service import send_application_received, resolve_sender
    from src.services.pipeline_notifications import resolve_step_dispatch
    try:
        disp = resolve_step_dispatch(db, job.company_id, "application_received", job=job)
        smtp_config = disp["smtp_config"] or resolve_sender(db, job.company_id)
        send_application_received(candidate.email, f"{candidate.first_name} {candidate.last_name}", job.title,
                                  smtp_config=smtp_config, cc=disp["cc"])
    except Exception as _e:
        logger.warning(f"application-received email to {candidate.email} failed: {_e}")

    from src.services.notification_service import create_notification
    create_notification(
        db,
        user_id=current_user.id,
        company_id=job.company_id,
        title="Application received",
        message=f"Your application for '{job.title}' was received. ATS score: {ats_result.ats_score}/100.",
        type="application_received",
        reference_type="application",
        reference_id=candidate.id,
    )

    return {
        "message": "Application submitted successfully with enhanced ATS analysis",
        "candidate_id": str(candidate.id),
        "ats_score": ats_result.ats_score,
        "ats_level": ats_interpretation['level'],
        "ats_color": ats_interpretation['color'],
        "application_stage": candidate.current_stage,
        "processing_confidence": confidence,
        "resume_needs_review": needs_review,
        "recommendations": ats_result.recommendations,
        "strengths": ats_result.strengths,
        "weaknesses": ats_result.weaknesses,
        "parsed_info": {
            "skills_count": len(ats_result.extracted_skills),
            "experience_years": ats_result.extracted_experience,
            "processing_method": "enhanced_ocr" if (resume.filename or "").endswith('.pdf') else "text_extraction"
        }
    }

@router.post("/jobs/{job_id}/quick-apply")
async def quick_apply_for_job(
    job_id: str,
    photo: UploadFile = File(..., description="Candidate photo for identity verification"),
    db: Session = Depends(get_db),
    current_user: User = Depends(AuthMiddleware.get_current_candidate)
):
    """Submit a job application using the candidate's saved profile data. Only a photo is required."""

    # Company clause required for the same reason as /apply above.
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id,
        JobPosting.company_id == current_user.company_id,
        JobPosting.status == "active"
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not active")

    # Load the candidate's extended profile
    profile = db.query(CandidateProfileExt).filter(
        CandidateProfileExt.user_id == current_user.id
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Candidate profile not found. Please complete your profile first.")

    # Resume must be saved on the profile
    if not profile.resume_path:
        raise HTTPException(status_code=400, detail="No resume found on your profile. Please upload a resume before using Quick Apply.")

    from src.services.storage import storage as _storage
    if not _storage.exists(profile.resume_path):
        raise HTTPException(status_code=400, detail="Your saved resume file could not be found. Please re-upload your resume in your profile.")

    # Duplicate application check
    existing = db.query(Candidate).filter(
        Candidate.job_id == job_id,
        Candidate.email == current_user.email
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="You have already applied for this job.")

    # Save photo
    photo_path = None
    try:
        allowed_photo_ext = ['.jpg', '.jpeg', '.png', '.webp']
        ext = Path(photo.filename).suffix.lower() if photo.filename else '.jpg'
        if ext not in allowed_photo_ext:
            raise HTTPException(status_code=400, detail="Photo must be JPG, PNG, or WEBP")
        photo_name = f"{current_user.id}_{job_id}_{uuid.uuid4().hex[:8]}{ext}"
        photo_path = f"uploads/photos/{photo_name}"
        content = await photo.read()
        from src.services.storage import storage
        storage.save_bytes(content, photo_path)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save photo: {e}")

    # Resume parsing and ATS scoring: the shared pipeline. Only "nothing at all to score"
    # is the candidate's to fix (400); a scorer failure becomes needs_review, not a 500.
    inputs = build_inputs_for_candidate(db, user_id=current_user.id, resume_path=profile.resume_path)
    if not (inputs.scoring_text or "").strip():
        raise HTTPException(status_code=400, detail="No profile data or resume text available to score. Please complete your profile or re-upload your resume.")
    resume_text = inputs.resume_text
    confidence = float(inputs.extraction.get("confidence") or 0.0)
    outcome = score_for_job(inputs, job)
    ats_result = outcome.result
    needs_review = outcome.parse["status"] == "needs_review"
    ats_interpretation = ({"level": "unknown", "color": "#888"} if needs_review
                          else get_ats_interpretation(ats_result.ats_score))

    # Derive application fields from profile
    expected_salary = None
    if profile.expected_salary_min is not None:
        try:
            expected_salary = float(profile.expected_salary_min)
        except (TypeError, ValueError):
            pass

    linkedin_url = profile.linkedin_url
    github_url = profile.github_url
    portfolio_url = profile.portfolio_url

    # Create candidate record
    candidate = Candidate(
        user_id=current_user.id,
        company_id=job.company_id,
        job_id=job_id,
        email=current_user.email,
        first_name=current_user.first_name,
        last_name=current_user.last_name,
        phone=getattr(current_user, 'phone', None),
        resume_path=profile.resume_path,
        resume_text=resume_text,
        extracted_skills=json.dumps(ats_result.extracted_skills) if ats_result.extracted_skills else None,
        experience_years=ats_result.extracted_experience,
        ats_score=ats_result.ats_score,
        expected_salary=expected_salary,
        notice_period=profile.notice_period,
        linkedin_url=linkedin_url,
        github_url=github_url,
        portfolio_url=portfolio_url,
        photo_path=photo_path
    )

    db.add(candidate)
    # The `(job_id, email)` check above is what produces a good message, but it cannot
    # be the whole story: two concurrent applications both read "not present" before
    # either writes, and the loser then meets uq_candidates_company_job_email. Without
    # this that race reaches the candidate as a 500 on the apply button.
    with duplicate_as_conflict(db, "You have already applied for this job. If you meant to update your application, contact the recruiter rather than applying again."):
        db.commit()
    db.refresh(candidate)

    # ATS result with provenance, and the first entry in its score history.
    persist_ats_result(db, candidate=candidate, job=job, outcome=outcome, event_type="scored")

    stage_applied = CandidateStageHistory(
        candidate_id=candidate.id,
        from_stage=None,
        to_stage="applied",
        notes=f"Applied via Quick Apply (Confidence: {confidence:.2f})"
    )
    db.add(stage_applied)

    candidate.current_stage = "ats_screening"
    stage_ats = CandidateStageHistory(
        candidate_id=candidate.id,
        from_stage="applied",
        to_stage="ats_screening",
        notes=f"ATS screening completed automatically. Score: {ats_result.ats_score}/100"
    )
    db.add(stage_ats)

    # A resume that could not be read reliably goes straight to a person; it must never
    # sit below the threshold looking like a weak candidate.
    if needs_review:
        candidate.current_stage = "hr_review"
        db.add(CandidateStageHistory(
            candidate_id=candidate.id, from_stage="ats_screening", to_stage="hr_review",
            notes="Resume could not be read reliably, so it was not ranked: manual review. "
                  + " ".join(parse_quality.describe(outcome.parse["issues"])),
        ))
    elif ats_result.ats_score and float(ats_result.ats_score) >= ATS_REVIEW_THRESHOLD:
        candidate.current_stage = "hr_review"
        stage_hr = CandidateStageHistory(
            candidate_id=candidate.id,
            from_stage="ats_screening",
            to_stage="hr_review",
            notes=f"Auto-promoted to HR review (ATS score {ats_result.ats_score} >= {ATS_REVIEW_THRESHOLD} threshold)"
        )
        db.add(stage_hr)

    db.commit()

    # Fold this application into the HR talent pool so the applicant is browsable there
    # immediately. Reuses the score we just computed — no re-scoring — and never raises,
    # because a pool write must not be able to fail an application.
    from src.services.resume_pool_import import sync_pool_on_apply
    sync_pool_on_apply(db, candidate=candidate, job=job, ats_result=ats_result,
                       profile=profile, resume_text=resume_text)

    from src.services.email_service import send_application_received, resolve_sender
    from src.services.pipeline_notifications import resolve_step_dispatch
    try:
        disp = resolve_step_dispatch(db, job.company_id, "application_received", job=job)
        smtp_config = disp["smtp_config"] or resolve_sender(db, job.company_id)
        send_application_received(candidate.email, f"{candidate.first_name} {candidate.last_name}", job.title,
                                  smtp_config=smtp_config, cc=disp["cc"])
    except Exception as _e:
        logger.warning(f"application-received email to {candidate.email} failed: {_e}")

    from src.services.notification_service import create_notification
    create_notification(
        db,
        user_id=current_user.id,
        company_id=job.company_id,
        title="Application received",
        message=f"Your application for '{job.title}' was received. ATS score: {ats_result.ats_score}/100.",
        type="application_received",
        reference_type="application",
        reference_id=candidate.id,
    )

    return {
        "message": "Quick Apply submitted successfully",
        "candidate_id": str(candidate.id),
        "ats_score": ats_result.ats_score,
        "ats_level": ats_interpretation['level'],
        "ats_color": ats_interpretation['color'],
        "application_stage": candidate.current_stage,
        "processing_confidence": confidence,
        "recommendations": ats_result.recommendations,
        "strengths": ats_result.strengths,
        "weaknesses": ats_result.weaknesses,
        "application_type": "quick_apply",
        "parsed_info": {
            "skills_count": len(ats_result.extracted_skills),
            "experience_years": ats_result.extracted_experience,
        }
    }


# ==================== CANDIDATE DASHBOARD ENDPOINTS ====================

@router.get("/applications", response_model=List[ApplicationResponse])
async def get_my_applications(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get current user's job applications"""
    candidates = db.query(Candidate).options(
        joinedload(Candidate.job)
    ).filter(
        Candidate.email == current_user.email,
        Candidate.company_id == current_user.company_id,
        Candidate.job_id.isnot(None)
    ).all()

    applications = []
    for candidate in candidates:
        if not candidate.job:
            continue
        # Normalise NULL current_stage so it never falls through to 'applied' on the frontend
        stage = candidate.current_stage or 'ats_screening'
        applications.append(ApplicationResponse(
            id=str(candidate.id),
            job_title=candidate.job.title,
            company="Company Name",
            stage=stage,
            ats_score=float(candidate.ats_score) if candidate.ats_score else None,
            applied_at=candidate.created_at,
            status=stage
        ))

    return applications

@router.get("/applications/{application_id}")
async def get_application_details(
    application_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get detailed information about a specific application"""
    candidate = db.query(Candidate).filter(
        Candidate.id == application_id,
        Candidate.email == current_user.email,
        Candidate.company_id == current_user.company_id,
    ).first()
    
    if not candidate:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Get ATS result if available
    ats_result = db.query(ATSResult).filter(
        ATSResult.candidate_id == candidate.id
    ).first()
    
    # Get stage history
    stage_history = db.query(CandidateStageHistory).filter(
        CandidateStageHistory.candidate_id == candidate.id
    ).order_by(CandidateStageHistory.created_at.desc()).all()
    
    if not candidate.job:
        raise HTTPException(status_code=404, detail="Application not found")

    return {
        "application_id": str(candidate.id),
        "job": {
            "id": str(candidate.job.id),
            "title": candidate.job.title,
            "description": candidate.job.description,
            "requirements": candidate.job.requirements,
            "location": candidate.job.location,
            "employment_type": candidate.job.employment_type
        },
        "candidate_info": {
            "first_name": candidate.first_name,
            "last_name": candidate.last_name,
            "email": candidate.email,
            "phone": candidate.phone,
            "current_salary": float(candidate.current_salary) if candidate.current_salary else None,
            "expected_salary": float(candidate.expected_salary) if candidate.expected_salary else None,
            "notice_period": candidate.notice_period,
            "linkedin_url": candidate.linkedin_url,
            "github_url": candidate.github_url,
            "portfolio_url": candidate.portfolio_url
        },
        "ats_result": {
            "ats_score": float(ats_result.ats_score) if ats_result else None,
            "skills_match_score": float(ats_result.skills_match_score) if ats_result else None,
            "semantic_score": float(ats_result.semantic_score) if ats_result else None,
            "experience_score": float(ats_result.experience_score) if ats_result else None,
            "keyword_score": float(ats_result.keyword_score) if ats_result else None,
            "matched_skills": ats_result.matched_skills if ats_result else [],
            "missing_skills": ats_result.missing_skills if ats_result else []
        },
        "current_stage": candidate.current_stage or 'ats_screening',
        "stage_history": [
            {
                "from_stage": stage.from_stage,
                "to_stage": stage.to_stage,
                "notes": stage.notes,
                "changed_at": stage.created_at
            }
            for stage in stage_history
        ],
        "applied_at": candidate.created_at,
        "updated_at": candidate.updated_at
    }

@router.get("/offers")
async def get_my_offers(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get offers for current user"""
    candidates = db.query(Candidate).filter(
        Candidate.email == current_user.email,
        Candidate.company_id == current_user.company_id
    ).all()
    
    offers = []
    for candidate in candidates:
        from src.models.models import Offer
        candidate_offers = db.query(Offer).filter(
            Offer.candidate_id == candidate.id
        ).all()
        
        for offer in candidate_offers:
            offers.append({
                "offer_id": str(offer.id),
                "job_title": (offer.job.title if offer.job else None) or (candidate.job.title if candidate.job else "N/A"),
                "offer_type": offer.offer_type,
                "salary": float(offer.salary),
                "bonus": float(offer.bonus) if offer.bonus else None,
                "start_date": offer.start_date.isoformat(),
                "expiry_date": offer.expiry_date.isoformat(),
                "status": offer.status,
                "sent_at": offer.sent_at.isoformat() if offer.sent_at else None,
                "responded_at": offer.responded_at.isoformat() if offer.responded_at else None
            })
    
    return {"offers": offers}


# ==================== CANDIDATE INTERVIEW ENDPOINTS ====================

@router.get("/interviews")
async def get_my_interviews(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get all interviews scheduled for this candidate"""
    # Find all candidate records for this user
    candidates = db.query(Candidate).filter(Candidate.user_id == current_user.id).all()
    candidate_ids = [c.id for c in candidates]
    # Fallback: match by email if user_id not linked
    if not candidate_ids:
        candidates = db.query(Candidate).filter(Candidate.email == current_user.email).all()
        candidate_ids = [c.id for c in candidates]
    if not candidate_ids:
        return []

    interviews = db.query(Interview).filter(
        Interview.candidate_id.in_(candidate_ids),
        Interview.company_id == current_user.company_id,
        Interview.status.in_(["SCHEDULED", "READY", "IN_PROGRESS", "COMPLETED"])
    ).order_by(Interview.scheduled_datetime.desc()).all()

    result = []
    for iv in interviews:
        # Find the candidate record for this interview
        cand = next((c for c in candidates if c.id == iv.candidate_id), None)
        # Parse security settings to expose the proctoring flags to the candidate UI.
        # Defaults matter: tests created before these flags were honoured (and drafts, whose
        # security_settings_data is NULL) must keep the stricter behaviour the exam had when
        # the client applied tab-switch detection and copy/paste blocking unconditionally.
        enable_webcam_monitoring = False
        enable_tab_switch_detection = True
        require_fullscreen = True
        copy_paste_allowed = False
        if iv.security_settings_data:
            try:
                _sec = json.loads(iv.security_settings_data)
                enable_webcam_monitoring = bool(_sec.get("enable_webcam_monitoring", False))
                enable_tab_switch_detection = bool(_sec.get("enable_tab_switch_detection", True))
                require_fullscreen = bool(_sec.get("require_fullscreen", True))
                copy_paste_allowed = bool(_sec.get("copy_paste_allowed", False))
            except (json.JSONDecodeError, TypeError, AttributeError):
                enable_webcam_monitoring = False
                enable_tab_switch_detection = True
                require_fullscreen = True
                copy_paste_allowed = False
        # Detect mode + location from notes (same convention as the HR/interviewer
        # lists). Offline interviews store "Location: <x>" in notes; fall back to the
        # job's location so the candidate always knows where to go.
        mode = "online"
        location = ""
        if iv.notes:
            if "Mode: offline" in iv.notes:
                mode = "offline"
            for line in iv.notes.split("\n"):
                if line.startswith("Location:"):
                    location = line.replace("Location:", "").strip()
        if not location and iv.job and iv.job.location:
            location = iv.job.location
        approved_acc = approved_accommodations(iv)
        if approved_acc.get("fullscreen_not_required"):
            require_fullscreen = False
        result.append({
            "id": iv.id,
            "title": iv.title,
            "job_title": iv.job_title or (iv.job.title if iv.job else "N/A"),
            "interview_type": iv.interview_type,
            "difficulty_level": iv.difficulty_level,
            "scheduled_datetime": iv.scheduled_datetime,
            "duration_minutes": iv.duration_minutes,
            "status": iv.status,
            "meeting_link": iv.meeting_link,
            "mode": mode,
            "location": location,
            "interviewer_name": iv.interviewer_name,
            "panel_members": (json.loads(iv.panel_members) if iv.panel_members else []),
            "enable_webcam_monitoring": enable_webcam_monitoring,
            "enable_tab_switch_detection": enable_tab_switch_detection,
            "require_fullscreen": require_fullscreen,
            "copy_paste_allowed": copy_paste_allowed,
            "is_scheduled_interview": bool(iv.notes and "Mode:" in iv.notes),
            "attempt_number": iv.attempt_number or 1,
            "accommodation_status": iv.accommodation_status,
            "accommodation_request": iv.accommodation_request,
            "accommodations": approved_acc or None,
            "accommodations_summary": describe_accommodations(approved_acc),
        })
    return result


@router.get("/interviews/{interview_id}/questions")
async def get_interview_questions_for_candidate(
    interview_id: int,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get approved questions for a scheduled interview (candidate view)"""
    # Verify candidate owns this interview
    candidates = db.query(Candidate).filter(Candidate.user_id == current_user.id).all()
    candidate_ids = [c.id for c in candidates]
    if not candidate_ids:
        candidates = db.query(Candidate).filter(Candidate.email == current_user.email).all()
        candidate_ids = [c.id for c in candidates]

    if candidate_ids:
        interview = db.query(Interview).filter(
            Interview.id == interview_id,
            Interview.candidate_id.in_(candidate_ids)
        ).first()
    else:
        interview = None
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    if interview.status == "COMPLETED":
        raise HTTPException(status_code=403, detail="Test already submitted. You cannot retake this test.")

    if interview.status not in ("READY", "IN_PROGRESS", "SCHEDULED"):
        raise HTTPException(status_code=400, detail="Interview is not available yet")

    # Get only approved questions
    from src.models.question_models import Question as InterviewQuestion
    questions = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id,
        InterviewQuestion.is_approved == True
    ).all()

    return [
        {
            "id": str(q.id),
            "question_text": q.question_text,
            "question_type": q.question_type or "multiple_choice",
            "difficulty_level": q.difficulty_level or "Medium",
            "options": q.options,
            "time_limit_minutes": q.time_limit_minutes or 5,
            # MCQ = 1 mark each; coding keeps its stored weighting.
            "points": (q.points or 10) if (q.question_type or "").lower() == "coding" else 1,
        }
        for q in questions
    ]


class SubmitAnswerItem(BaseModel):
    question_id: str
    # MCQ choice, or the candidate's code for a coding question (the judge's 64 KB bound).
    selected_option: Optional[str] = Field(None, max_length=64 * 1024)
    language: Optional[str] = None   # for coding answers: the editor language

class SubmitInterviewRequest(BaseModel):
    answers: List[SubmitAnswerItem] = []


def _resolve_owned_interview(interview_id: int, current_user: User, db: Session):
    """Return (interview, candidate_id) if it is this candidate's own, else (None, None)."""
    from src.services.interview_access import candidate_owns_interview
    interview = candidate_owns_interview(db, current_user, interview_id)
    return interview, (interview.candidate_id if interview else None)


@router.post("/interviews/{interview_id}/voice-answer")
async def submit_voice_answer(
    interview_id: int,
    question_id: str = Form(...),
    transcript: str = Form(""),
    duration_seconds: float = Form(0.0),
    audio: UploadFile = File(...),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """
    Receive a spoken answer to a `communication` question: store the recording,
    measure delivery metrics, and upsert the CommunicationAnswer. Returns the
    score breakdown so the candidate gets immediate confirmation.
    """
    from src.models.question_models import Question as InterviewQuestion, CommunicationAnswer
    from src.services.communication_scoring_service import score_communication

    interview, candidate_id = _resolve_owned_interview(interview_id, current_user, db)
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    if interview.status == "COMPLETED":
        raise HTTPException(status_code=403, detail="Test already submitted.")

    question = db.query(InterviewQuestion).filter(
        InterviewQuestion.id == question_id,
        InterviewQuestion.interview_id == interview_id,
    ).first()
    if not question:
        raise HTTPException(status_code=404, detail="Question not found for this interview")

    # Save the recording (WAV) for HR playback — served under /uploads.
    ext = Path(audio.filename).suffix.lower() if audio.filename else ".wav"
    if ext not in (".wav", ".webm", ".ogg", ".mp3", ".m4a"):
        ext = ".wav"
    filename = f"{interview_id}_{question_id}_{uuid.uuid4().hex[:8]}{ext}"
    audio_path = f"uploads/voice_answers/{filename}"
    content = await audio.read()
    from src.services.storage import storage, as_local_path
    storage.save_bytes(content, audio_path)

    # Measure delivery metrics (needs a real local file).
    with as_local_path(audio_path) as _audio_local:
        result = score_communication(_audio_local, transcript, duration_seconds)

    # Upsert one row per (interview, question).
    record = db.query(CommunicationAnswer).filter(
        CommunicationAnswer.interview_id == interview_id,
        CommunicationAnswer.question_id == question_id,
    ).first()
    if not record:
        record = CommunicationAnswer(interview_id=interview_id, question_id=question_id)
        db.add(record)
    else:
        # Replace the previous recording file on re-record. Must go through the
        # storage layer: audio_path is a storage KEY, not a filesystem path, so
        # os.remove() would resolve it against the process working directory and
        # miss the file entirely (and never worked at all on the GCS backend).
        if record.audio_path and record.audio_path != audio_path:
            storage.delete(record.audio_path)

    record.candidate_id = candidate_id or interview.candidate_id
    record.audio_path = audio_path
    record.transcript = transcript
    record.duration_seconds = result.duration_seconds
    record.words_per_minute = result.words_per_minute
    record.filler_count = result.filler_count
    record.filler_ratio = result.filler_ratio
    record.pause_count = result.pause_count
    record.speaking_ratio = result.speaking_ratio
    record.acoustic_confidence = result.acoustic_confidence
    record.communication_score = result.communication_score
    record.breakdown_json = json.dumps(result.to_breakdown())
    db.commit()

    return {
        "message": "Voice answer recorded",
        "question_id": question_id,
        "communication_score": result.communication_score,
        "transcript": transcript,
        "breakdown": result.to_breakdown(),
        "audio_url": "/" + audio_path,
    }


def _own_interview(db: Session, current_user: User, interview_id: int) -> Interview:
    """The candidate's own interview, or 404. Same ownership rule as the questions endpoint."""
    candidate_ids = [c.id for c in db.query(Candidate).filter(Candidate.user_id == current_user.id).all()]
    if not candidate_ids:
        candidate_ids = [c.id for c in db.query(Candidate).filter(Candidate.email == current_user.email).all()]
    interview = db.query(Interview).filter(
        Interview.id == interview_id, Interview.candidate_id.in_(candidate_ids)
    ).first() if candidate_ids else None
    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")
    return interview


class ExplanationRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=2000)


MAX_EXPLANATIONS_PER_TEST = 5


@router.post("/interviews/{interview_id}/explanation")
async def submit_explanation(
    interview_id: int,
    body: ExplanationRequest,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """The candidate's own account of their test, shown to the reviewers alongside the
    proctoring record. For a test that ended early, or anything the candidate wants the
    hiring team to know when they look at the flags."""
    from src.models.models import AssessmentExplanation
    interview = _own_interview(db, current_user, interview_id)
    if (interview.status or "").upper() != "COMPLETED":
        raise HTTPException(status_code=409, detail="You can add an explanation once the test has been submitted.")
    text = body.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write a few words first.")
    count = db.query(AssessmentExplanation).filter(
        AssessmentExplanation.interview_id == interview.id,
        AssessmentExplanation.candidate_user_id == current_user.id,
    ).count()
    if count >= MAX_EXPLANATIONS_PER_TEST:
        raise HTTPException(status_code=409, detail="You have already added the maximum number of explanations for this test.")
    row = AssessmentExplanation(
        company_id=interview.company_id, interview_id=interview.id,
        candidate_user_id=current_user.id, text=text, created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return {"id": row.id, "text": row.text, "created_at": row.created_at.isoformat()}


@router.get("/interviews/{interview_id}/explanations")
async def list_my_explanations(
    interview_id: int,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    from src.models.models import AssessmentExplanation
    interview = _own_interview(db, current_user, interview_id)
    rows = db.query(AssessmentExplanation).filter(
        AssessmentExplanation.interview_id == interview.id,
        AssessmentExplanation.candidate_user_id == current_user.id,
    ).order_by(AssessmentExplanation.created_at).all()
    return [{"id": r.id, "text": r.text, "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows]


class AccommodationRequestBody(BaseModel):
    text: str = Field(..., min_length=10, max_length=2000)


@router.post("/interviews/{interview_id}/accommodation-request")
async def request_accommodation(
    interview_id: int,
    body: AccommodationRequestBody,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Ask the hiring team for an adjustment (extra time, microphone monitoring off...)
    before starting. HR approves specific settings; nothing changes until they do."""
    interview = _own_interview(db, current_user, interview_id)
    if (interview.status or "").upper() in ("COMPLETED", "IN_PROGRESS"):
        raise HTTPException(status_code=409, detail="Adjustments must be requested before the test starts.")
    if interview.accommodation_status == "approved":
        raise HTTPException(status_code=409, detail="Your adjustments are already approved. Contact your recruiter to change them.")
    interview.accommodation_request = body.text.strip()
    interview.accommodation_status = "requested"
    interview.updated_at = datetime.utcnow()
    db.commit()
    return {"accommodation_status": "requested", "accommodation_request": interview.accommodation_request}


def _proctoring_termination_reason(db: Session, interview_id: int) -> Optional[str]:
    """The flag that ended this test early, or None if proctoring didn't end it.

    The server marks the session terminated (and records TEST_TERMINATED with its reason)
    before the client auto-submits, so by the time submit runs the record is in place.
    """
    from src.models.models import ProctoringSession, ProctoringViolation
    session = (db.query(ProctoringSession)
               .filter(ProctoringSession.test_id == interview_id,
                       ProctoringSession.status == "terminated")
               .order_by(ProctoringSession.started_at.desc()).first())
    if not session:
        return None
    marker = (db.query(ProctoringViolation)
              .filter(ProctoringViolation.session_id == session.id,
                      ProctoringViolation.violation_type == "TEST_TERMINATED")
              .order_by(ProctoringViolation.timestamp.desc()).first())
    details = marker.details if marker else None
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except ValueError:
            details = None
    return (details or {}).get("reason") or "PROCTORING"


@router.post("/interviews/{interview_id}/submit")
async def submit_interview(
    interview_id: int,
    body: Optional[SubmitInterviewRequest] = None,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Submit interview answers, calculate score, and mark as completed"""
    from src.models.question_models import Question as InterviewQuestion, CommunicationAnswer

    # Must be this candidate's own interview. There is deliberately no fallback for an
    # account without Candidate rows (see src/services/interview_access.py).
    from src.services.interview_access import candidate_owns_interview
    interview = candidate_owns_interview(db, current_user, interview_id)

    if not interview:
        raise HTTPException(status_code=404, detail="Interview not found")

    # Block re-submission once test is already completed
    if interview.status == "COMPLETED":
        raise HTTPException(
            status_code=403,
            detail="Test already submitted. You cannot retake this test."
        )

    # Calculate score from answers
    score = 0
    total_marks = 0
    scored_answers = []

    # The paper the candidate was served (the same rows GET .../questions returns).
    # Its marks are the denominator whatever the client sends, so leaving out the
    # answers you're unsure of can't raise the percentage.
    served = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == interview_id,
        InterviewQuestion.is_approved == True,
    ).all()
    q_map = {q.id: q for q in served}

    def _marks(q):
        # Every MCQ is worth exactly 1 mark; coding keeps its stored weighting.
        # (A NULL/0 points value must NOT silently become 10 — that inflated the
        # denominator, e.g. a 40-question test showing "/49".)
        return (q.points or 10) if (q.question_type or "").lower() == "coding" else 1

    # One answer per question (the last one sent wins), so repeating a question id
    # can't score it twice or re-run the judge for it.
    answers = list({a.question_id: a for a in (body.answers if body else [])}.values())
    answered_ids = {a.question_id for a in answers if a.question_id in q_map}
    for q in served:
        if q.id not in answered_ids and (q.question_type or "").lower() != "communication":
            total_marks += _marks(q)          # unanswered: counts, scores 0

    if answers:
        for ans in answers:
            q = q_map.get(ans.question_id)
            if not q:
                continue
            # Communication questions are scored from their uploaded recording below.
            if (q.question_type or "").lower() == "communication":
                continue
            points = _marks(q)
            total_marks += points

            # Coding questions with test cases → judge the candidate's code
            # against ALL cases (HackerRank-style), partial credit by pass ratio.
            if q.question_type == 'coding' and getattr(q, 'test_cases', None):
                from src.judge.interview_grading import grade_interview_question
                # Prefer the candidate's editor language; fall back to the question's
                # HR-assigned language, then python.
                answer_lang = ans.language or getattr(q, 'language', None) or 'python'
                # Off the event loop: judging waits seconds on the worker, and every
                # other request on this API worker would wait with it.
                graded = await asyncio.to_thread(
                    grade_interview_question, q, answer_lang, ans.selected_option or '')
                if graded.get('unavailable'):
                    # Judge was down at submit time. Don't silently award 0: drop this
                    # question from the denominator for now and flag it for deferred
                    # re-grade (POST /api/v1/judge/regrade-pending) so an outage doesn't
                    # penalise the candidate. The points are folded back in on re-grade.
                    total_marks -= points
                    scored_answers.append({
                        "question_id": ans.question_id,
                        "selected_option": ans.selected_option,
                        "language": ans.language,
                        "pending_regrade": True,
                        "is_correct": False,
                        "verdict": None,
                        "points": 0,
                        "max_points": points,
                        "message": graded.get('message'),
                    })
                    continue
                awarded = graded.get('points_awarded', 0) or 0
                score += awarded
                scored_answers.append({
                    "question_id": ans.question_id,
                    "selected_option": ans.selected_option,
                    "language": ans.language,
                    "is_correct": graded.get('verdict') == 'AC',
                    "verdict": graded.get('verdict'),
                    "passed": graded.get('passed'),
                    "total": graded.get('total'),
                    "points": awarded,
                    "max_points": points,
                    "message": graded.get('message'),
                })
                continue

            is_correct = False
            if ans.selected_option and q.correct_answer:
                correct = q.correct_answer.strip()
                selected = ans.selected_option.strip()

                # Parse options list, splitting any merged "A) .. B) .." blob so
                # the letter->index mapping lines up with what the candidate saw.
                from src.mcq_options import normalize_mcq_options
                options = normalize_mcq_options(q.options)

                if len(correct) == 1 and correct.upper() in 'ABCD' and options:
                    # correct_answer is a letter like "A","B","C","D"
                    correct_idx = ord(correct.upper()) - ord('A')
                    # Check if selected text matches the option at that index
                    if correct_idx < len(options):
                        correct_text = str(options[correct_idx]).strip()
                        is_correct = selected.lower() == correct_text.lower()
                    # Also check if selected is the same letter
                    if not is_correct:
                        is_correct = selected.upper() == correct.upper()
                    # Also check by index: find which option matches selected text
                    if not is_correct:
                        for i, opt in enumerate(options):
                            if selected.lower() == str(opt).strip().lower():
                                selected_letter = chr(ord('A') + i)
                                is_correct = selected_letter == correct.upper()
                                break
                else:
                    # Direct text comparison
                    is_correct = selected.lower() == correct.lower()

            if is_correct:
                score += points
            scored_answers.append({
                "question_id": ans.question_id,
                "selected_option": ans.selected_option,
                "is_correct": is_correct,
                "points": points
            })

    # Merge communication (voice) answers: scored from their uploaded recordings.
    comm_answers = db.query(CommunicationAnswer).filter(
        CommunicationAnswer.interview_id == interview_id
    ).all()
    if comm_answers:
        comm_q_points = {
            q.id: (q.points or 10)
            for q in db.query(InterviewQuestion).filter(
                InterviewQuestion.interview_id == interview_id
            ).all()
        }
        for rec in comm_answers:
            points = comm_q_points.get(rec.question_id, 10)
            comm_score = rec.communication_score or 0.0
            awarded = round(points * comm_score / 100.0, 2)
            total_marks += points
            score += awarded
            try:
                breakdown = json.loads(rec.breakdown_json) if rec.breakdown_json else {}
            except (ValueError, TypeError):
                breakdown = {}
            scored_answers.append({
                "question_id": rec.question_id,
                "type": "communication",
                "transcript": rec.transcript,
                "communication_score": comm_score,
                "points": points,
                "awarded": awarded,
                "audio_url": ("/" + rec.audio_path) if rec.audio_path else None,
                "breakdown": breakdown,
            })

    interview.status = "COMPLETED"
    interview.test_score = score
    interview.total_marks = total_marks if total_marks > 0 else None
    interview.answers_json = json.dumps(scored_answers) if scored_answers else None
    interview.updated_at = datetime.utcnow()

    if interview.candidate_id:
        cand = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
        if cand and cand.current_stage not in ("interview_scheduled", "offer_sent", "hired", "rejected"):
            prev = cand.current_stage
            cand.current_stage = "test_completed"
            notes = f"MCQ test submitted. Score: {score}/{total_marks if total_marks > 0 else '?'}"
            ended_early = _proctoring_termination_reason(db, interview.id)
            if ended_early:
                # A test the proctor ended early auto-submits through this same endpoint,
                # and used to look exactly like a normal submission. Say so, and say that
                # it is waiting on a person — the score reflects a shortened attempt.
                notes += (f". Ended early by proctoring ({ended_early.replace('_', ' ').lower()}); "
                          "the score covers answers up to that point. Pending proctoring review.")
            db.add(CandidateStageHistory(
                candidate_id=cand.id,
                from_stage=prev,
                to_stage="test_completed",
                changed_by_id=current_user.id,
                notes=notes,
            ))

    db.commit()

    return {
        "message": "Interview submitted successfully",
        "score": score,
        "total_marks": total_marks,
        "correct": sum(1 for a in scored_answers if a.get("is_correct")),
        "total_questions": len(scored_answers)
    }

# ==================== PROFILE ENDPOINTS ====================

@router.get("/profile")
async def get_profile(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Return full extended candidate profile (all sections)."""
    profile = _get_or_create_profile(current_user.id, db)
    return _serialize_profile(current_user, profile)


class ProfileUpdate(BaseModel):
    # Core user fields
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    gender: Optional[str] = None
    date_of_birth: Optional[str] = None

    @field_validator("first_name", "last_name")
    @classmethod
    def _name_not_blank(cls, v):
        """A name may be left out of the payload, but never emptied.

        first_name is NOT NULL on the model, but "" satisfies that in MySQL, so an edit
        form that posted an empty field permanently erased the person's name. Whitespace
        is stripped first so "   " is not a loophole.
        """
        if v is None:
            return v
        v = v.strip()
        if not v:
            raise ValueError("Name cannot be blank")
        return v

    # Extended personal
    alternate_phone: Optional[str] = None
    nationality: Optional[str] = None
    marital_status: Optional[str] = None
    languages: Optional[List[str]] = None


@router.put("/profile")
async def update_profile(
    data: ProfileUpdate,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Update core + extended personal info fields."""
    profile = _get_or_create_profile(current_user.id, db)

    if data.first_name is not None:
        current_user.first_name = data.first_name
    if data.last_name is not None:
        current_user.last_name = data.last_name
    if data.phone is not None:
        current_user.phone = data.phone
    if data.gender is not None:
        if data.gender not in ("male", "female", "other"):
            raise HTTPException(status_code=400, detail="Gender must be male, female, or other")
        current_user.gender = data.gender
    if data.date_of_birth is not None:
        from datetime import datetime as dt
        current_user.date_of_birth = dt.strptime(data.date_of_birth, "%Y-%m-%d").date()
    if data.alternate_phone is not None:
        profile.alternate_phone = data.alternate_phone
    if data.nationality is not None:
        profile.nationality = data.nationality
    if data.marital_status is not None:
        profile.marital_status = data.marital_status
    if data.languages is not None:
        import json as _json
        profile.languages = _json.dumps(data.languages)

    db.commit()
    db.refresh(current_user)
    db.refresh(profile)
    return {"message": "Profile updated successfully"}


class CoverLetterRequest(BaseModel):
    job_id: str
    tone: Optional[str] = "professional"  # professional | enthusiastic | concise


@router.post("/cover-letter/generate")
async def generate_cover_letter(
    data: CoverLetterRequest,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Generate an AI-drafted cover letter tailored to a job, from the candidate's
    profile. Returns prose for the candidate to edit before applying — nothing is
    persisted here. Returns 503 if the AI service (Ollama) is unavailable.

    Scoped to the caller's company and to `active` jobs: the job's title,
    description and requirements are fed into the prompt below and come back in
    the generated prose, so an unfiltered lookup would hand any candidate the
    contents of any job in any tenant."""
    job = db.query(JobPosting).filter(
        JobPosting.id == data.job_id,
        JobPosting.company_id == current_user.company_id,
        JobPosting.status == "active",
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    profile = _get_or_create_profile(current_user.id, db)
    serialized = _serialize_profile(current_user, profile)

    company_name = job.company.name if getattr(job, "company", None) else ""

    config = {
        "candidate_name": (serialized.get("first_name") or "").strip() or "the candidate",
        "headline": serialized.get("headline") or "",
        "about": serialized.get("about") or "",
        "skills": [s.get("skill_name") for s in serialized.get("skills", []) if s.get("skill_name")],
        "work_experience": serialized.get("work_experiences", []),
        "job_title": job.title,
        "company_name": company_name,
        "job_description": " ".join(filter(None, [job.description, job.requirements])),
        "tone": data.tone,
    }

    from src.prompt_builder import prompt_builder
    from src.services.gemini_service import gemini_service

    prompt = prompt_builder.build_cover_letter_prompt(config)
    try:
        cover_letter = await gemini_service.generate_text(prompt)
    except Exception as e:
        logger.warning(f"Cover letter generation failed: {e}")
        raise HTTPException(
            status_code=503,
            detail="AI cover letter generation is temporarily unavailable. Please write your cover note manually.",
        )

    return {"cover_letter": cover_letter, "model": gemini_service.model}


# ==================== NOTIFICATIONS ====================

@router.get("/notifications")
async def get_my_notifications(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """List the candidate's most recent notifications (newest first)."""
    from src.models.models import Notification
    notifs = db.query(Notification).filter(
        Notification.user_id == current_user.id
    ).order_by(Notification.created_at.desc()).limit(50).all()
    return [
        {
            "id": n.id,
            "title": n.title,
            "message": n.message,
            "type": n.type,
            "reference_type": n.reference_type,
            "reference_id": n.reference_id,
            "is_read": n.is_read,
            "created_at": n.created_at,
        }
        for n in notifs
    ]


@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Mark a single notification as read (must belong to the candidate)."""
    from src.models.models import Notification
    notif = db.query(Notification).filter(
        Notification.id == notification_id,
        Notification.user_id == current_user.id,
    ).first()
    if not notif:
        raise HTTPException(status_code=404, detail="Notification not found")
    notif.is_read = True
    db.commit()
    return {"message": "Notification marked as read"}


@router.put("/notifications/read-all")
async def mark_all_notifications_read(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db),
):
    """Mark all of the candidate's notifications as read."""
    from src.models.models import Notification
    db.query(Notification).filter(
        Notification.user_id == current_user.id,
        Notification.is_read == False,
    ).update({"is_read": True})
    db.commit()
    return {"message": "All notifications marked as read"}


@router.get("/gender-stats")
async def get_gender_stats(
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get gender distribution of candidates within the caller's company."""
    # Tenant isolation: scope to the caller's company. The auto-filter
    # (src/tenancy/events.py) catches this too, but we keep the explicit
    # predicate so a reader sees the intent without reading the ORM hook.
    gender_counts = db.query(
        User.gender,
        func.count(User.id).label('count')
    ).filter(
        User.company_id == current_user.company_id,
        User.role == "candidate",
        User.gender.in_(["male", "female"])
    ).group_by(User.gender).all()

    return [{"gender": gender.capitalize(), "count": count} for gender, count in gender_counts]
