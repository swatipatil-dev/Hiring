"""
👔 ENHANCED HR API ROUTES
Advanced HR dashboard with ATS ranking, candidate selection, and detailed analytics
"""

from fastapi import APIRouter, HTTPException, status, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from typing import List, Optional, Dict, Any
from src.config.database_config_mysql import get_db
from src.auth import get_current_user, AuthMiddleware
from src.models.models import (
    User, JobPosting, Candidate, ATSResult, CandidateStageHistory,
    Interview, InterviewFeedback, Offer, HRProfile, Department
)
from pydantic import BaseModel, EmailStr, Field
from datetime import datetime, timedelta
import uuid

router = APIRouter(prefix="/api/v1/hr", tags=["Enhanced HR"])

# ==================== ENHANCED PYDANTIC MODELS ====================

class EnhancedJobPostingCreate(BaseModel):
    title: str
    description: str
    requirements: str
    department_id: str
    location: str
    employment_type: str
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    experience_years: int
    required_skills: List[str]
    required_education: Optional[str] = "bachelor"
    required_certifications: Optional[List[str]] = []
    job_highlights: Optional[List[str]] = []
    benefits: Optional[str] = None

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
    required_education: Optional[str]
    required_certifications: Optional[List[str]]
    job_highlights: Optional[List[str]]
    benefits: Optional[str]
    status: str
    created_at: datetime
    application_count: Optional[int] = 0
    average_ats_score: Optional[float] = None
    top_candidates: Optional[List[Dict[str, Any]]] = []

class ATSCandidateResponse(BaseModel):
    candidate_id: str
    name: str
    email: str
    phone: Optional[str]
    current_stage: str
    ats_score: float
    ats_level: str
    ats_color: str
    skills_match_score: float
    semantic_score: float
    experience_score: float
    keyword_score: float
    matched_skills: List[str]
    missing_skills: List[str]
    extracted_skills: List[str]
    experience_years: int
    education_level: Optional[str]
    certifications: List[str]
    confidence_score: float
    applied_at: datetime
    resume_path: str
    recommendations: List[str]
    strengths: List[str]
    weaknesses: List[str]

class JobCandidatesEnhancedResponse(BaseModel):
    job_id: str
    job_title: str
    total_candidates: int
    candidates: List[ATSCandidateResponse]
    statistics: Dict[str, Any]
    ats_distribution: Dict[str, int]

class CandidateSelectionRequest(BaseModel):
    candidate_ids: List[str]
    selection_type: str = Field(..., description="Type of selection: 'top_n', 'score_threshold', 'top_percentile', 'manual'")
    top_n: Optional[int] = Field(None, description="Number of top candidates to select")
    min_score: Optional[float] = Field(None, description="Minimum ATS score threshold")
    percentile: Optional[float] = Field(None, description="Top percentile to select (e.g. 10 = top 10%)", ge=1, le=100)
    reject_others: bool = False
    rejection_reason: Optional[str] = None
    next_stage: str = Field(..., description="Next stage to move selected candidates to")

class BulkActionResponse(BaseModel):
    message: str
    selected_count: int
    rejected_count: int
    moved_to_stage: str
    job_id: str
    processing_time: float

class JobAnalyticsResponse(BaseModel):
    job_id: str
    job_title: str
    total_applications: int
    average_ats_score: Optional[float]
    ats_distribution: Dict[str, int]
    stage_conversion_rates: Dict[str, float]
    source_effectiveness: Dict[str, Any]
    time_to_hire: Optional[int]
    cost_per_hire: Optional[float]

# ==================== HELPER FUNCTIONS ====================

def get_hr_profile_enhanced(db: Session, user: User) -> HRProfile:
    """Get HR profile with enhanced error handling"""
    hr_profile = db.query(HRProfile).filter(HRProfile.user_id == user.id).first()
    if not hr_profile:
        raise HTTPException(status_code=403, detail="HR profile not found")
    return hr_profile

