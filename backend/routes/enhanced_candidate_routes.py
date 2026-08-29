"""
🎓 ENHANCED CANDIDATE API ROUTES
Advanced career page with OCR support, enhanced ATS scoring, and detailed candidate analysis
"""

from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from src.config.database_config_mysql import get_db
from src.auth import get_current_user, AuthMiddleware
from src.models.models import (
    User, JobPosting, Candidate, ATSResult, CandidateStageHistory,
    Department
)
from src.services.enhanced_ats_service import (
    EnhancedATSScoreResult, EnhancedResumeParseResult,
    enhanced_parse_resume_file, enhanced_score_candidate_ats,
    get_enhanced_ats_interpretation
)
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime
from pathlib import Path
import uuid
import os
import json
import logging
from src.services.job_dedup import duplicate_as_conflict

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/candidate", tags=["Enhanced Candidates"])

# ==================== ENHANCED PYDANTIC MODELS ====================

class EnhancedJobPostingResponse(BaseModel):
    id: str
    title: str
    description: str
    requirements: str
    department: str
    location: str
    employment_type: str
    salary_min: Optional[float]
    salary_max: Optional[float]
    experience_years: int
    required_skills: List[str]
    required_education: Optional[str] = "bachelor"
    status: str
    created_at: datetime
    application_count: Optional[int] = 0
    average_ats_score: Optional[float] = None

class EnhancedCandidateApplication(BaseModel):
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

class EnhancedApplicationResponse(BaseModel):
    id: str
    job_title: str
    company: str
    stage: str
    ats_score: Optional[float]
    ats_level: Optional[str]
    ats_color: Optional[str]
    applied_at: datetime
    status: str
    recommendations: List[str] = []
    strengths: List[str] = []
    weaknesses: List[str] = []

class JobApplicationStats(BaseModel):
    job_id: str
    total_applications: int
    average_ats_score: Optional[float]
    applications_by_stage: Dict[str, int]
    recent_applications: List[Dict[str, Any]]

class CareerPageResponse(BaseModel):
    jobs: List[EnhancedJobPostingResponse]
    total_jobs: int
    page: int
    per_page: int
    has_next: bool
    has_prev: bool

# ==================== HELPER FUNCTIONS ====================

def create_enhanced_job_response(job: JobPosting, db: Session) -> EnhancedJobPostingResponse:
    """Create enhanced job posting response with application stats"""
    # Get application statistics
    app_stats = db.query(
        func.count(Candidate.id).label('application_count'),
        func.avg(Candidate.ats_score).label('avg_ats_score')
    ).filter(Candidate.job_id == job.id).first()
    
    return EnhancedJobPostingResponse(
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
        required_skills=job.required_skills or [],
        required_education=getattr(job, 'required_education', 'bachelor'),
        status=job.status,
        created_at=job.created_at,
        application_count=app_stats.application_count if app_stats else 0,
        average_ats_score=float(app_stats.avg_ats_score) if app_stats and app_stats.avg_ats_score else None
    )

def save_enhanced_resume_file(resume: UploadFile, candidate_email: str, job_id: str) -> str:
    """Save resume file with enhanced naming and validation; returns its storage key"""
    from src.services.storage import storage

    # Generate unique filename with timestamp
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    file_extension = resume.filename.split('.')[-1].lower()
    unique_filename = f"{candidate_email}_{job_id}_{timestamp}_{uuid.uuid4().hex[:8]}.{file_extension}"
    key = f"resumes/{unique_filename}"

    # Validate file size (10MB limit)
    content = resume.file.read()
    if len(content) > 10 * 1024 * 1024:  # 10MB
        raise HTTPException(
            status_code=413,
            detail="File too large. Maximum size is 10MB"
        )

    storage.save_bytes(content, key)
    return key

def create_enhanced_application_response(candidate, ats_result: Optional[EnhancedATSScoreResult]) -> EnhancedApplicationResponse:
    """Create enhanced application response with ATS details"""
    ats_interpretation = None
    if ats_result:
        ats_interpretation = get_enhanced_ats_interpretation(ats_result.ats_score)
    
    return EnhancedApplicationResponse(
        id=str(candidate.id),
        job_title=candidate.job.title if candidate.job else "N/A",
        company="Company Name",
        stage=candidate.current_stage,
        ats_score=ats_result.ats_score if ats_result else None,
        ats_level=ats_interpretation['level'] if ats_interpretation else None,
        ats_color=ats_interpretation['color'] if ats_interpretation else None,
        applied_at=candidate.created_at,
        status=candidate.current_stage,
        recommendations=ats_result.recommendations if ats_result else [],
        strengths=ats_result.strengths if ats_result else [],
        weaknesses=ats_result.weaknesses if ats_result else []
    )