def create_enhanced_job_response(job: JobPosting, db: Session) -> EnhancedJobPostingResponse:
    """Create enhanced job posting response with statistics"""
    # Get application statistics
    app_stats = db.query(
        func.count(Candidate.id).label('application_count'),
        func.avg(Candidate.ats_score).label('avg_ats_score')
    ).filter(Candidate.job_id == job.id).first()
    
    # Get top 5 candidates
    top_candidates_query = db.query(Candidate, ATSResult).join(
        ATSResult, Candidate.id == ATSResult.candidate_id
    ).filter(
        Candidate.job_id == job.id,
        Candidate.current_stage.in_(['applied', 'screening'])
    ).order_by(desc(ATSResult.ats_score)).limit(5)
    
    top_candidates = []
    for candidate, ats_result in top_candidates_query:
        top_candidates.append({
            "candidate_id": str(candidate.id),
            "name": f"{candidate.first_name} {candidate.last_name}",
            "ats_score": float(ats_result.ats_score),
            "applied_at": candidate.created_at
        })
    
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
        required_certifications=getattr(job, 'required_certifications', []),
        job_highlights=getattr(job, 'job_highlights', []),
        benefits=getattr(job, 'benefits', None),
        status=job.status,
        created_at=job.created_at,
        application_count=app_stats.application_count if app_stats else 0,
        average_ats_score=float(app_stats.avg_ats_score) if app_stats and app_stats.avg_ats_score else None,
        top_candidates=top_candidates
    )

def create_ats_candidate_response(candidate, ats_result, enhanced_parse_result=None) -> ATSCandidateResponse:
    """Create enhanced ATS candidate response"""
    from src.services.enhanced_ats_service import get_enhanced_ats_interpretation
    
    ats_interpretation = get_enhanced_ats_interpretation(ats_result.ats_score)
    
    return ATSCandidateResponse(
        candidate_id=str(candidate.id),
        name=f"{candidate.first_name} {candidate.last_name}",
        email=candidate.email,
        phone=candidate.phone,
        current_stage=candidate.current_stage,
        ats_score=ats_result.ats_score,
        ats_level=ats_interpretation['level'],
        ats_color=ats_interpretation['color'],
        skills_match_score=ats_result.skills_match_score,
        semantic_score=ats_result.semantic_score,
        experience_score=ats_result.experience_score,
        keyword_score=ats_result.keyword_score,
        matched_skills=ats_result.matched_skills,
        missing_skills=ats_result.missing_skills,
        extracted_skills=candidate.extracted_skills or [],
        experience_years=candidate.experience_years,
        education_level=getattr(enhanced_parse_result, 'education_level', 'Not specified') if enhanced_parse_result else "Not specified",
        certifications=getattr(enhanced_parse_result, 'certifications', []) if enhanced_parse_result else [],
        confidence_score=getattr(enhanced_parse_result, 'confidence_score', 0.0) if enhanced_parse_result else 0.0,
        applied_at=candidate.created_at,
        resume_path=candidate.resume_path,
        recommendations=getattr(ats_result, 'recommendations', []) if hasattr(ats_result, 'recommendations') else [],
        strengths=getattr(ats_result, 'strengths', []) if hasattr(ats_result, 'strengths') else [],
        weaknesses=getattr(ats_result, 'weaknesses', []) if hasattr(ats_result, 'weaknesses') else []
    )

def calculate_ats_distribution(candidates: List[ATSCandidateResponse]) -> Dict[str, int]:
    """Calculate ATS score distribution"""
    distribution = {
        "excellent": 0,    # 90-100
        "very_good": 0,    # 80-89
        "good": 0,          # 70-79
        "fair": 0,          # 60-69
        "poor": 0            # 0-59
    }
    
    for candidate in candidates:
        score = candidate.ats_score
        if score >= 90:
            distribution["excellent"] += 1
        elif score >= 80:
            distribution["very_good"] += 1
        elif score >= 70:
            distribution["good"] += 1
        elif score >= 60:
            distribution["fair"] += 1
        else:
            distribution["poor"] += 1
    
    return distribution

# ==================== ENHANCED JOB MANAGEMENT ENDPOINTS ====================