# ==================== ENHANCED JOB BROWSING ENDPOINTS ====================

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
    db: Session = Depends(get_db)
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
    # Build base query
    query = db.query(JobPosting).filter(JobPosting.status == "active")
    
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
    job_responses = [create_enhanced_job_response(job, db) for job in jobs]
    
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

@router.get("/jobs/{job_id}", response_model=EnhancedJobPostingResponse)
async def get_job_details_enhanced(
    job_id: str,
    db: Session = Depends(get_db)
):
    """Get enhanced job details with application statistics"""
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    return create_enhanced_job_response(job, db)

@router.get("/jobs/{job_id}/stats", response_model=JobApplicationStats)
async def get_job_application_stats(
    job_id: str,
    db: Session = Depends(get_db)
):
    """
    Get detailed application statistics for a specific job
    
    Returns application count, average ATS score,
    stage distribution, and recent applications
    """
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    # Get application statistics
    app_stats = db.query(
        func.count(Candidate.id).label('total_applications'),
        func.avg(Candidate.ats_score).label('avg_ats_score')
    ).filter(Candidate.job_id == job_id).first()
    
    # Get applications by stage
    stage_stats = db.query(
        Candidate.current_stage,
        func.count(Candidate.id).label('count')
    ).filter(Candidate.job_id == job_id).group_by(Candidate.current_stage).all()
    
    applications_by_stage = {stage: count for stage, count in stage_stats}
    
    # Get recent applications (last 10)
    recent_applications = db.query(Candidate).filter(
        Candidate.job_id == job_id
    ).order_by(desc(Candidate.created_at)).limit(10).all()
    
    recent_apps_data = []
    for app in recent_applications:
        recent_apps_data.append({
            "candidate_id": str(app.id),
            "name": f"{app.first_name} {app.last_name}",
            "email": app.email,
            "ats_score": float(app.ats_score) if app.ats_score else None,
            "stage": app.current_stage,
            "applied_at": app.created_at
        })
    
    return JobApplicationStats(
        job_id=job_id,
        total_applications=app_stats.total_applications if app_stats else 0,
        average_ats_score=float(app_stats.avg_ats_score) if app_stats and app_stats.avg_ats_score else None,
        applications_by_stage=applications_by_stage,
        recent_applications=recent_apps_data
    )

# ==================== ENHANCED APPLICATION ENDPOINTS ====================