@router.post("/jobs", response_model=EnhancedJobPostingResponse, status_code=status.HTTP_201_CREATED)
async def create_job_enhanced(
    job_data: EnhancedJobPostingCreate,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Create enhanced job posting with additional fields
    
    Enhanced Features:
    - Education requirements
    - Certification requirements
    - Job highlights
    - Benefits information
    - Enhanced statistics
    """
    # Validate department belongs to same company
    department = db.query(Department).filter(
        Department.id == job_data.department_id,
        Department.company_id == current_user.company_id
    ).first()
    if not department:
        raise HTTPException(status_code=404, detail="Department not found")
    
    # Get HR profile
    try:
        hr_profile = get_hr_profile_enhanced(db, current_user)
    except HTTPException:
        # If no HR profile exists, create one for admin users
        if current_user.role == "admin":
            hr_profile = HRProfile(
                user_id=current_user.id,
                department_id=job_data.department_id,
                employee_id=f"HR-{uuid.uuid4().hex[:8]}"
            )
            db.add(hr_profile)
            db.commit()
            db.refresh(hr_profile)
        else:
            raise
    
    # Create enhanced job posting
    db_job = JobPosting(
        title=job_data.title,
        description=job_data.description,
        requirements=job_data.requirements,
        department_id=job_data.department_id,
        created_by_id=current_user.id,
        hr_id=hr_profile.id,
        location=job_data.location,
        employment_type=job_data.employment_type,
        salary_min=job_data.salary_min,
        salary_max=job_data.salary_max,
        experience_years=job_data.experience_years,
        required_skills=job_data.required_skills,
        status="active"
    )
    
    # Add enhanced fields (would need to extend JobPosting model)
    # For now, we'll store them in a JSON field or create new columns
    
    db.add(db_job)
    db.commit()
    db.refresh(db_job)
    
    return create_enhanced_job_response(db_job, db)

@router.get("/jobs", response_model=List[EnhancedJobPostingResponse])
async def get_my_jobs_enhanced(
    skip: int = 0,
    limit: int = 50,
    status: Optional[str] = None,
    include_stats: bool = Query(True, description="Include application statistics"),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get enhanced jobs with detailed statistics"""
    query = db.query(JobPosting)
    
    # If not admin, filter by HR profile
    if current_user.role != "admin":
        hr_profile = get_hr_profile_enhanced(db, current_user)
        query = query.filter(JobPosting.hr_id == hr_profile.id)
    
    if status:
        query = query.filter(JobPosting.status == status)
    
    jobs = query.order_by(desc(JobPosting.created_at)).offset(skip).limit(limit).all()
    
    if include_stats:
        return [create_enhanced_job_response(job, db) for job in jobs]
    else:
        # Basic response without statistics for performance
        return [
            EnhancedJobPostingResponse(
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
                required_certifications=getattr(job, 'required_certifications', []),
                job_highlights=getattr(job, 'job_highlights', []),
                benefits=getattr(job, 'benefits', None),
                status=job.status,
                created_at=job.created_at
            )
            for job in jobs
        ]

# ==================== ENHANCED ATS SCREENING ENDPOINTS ====================

@router.get("/jobs/{job_id}/candidates", response_model=JobCandidatesEnhancedResponse)
async def get_job_candidates_enhanced(
    job_id: str,
    sort_by: str = Query("ats_score", description="Sort by field"),
    order: str = Query("desc", description="Sort order"),
    min_score: Optional[float] = Query(None, description="Minimum ATS score filter"),
    max_score: Optional[float] = Query(None, description="Maximum ATS score filter"),
    stage: Optional[str] = Query(None, description="Filter by candidate stage"),
    skills: Optional[List[str]] = Query(None, description="Filter by skills"),
    education: Optional[str] = Query(None, description="Filter by education level"),
    has_certifications: Optional[bool] = Query(None, description="Filter candidates with certifications"),
    search: Optional[str] = Query(None, description="Search in candidate names and emails"),
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Get enhanced candidates for a job with advanced filtering and statistics
    
    Enhanced Features:
    - Advanced filtering options
    - Enhanced ATS information
    - Statistical analysis
    - Pagination support
    - Search functionality
    """
    # Check if job exists and permissions
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if current_user.role != "admin":
        hr_profile = get_hr_profile_enhanced(db, current_user)
        if job.hr_id != hr_profile.id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    # Build enhanced query
    query = db.query(Candidate, ATSResult).join(
        ATSResult, Candidate.id == ATSResult.candidate_id
    ).filter(Candidate.job_id == job_id)
    
    # Apply filters
    if min_score:
        query = query.filter(ATSResult.ats_score >= min_score)
    
    if max_score:
        query = query.filter(ATSResult.ats_score <= max_score)
    
    if stage:
        query = query.filter(Candidate.current_stage == stage)
    
    if search:
        search_filter = or_(
            Candidate.first_name.ilike(f"%{search}%"),
            Candidate.last_name.ilike(f"%{search}%"),
            Candidate.email.ilike(f"%{search}%")
        )
        query = query.filter(search_filter)
    
    if skills:
        for skill in skills:
            query = query.filter(ATSResult.matched_skills.contains([skill]))
    
    # Apply sorting
    if sort_by == "ats_score":
        if order == "desc":
            query = query.order_by(desc(ATSResult.ats_score))
        else:
            query = query.order_by(ATSResult.ats_score)
    elif sort_by == "applied_at":
        if order == "desc":
            query = query.order_by(desc(Candidate.created_at))
        else:
            query = query.order_by(Candidate.created_at)
    elif sort_by == "name":
        if order == "desc":
            query = query.order_by(desc(Candidate.last_name), desc(Candidate.first_name))
        else:
            query = query.order_by(Candidate.last_name, Candidate.first_name)
    
    # Get total count for pagination
    total_count = query.count()
    
    # Apply pagination
    offset = (page - 1) * per_page
    candidates = query.offset(offset).limit(per_page).all()
    
    # Create enhanced candidate responses
    result = []
    for candidate, ats_result in candidates:
        # Get enhanced parsing data if available
        enhanced_parse_result = None
        try:
            from src.services.enhanced_ats_service import EnhancedATSScoringService
            ats_service = EnhancedATSScoringService()
            enhanced_parse_result = ats_service.enhanced_parse_resume_from_text(candidate.resume_text)
        except:
            pass
        
        result.append(create_ats_candidate_response(candidate, ats_result, enhanced_parse_result))
    
    # Calculate statistics
    ats_distribution = calculate_ats_distribution(result)
    
    # Calculate additional statistics
    statistics = {
        "total_candidates": total_count,
        "average_ats_score": sum(c.ats_score for c in result) / len(result) if result else 0,
        "highest_ats_score": max(c.ats_score for c in result) if result else 0,
        "lowest_ats_score": min(c.ats_score for c in result) if result else 0,
        "ats_distribution": ats_distribution,
        "skill_coverage": _calculate_skill_coverage(result, job.required_skills or []),
        "experience_alignment": _calculate_experience_alignment(result, job.experience_years)
    }
    
    return JobCandidatesEnhancedResponse(
        job_id=job_id,
        job_title=job.title,
        total_candidates=total_count,
        candidates=result,
        statistics=statistics,
        ats_distribution=ats_distribution
    )

def _calculate_skill_coverage(candidates: List[ATSCandidateResponse], required_skills: List[str]) -> Dict[str, Any]:
    """Calculate skill coverage statistics"""
    if not required_skills:
        return {"coverage_percentage": 0, "total_required": 0, "total_found": 0}
    
    total_found = 0
    skill_coverage = {}
    
    for skill in required_skills:
        found_count = sum(1 for c in candidates if skill.lower() in [s.lower() for s in c.matched_skills])
        skill_coverage[skill] = {
            "found": found_count > 0,
            "coverage_percentage": (found_count / len(candidates)) * 100 if candidates else 0
        }
        if found_count > 0:
            total_found += 1
    
    return {
        "coverage_percentage": (total_found / len(required_skills)) * 100,
        "total_required": len(required_skills),
        "total_found": total_found,
        "skill_details": skill_coverage
    }

def _calculate_experience_alignment(candidates: List[ATSCandidateResponse], required_years: int) -> Dict[str, Any]:
    """Calculate experience alignment statistics"""
    if not candidates:
        return {"alignment_percentage": 0, "average_years": 0}
    
    total_years = sum(c.experience_years for c in candidates)
    aligned_count = sum(1 for c in candidates if c.experience_years >= required_years)
    
    return {
        "alignment_percentage": (aligned_count / len(candidates)) * 100,
        "average_years": total_years / len(candidates),
        "required_years": required_years,
        "aligned_candidates": aligned_count
    }

@router.post("/jobs/{job_id}/select-candidates", response_model=BulkActionResponse)
async def select_candidates_bulk(
    job_id: str,
    selection: CandidateSelectionRequest,
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Enhanced candidate selection with multiple selection strategies
    
    Selection Types:
    - 'top_n': Select top N candidates by ATS score
    - 'score_threshold': Select candidates above minimum score
    - 'manual': Select specific candidate IDs
    
    Features:
    - Bulk stage movement
    - Automatic rejection option
    - Detailed processing feedback
    """
    import time
    start_time = time.time()
    
    # Validate job exists and permissions
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if current_user.role != "admin":
        hr_profile = get_hr_profile_enhanced(db, current_user)
        if job.hr_id != hr_profile.id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    # Get all candidates for this job
    all_candidates = db.query(Candidate).filter(Candidate.job_id == job_id).all()
    selected_candidates = []
    
    # Apply selection strategy
    if selection.selection_type == "top_n":
        if not selection.top_n:
            raise HTTPException(status_code=400, detail="top_n is required for top_n selection")
        
        # Get ATS results for sorting
        candidates_with_ats = db.query(Candidate, ATSResult).join(
            ATSResult, Candidate.id == ATSResult.candidate_id
        ).filter(Candidate.job_id == job_id).order_by(desc(ATSResult.ats_score)).limit(selection.top_n).all()
        
        selected_candidates = [c[0] for c in candidates_with_ats]
        
    elif selection.selection_type == "score_threshold":
        if not selection.min_score:
            raise HTTPException(status_code=400, detail="min_score is required for score_threshold selection")
        
        candidates_with_ats = db.query(Candidate, ATSResult).join(
            ATSResult, Candidate.id == ATSResult.candidate_id
        ).filter(
            Candidate.job_id == job_id,
            ATSResult.ats_score >= selection.min_score
        ).all()
        
        selected_candidates = [c[0] for c in candidates_with_ats]
        
    elif selection.selection_type == "manual":
        if not selection.candidate_ids:
            raise HTTPException(status_code=400, detail="candidate_ids is required for manual selection")
        
        selected_candidates = db.query(Candidate).filter(
            Candidate.id.in_(selection.candidate_ids),
            Candidate.job_id == job_id
        ).all()
    
    else:
        raise HTTPException(status_code=400, detail=f"Invalid selection_type: {selection.selection_type}")
    
    if not selected_candidates:
        raise HTTPException(status_code=404, detail="No valid candidates found for selection")
    
    # Move selected candidates to next stage
    moved_count = 0
    for candidate in selected_candidates:
        if candidate.current_stage == "applied":
            candidate.current_stage = selection.next_stage
            candidate.updated_at = datetime.utcnow()
            
            # Create stage history
            stage_history = CandidateStageHistory(
                candidate_id=candidate.id,
                from_stage="applied",
                to_stage=selection.next_stage,
                changed_by_id=current_user.id,
                notes=f"Selected via {selection.selection_type} strategy"
            )
            db.add(stage_history)
            moved_count += 1
    
    # Optionally reject others
    rejected_count = 0
    rejected_candidates = []
    if selection.reject_others:
        selected_id_set = {c.id for c in selected_candidates}
        for candidate in all_candidates:
            if candidate.id not in selected_id_set and candidate.current_stage not in ("rejected", "hired"):
                prev_stage = candidate.current_stage
                candidate.current_stage = "rejected"
                candidate.updated_at = datetime.utcnow()

                # Create stage history
                stage_history = CandidateStageHistory(
                    candidate_id=candidate.id,
                    from_stage=prev_stage,
                    to_stage="rejected",
                    changed_by_id=current_user.id,
                    notes=selection.rejection_reason or "Auto-rejected during candidate selection"
                )
                db.add(stage_history)
                rejected_count += 1
                rejected_candidates.append(candidate)

    db.commit()

    # Send rejection emails after the transaction commits
    from src.services.email_service import notify_candidate_rejected
    for c in rejected_candidates:
        notify_candidate_rejected(db, c)

    processing_time = time.time() - start_time
    
    return BulkActionResponse(
        message=f"Successfully processed {len(selected_candidates)} candidates using {selection.selection_type} strategy",
        selected_count=moved_count,
        rejected_count=rejected_count,
        moved_to_stage=selection.next_stage,
        job_id=job_id,
        processing_time=processing_time
    )

@router.get("/jobs/{job_id}/analytics", response_model=JobAnalyticsResponse)
async def get_job_analytics_enhanced(
    job_id: str,
    days: int = Query(30, ge=1, le=365, description="Number of days to analyze"),
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """
    Get enhanced job analytics with detailed insights
    
    Analytics Features:
    - Application funnel analysis
    - ATS score distribution
    - Stage conversion rates
    - Source effectiveness
    - Time-based trends
    """
    # Validate job exists and permissions
    job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    
    if current_user.role != "admin":
        hr_profile = get_hr_profile_enhanced(db, current_user)
        if job.hr_id != hr_profile.id:
            raise HTTPException(status_code=403, detail="Access denied")
    
    # Calculate date range
    end_date = datetime.utcnow()
    start_date = end_date - timedelta(days=days)
    
    # Get applications in date range
    applications = db.query(Candidate).filter(
        Candidate.job_id == job_id,
        Candidate.created_at >= start_date
    ).all()
    
    if not applications:
        return JobAnalyticsResponse(
            job_id=job_id,
            job_title=job.title,
            total_applications=0,
            average_ats_score=None,
            ats_distribution={},
            stage_conversion_rates={},
            source_effectiveness={},
            time_to_hire=None,
            cost_per_hire=None
        )
    
    # Basic statistics
    total_applications = len(applications)
    ats_scores = [float(c.ats_score) for c in applications if c.ats_score]
    average_ats_score = sum(ats_scores) / len(ats_scores) if ats_scores else None
    
    # ATS distribution
    ats_distribution = {
        "excellent": 0, "very_good": 0, "good": 0, "fair": 0, "poor": 0
    }
    
    for score in ats_scores:
        if score >= 90:
            ats_distribution["excellent"] += 1
        elif score >= 80:
            ats_distribution["very_good"] += 1
        elif score >= 70:
            ats_distribution["good"] += 1
        elif score >= 60:
            ats_distribution["fair"] += 1
        else:
            ats_distribution["poor"] += 1
    
    # Stage conversion rates
    stage_stats = db.query(
        Candidate.current_stage,
        func.count(Candidate.id).label('count')
    ).filter(
        Candidate.job_id == job_id,
        Candidate.created_at >= start_date
    ).group_by(Candidate.current_stage).all()
    
    total_stages = sum(stat[1] for stat in stage_stats)
    stage_conversion_rates = {
        stage: (count / total_stages * 100) if total_stages > 0 else 0
        for stage, count in stage_stats
    }
    
    # Source effectiveness (placeholder - would need source tracking)
    source_effectiveness = {
        "career_page": total_applications,  # All applications assumed from career page
        "referrals": 0,
        "linkedin": 0,
        "indeed": 0,
        "other": 0
    }
    
    # Time to hire (placeholder - would need hire tracking)
    hired_candidates = [c for c in applications if c.current_stage == "hired"]
    time_to_hire = None
    if hired_candidates:
        hire_times = [(c.updated_at - c.created_at).days for c in hired_candidates]
        time_to_hire = sum(hire_times) / len(hire_times)
    
    # Cost per hire (placeholder)
    cost_per_hire = None
    
    return JobAnalyticsResponse(
        job_id=job_id,
        job_title=job.title,
        total_applications=total_applications,
        average_ats_score=average_ats_score,
        ats_distribution=ats_distribution,
        stage_conversion_rates=stage_conversion_rates,
        source_effectiveness=source_effectiveness,
        time_to_hire=time_to_hire,
        cost_per_hire=cost_per_hire
    )

# ==================== UTILITY ENDPOINTS ====================

@router.get("/dashboard/stats")
async def get_hr_dashboard_stats(
    current_user: User = Depends(AuthMiddleware.get_current_hr),
    db: Session = Depends(get_db)
):
    """Get HR dashboard statistics"""
    hr_profile = get_hr_profile_enhanced(db, current_user)
    
    # Get HR's jobs
    jobs = db.query(JobPosting).filter(JobPosting.hr_id == hr_profile.id).all()
    
    # Calculate statistics
    total_jobs = len(jobs)
    active_jobs = len([j for j in jobs if j.status == "active"])
    
    # Get applications for HR's jobs
    all_applications = db.query(Candidate).join(JobPosting).filter(
        JobPosting.hr_id == hr_profile.id
    ).all()
    
    total_applications = len(all_applications)
    
    # ATS score distribution for all applications
    ats_scores = [float(c.ats_score) for c in all_applications if c.ats_score]
    avg_ats_score = sum(ats_scores) / len(ats_scores) if ats_scores else 0
    
    # Stage distribution
    stage_stats = {}
    for app in all_applications:
        stage = app.current_stage
        stage_stats[stage] = stage_stats.get(stage, 0) + 1
    
    return {
        "hr_info": {
            "name": f"{current_user.first_name} {current_user.last_name}",
            "department": hr_profile.department.name if hr_profile.department else None,
            "employee_id": hr_profile.employee_id
        },
        "job_statistics": {
            "total_jobs": total_jobs,
            "active_jobs": active_jobs,
            "total_applications": total_applications,
            "average_ats_score": round(avg_ats_score, 2)
        },
        "candidate_pipeline": stage_stats,
        "recent_activity": {
            "applications_today": len([a for a in all_applications 
                                   if a.created_at.date() == datetime.utcnow().date()]),
            "applications_this_week": len([a for a in all_applications 
                                       if (datetime.utcnow() - a.created_at).days <= 7])
        }
    }