@router.post("/jobs/{job_id}/apply")
async def apply_for_job_enhanced(
    job_id: str,
    resume: UploadFile = File(...),
    cover_letter: Optional[UploadFile] = File(None),
    application: str = Form(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
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
    - 7-factor ATS scoring
    - Detailed candidate analysis
    - Personalized recommendations
    """
    # Check if job exists and is active
    job = db.query(JobPosting).filter(
        JobPosting.id == job_id, 
        JobPosting.status == "active"
    ).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found or not active")
    
    # Parse application data
    try:
        application_data = json.loads(application)
        application_data = EnhancedCandidateApplication(**application_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid application data: {str(e)}")
    
    # Check if already applied
    existing_application = db.query(Candidate).filter(
        Candidate.job_id == job_id,
        Candidate.email == application_data.email
    ).first()
    if existing_application:
        raise HTTPException(status_code=400, detail="Already applied for this job")
    
    # Validate resume file
    allowed_extensions = ['.pdf', '.docx', '.doc']
    file_extension = Path(resume.filename).suffix.lower()
    
    if file_extension not in allowed_extensions:
        raise HTTPException(
            status_code=400, 
            detail=f"Unsupported file format. Allowed formats: {', '.join(allowed_extensions)}"
        )
    
    # Save resume file
    try:
        resume_path = save_enhanced_resume_file(resume, application_data.email, job_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save resume: {str(e)}")
    
    # Handle cover letter if provided
    cover_letter_path = None
    cover_letter_text = None
    if cover_letter:
        try:
            from src.services.storage import storage, as_local_path

            cover_extension = Path(cover_letter.filename).suffix.lower()
            if cover_extension not in ['.pdf', '.docx', '.doc', '.txt']:
                raise HTTPException(status_code=400, detail="Unsupported cover letter format")

            cover_filename = f"{application_data.email}_{job_id}_{uuid.uuid4().hex[:8]}{cover_extension}"
            cover_letter_path = f"cover_letters/{cover_filename}"

            content = cover_letter.file.read()
            storage.save_bytes(content, cover_letter_path)

            # Extract text from cover letter (needs a real local path for pdf/docx)
            if cover_extension == '.pdf':
                from src.services.enhanced_ats_service import EnhancedATSScoringService
                ats_service = EnhancedATSScoringService()
                with as_local_path(cover_letter_path) as _cl:
                    cover_letter_text, _ = ats_service.extract_text_from_pdf_enhanced(_cl)
            elif cover_extension in ['.docx', '.doc']:
                ats_service = EnhancedATSScoringService()
                with as_local_path(cover_letter_path) as _cl:
                    cover_letter_text, _ = ats_service.extract_text_from_docx(_cl)
            else:
                cover_letter_text = content.decode('utf-8')
                
        except Exception as e:
            logger.warning(f"Failed to process cover letter: {e}")
            cover_letter_text = None
    
    # Enhanced resume parsing and ATS scoring
    try:
        # Parse resume with enhanced extraction (needs a real local path)
        from src.services.storage import as_local_path
        with as_local_path(resume_path) as _resume_local:
            resume_parse_result = enhanced_parse_resume_file(_resume_local)
        
        # Calculate enhanced ATS score
        required_education = getattr(job, 'required_education', 'bachelor')
        ats_result = enhanced_score_candidate_ats(
            resume_text=resume_parse_result.text,
            job_description=job.description + " " + job.requirements,
            required_skills=job.required_skills or [],
            required_experience=job.experience_years,
            required_education=required_education
        )
        
    except Exception as e:
        logger.error(f"Enhanced ATS scoring failed: {e}")
        # Fallback to basic scoring
        from ats_service import score_candidate_ats
        ats_result = score_candidate_ats(
            resume_parse_result.text,
            job.description + " " + job.requirements,
            job.required_skills or [],
            job.experience_years
        )
        # Convert to enhanced format
        ats_result = EnhancedATSScoreResult(
            ats_score=ats_result.ats_score,
            skills_match_score=ats_result.skills_match_score,
            semantic_score=ats_result.semantic_score,
            experience_score=ats_result.experience_score,
            keyword_score=ats_result.keyword_score,
            education_score=0.0,
            certification_score=0.0,
            project_score=0.0,
            matched_skills=ats_result.matched_skills,
            missing_skills=ats_result.missing_skills,
            extracted_skills=ats_result.extracted_skills,
            extracted_experience=ats_result.extracted_experience,
            education_level="Not specified",
            certifications=[],
            confidence_score=0.5,
            scoring_breakdown={},
            recommendations=[],
            strengths=[],
            weaknesses=[]
        )
    
    # Create candidate record
    candidate = Candidate(
        user_id=current_user.id,
        job_id=job_id,
        email=application_data.email,
        first_name=application_data.first_name,
        last_name=application_data.last_name,
        phone=application_data.phone,
        resume_path=resume_path,
        resume_text=resume_parse_result.text,
        extracted_skills=resume_parse_result.skills,
        experience_years=resume_parse_result.experience_years,
        ats_score=ats_result.ats_score,
        current_salary=application_data.current_salary,
        expected_salary=application_data.expected_salary,
        notice_period=application_data.notice_period,
        linkedin_url=application_data.linkedin_url,
        github_url=application_data.github_url,
        portfolio_url=application_data.portfolio_url,
        additional_info=application_data.additional_info
    )
    
    db.add(candidate)
    # The `(job_id, email)` check above is what produces a good message, but it cannot
    # be the whole story: two concurrent applications both read "not present" before
    # either writes, and the loser then meets uq_candidates_company_job_email. Without
    # this that race reaches the candidate as a 500 on the apply button.
    with duplicate_as_conflict(db, "You have already applied for this job. If you meant to update your application, contact the recruiter rather than applying again."):
        db.commit()
    db.refresh(candidate)
    
    # Create enhanced ATS result record
    from src.models.models import ATSResult
    ats_record = ATSResult(
        candidate_id=candidate.id,
        job_id=job_id,
        skills_match_score=ats_result.skills_match_score,
        semantic_score=ats_result.semantic_score,
        experience_score=ats_result.experience_score,
        keyword_score=ats_result.keyword_score,
        ats_score=ats_result.ats_score,
        matched_skills=ats_result.matched_skills,
        missing_skills=ats_result.missing_skills,
        scoring_breakdown=ats_result.scoring_breakdown
    )
    
    db.add(ats_record)
    
    # Create stage history record
    stage_history = CandidateStageHistory(
        candidate_id=candidate.id,
        from_stage=None,
        to_stage="applied",
        notes=f"Applied via enhanced career portal (Confidence: {resume_parse_result.confidence_score:.2f})"
    )
    
    db.add(stage_history)
    db.commit()
    
    return {
        "message": "Application submitted successfully with enhanced ATS analysis",
        "candidate_id": str(candidate.id),
        "ats_score": ats_result.ats_score,
        "ats_level": get_enhanced_ats_interpretation(ats_result.ats_score)['level'],
        "ats_color": get_enhanced_ats_interpretation(ats_result.ats_score)['color'],
        "application_stage": "applied",
        "processing_confidence": resume_parse_result.confidence_score,
        "recommendations": ats_result.recommendations,
        "strengths": ats_result.strengths,
        "weaknesses": ats_result.weaknesses,
        "parsed_info": {
            "skills_count": len(resume_parse_result.skills),
            "education_count": len(resume_parse_result.education),
            "certifications_count": len(resume_parse_result.certifications),
            "projects_count": len(resume_parse_result.projects),
            "languages_count": len(resume_parse_result.languages)
        }
    }

# ==================== ENHANCED CANDIDATE DASHBOARD ENDPOINTS ====================

@router.get("/applications", response_model=List[EnhancedApplicationResponse])
async def get_my_applications_enhanced(
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=50),
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get enhanced applications with detailed ATS information"""
    # Get candidate's applications
    candidates_query = db.query(Candidate).filter(
        Candidate.email == current_user.email
    )
    
    # Get total count for pagination
    total_count = candidates_query.count()
    
    # Apply pagination
    offset = (page - 1) * per_page
    candidates = candidates_query.order_by(desc(Candidate.created_at)).offset(offset).limit(per_page).all()
    
    applications = []
    for candidate in candidates:
        # Get enhanced ATS result if available
        ats_result = db.query(ATSResult).filter(
            ATSResult.candidate_id == candidate.id
        ).first()
        
        # Convert to enhanced format
        enhanced_ats_result = None
        if ats_result:
            enhanced_ats_result = EnhancedATSScoreResult(
                ats_score=ats_result.ats_score,
                skills_match_score=ats_result.skills_match_score,
                semantic_score=ats_result.semantic_score,
                experience_score=ats_result.experience_score,
                keyword_score=ats_result.keyword_score,
                education_score=0.0,
                certification_score=0.0,
                project_score=0.0,
                matched_skills=ats_result.matched_skills,
                missing_skills=ats_result.missing_skills,
                extracted_skills=[],
                extracted_experience=0,
                education_level="Not specified",
                certifications=[],
                confidence_score=0.8,
                scoring_breakdown={},
                recommendations=[],
                strengths=[],
                weaknesses=[]
            )
        
        applications.append(create_enhanced_application_response(candidate, enhanced_ats_result))
    
    return applications

@router.get("/applications/{application_id}")
async def get_application_details_enhanced(
    application_id: str,
    current_user: User = Depends(AuthMiddleware.get_current_candidate),
    db: Session = Depends(get_db)
):
    """Get detailed enhanced application information"""
    candidate = db.query(Candidate).filter(
        Candidate.id == application_id,
        Candidate.email == current_user.email
    ).first()
    
    if not candidate:
        raise HTTPException(status_code=404, detail="Application not found")
    
    # Get ATS result
    ats_result = db.query(ATSResult).filter(
        ATSResult.candidate_id == candidate.id
    ).first()
    
    # Get stage history
    stage_history = db.query(CandidateStageHistory).filter(
        CandidateStageHistory.candidate_id == candidate.id
    ).order_by(desc(CandidateStageHistory.created_at)).all()
    
    # Get enhanced parsing result if available
    enhanced_parse_result = None
    try:
        from src.services.enhanced_ats_service import EnhancedATSScoringService
        ats_service = EnhancedATSScoringService()
        enhanced_parse_result = ats_service.enhanced_parse_resume_from_text(candidate.resume_text)
    except:
        pass
    
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
        "enhanced_ats_result": {
            "ats_score": float(ats_result.ats_score) if ats_result else None,
            "interpretation": get_enhanced_ats_interpretation(ats_result.ats_score) if ats_result else None,
            "detailed_breakdown": {
                "skills_match_score": float(ats_result.skills_match_score) if ats_result else None,
                "semantic_score": float(ats_result.semantic_score) if ats_result else None,
                "experience_score": float(ats_result.experience_score) if ats_result else None,
                "keyword_score": float(ats_result.keyword_score) if ats_result else None,
                "matched_skills": ats_result.matched_skills if ats_result else [],
                "missing_skills": ats_result.missing_skills if ats_result else []
            } if ats_result else None
        },
        "enhanced_parsing": {
            "extracted_skills": enhanced_parse_result.skills if enhanced_parse_result else [],
            "education": enhanced_parse_result.education if enhanced_parse_result else [],
            "certifications": enhanced_parse_result.certifications if enhanced_parse_result else [],
            "projects": enhanced_parse_result.projects if enhanced_parse_result else [],
            "languages": enhanced_parse_result.languages if enhanced_parse_result else [],
            "confidence_score": enhanced_parse_result.confidence_score if enhanced_parse_result else 0.0,
            "processing_method": enhanced_parse_result.processing_method if enhanced_parse_result else "unknown"
        } if enhanced_parse_result else None,
        "current_stage": candidate.current_stage,
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

# ==================== UTILITY ENDPOINTS ====================

@router.get("/supported-formats")
async def get_supported_formats():
    """Get list of supported file formats for resume upload"""
    return {
        "supported_formats": [
            {
                "extension": ".pdf",
                "mime_type": "application/pdf",
                "description": "PDF document with OCR support",
                "max_size": "10MB",
                "ocr_enabled": True
            },
            {
                "extension": ".docx",
                "mime_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "description": "Microsoft Word document",
                "max_size": "10MB",
                "ocr_enabled": False
            },
            {
                "extension": ".doc",
                "mime_type": "application/msword",
                "description": "Legacy Microsoft Word document",
                "max_size": "10MB",
                "ocr_enabled": False
            }
        ],
        "general_requirements": {
            "max_file_size": "10MB",
            "ocr_available": True,
            "enhanced_parsing": True,
            "ats_scoring": "7-factor algorithm"
        }
    }

@router.get("/ats-info")
async def get_ats_info():
    """Get information about the ATS scoring system"""
    return {
        "ats_version": "Enhanced 2.0",
        "scoring_factors": [
            {
                "name": "Skills Match",
                "weight": "25%",
                "description": "Overlap between candidate skills and job requirements"
            },
            {
                "name": "Semantic Similarity",
                "weight": "20%",
                "description": "Contextual similarity using TF-IDF and cosine similarity"
            },
            {
                "name": "Experience Match",
                "weight": "15%",
                "description": "Alignment of candidate experience with job requirements"
            },
            {
                "name": "Keyword Match",
                "weight": "10%",
                "description": "Frequency of job keywords in resume"
            },
            {
                "name": "Education Level",
                "weight": "10%",
                "description": "Educational qualifications alignment"
            },
            {
                "name": "Certifications",
                "weight": "10%",
                "description": "Relevant professional certifications"
            },
            {
                "name": "Projects",
                "weight": "10%",
                "description": "Relevant project experience and portfolio"
            }
        ],
        "score_ranges": {
            "excellent": {"min": 90, "max": 100, "color": "#10B981"},
            "very_good": {"min": 80, "max": 89, "color": "#3B82F6"},
            "good": {"min": 70, "max": 79, "color": "#F59E0B"},
            "fair": {"min": 60, "max": 69, "color": "#F97316"},
            "poor": {"min": 0, "max": 59, "color": "#EF4444"}
        },
        "enhanced_features": [
            "OCR support for scanned PDFs",
            "DOCX document parsing",
            "Detailed candidate analysis",
            "Personalized recommendations",
            "Strengths and weaknesses assessment",
            "Education and certification extraction",
            "Project and language detection"
        ]
    }
