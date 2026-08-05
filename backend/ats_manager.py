"""
🤖 ATS MANAGER MODULE
Comprehensive ATS scoring, candidate management, and analytics system
Integrates all ATS functionality with database operations and advanced features
"""

import os
import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any, Union
from sqlalchemy.orm import Session
from sqlalchemy import func, desc, and_, or_
from pathlib import Path

# Import custom modules
from src.config.database_config_mysql import get_db, init_db
from src.models.models import (
    Candidate, ATSResult, JobPosting, CandidateStageHistory,
    User, HRProfile, Interview, Offer
)
from src.services.enhanced_ats_service import EnhancedATSScoringService
from src.utils import (
    get_ats_interpretation, calculate_ats_distribution,
    extract_text_from_file, validate_file_upload, save_file_securely,
    get_job_statistics, validate_pagination_params
)
from src.exceptions import (
    ValidationError, DatabaseError, NotFoundError, ConflictError
)
from src.logging_config import (
    StructuredLogger, PerformanceLogger, business_logger
)
from src.constants import (
    ATS_SCORE_LEVELS, CANDIDATE_STAGES, VALID_SORT_FIELDS,
    DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, SUCCESS_MESSAGES
)

# Initialize logger
ats_logger = StructuredLogger("ats_manager", "INFO")


class ATSManager:
    """
    Comprehensive ATS Management System
    
    Features:
    - Advanced ATS scoring with 7-factor algorithm
    - Candidate ranking and filtering
    - Stage management and tracking
    - Analytics and reporting
    - Bulk operations
    - Performance monitoring
    """
    
    def __init__(self, db: Session):
        self.db = db
        self.logger = ats_logger
    
    def score_candidate(
        self,
        resume_text: str,
        job_description: str,
        required_skills: List[str],
        required_experience: int,
        candidate_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Score candidate using enhanced ATS algorithm
        
        Args:
            resume_text: Extracted resume text
            job_description: Job description text
            required_skills: List of required skills
            required_experience: Required experience in years
            candidate_id: Optional candidate ID
            
        Returns:
            Dictionary with ATS score and detailed breakdown
        """
        with PerformanceLogger(self.logger, "ats_scoring", {
            "candidate_id": candidate_id,
            "resume_length": len(resume_text),
            "job_description_length": len(job_description),
            "required_skills_count": len(required_skills)
        }):
            try:
                # Perform ATS scoring
                ats_service = EnhancedATSScoringService()
                ats_result = ats_service.score_candidate_ats(
                    resume_text=resume_text,
                    job_description=job_description,
                    required_skills=required_skills,
                    required_experience=required_experience
                )
                
                # Get interpretation
                interpretation = get_ats_interpretation(ats_result.ats_score)
                
                # Log business event
                self.logger.log_business_event(
                    "candidate_scored",
                    {
                        "candidate_id": candidate_id,
                        "ats_score": ats_result.ats_score,
                        "level": interpretation['level'],
                        "skills_match": ats_result.skills_match_score,
                        "semantic_match": ats_result.semantic_score
                    }
                )
                
                return {
                    "success": True,
                    "ats_result": ats_result,
                    "interpretation": interpretation,
                    "recommendations": self._generate_recommendations(ats_result, interpretation),
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                self.logger.log_error(
                    error=e,
                    context={
                        "operation": "ats_scoring",
                        "candidate_id": candidate_id
                    }
                )
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    def process_resume_file(
        self,
        file_path: str,
        candidate_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Process resume file and extract text
        
        Args:
            file_path: Path to resume file
            candidate_id: Optional candidate ID
            
        Returns:
            Dictionary with extracted text and metadata
        """
        with PerformanceLogger(self.logger, "resume_processing", {
            "file_path": file_path,
            "candidate_id": candidate_id
        }):
            try:
                # Extract text from file
                text, confidence = extract_text_from_file(file_path)
                
                if not text:
                    return {
                        "success": False,
                        "error": "No text extracted from resume",
                        "confidence": confidence,
                        "timestamp": datetime.utcnow().isoformat()
                    }
                
                # Extract additional information
                extracted_info = self._extract_resume_info(text)
                
                # Log processing event
                self.logger.log_business_event(
                    "resume_processed",
                    {
                        "candidate_id": candidate_id,
                        "file_path": file_path,
                        "confidence": confidence,
                        "text_length": len(text),
                        "extracted_skills": len(extracted_info.get('skills', []))
                    }
                )
                
                return {
                    "success": True,
                    "text": text,
                    "confidence": confidence,
                    "extracted_info": extracted_info,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                self.logger.log_error(
                    error=e,
                    context={
                        "operation": "resume_processing",
                        "file_path": file_path,
                        "candidate_id": candidate_id
                    }
                )
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    def rank_candidates(
        self,
        job_id: str,
        filters: Optional[Dict[str, Any]] = None,
        sort_by: str = "ats_score",
        sort_order: str = "desc",
        page: int = 1,
        per_page: int = DEFAULT_PAGE_SIZE
    ) -> Dict[str, Any]:
        """
        Rank and filter candidates for a job
        
        Args:
            job_id: Job ID
            filters: Optional filtering criteria
            sort_by: Sort field
            sort_order: Sort order
            page: Page number
            per_page: Items per page
            
        Returns:
            Dictionary with ranked candidates and metadata
        """
        with PerformanceLogger(self.logger, "candidate_ranking", {
            "job_id": job_id,
            "filters": filters,
            "sort_by": sort_by,
            "sort_order": sort_order
        }):
            try:
                # Validate pagination
                page, per_page = validate_pagination_params(page, per_page, MAX_PAGE_SIZE)
                
                # Build base query
                query = self.db.query(Candidate, ATSResult).join(
                    ATSResult, Candidate.id == ATSResult.candidate_id
                ).filter(Candidate.job_id == job_id)
                
                # Apply filters
                if filters:
                    query = self._apply_candidate_filters(query, filters)
                
                # Get total count
                total_count = query.count()
                
                # Apply sorting
                query = self._apply_candidate_sorting(query, sort_by, sort_order)
                
                # Apply pagination
                offset = (page - 1) * per_page
                candidates = query.offset(offset).limit(per_page).all()
                
                # Process candidates with interpretation
                processed_candidates = []
                for candidate, ats_result in candidates:
                    interpretation = get_ats_interpretation(ats_result.ats_score)
                    
                    processed_candidates.append({
                        "candidate_id": str(candidate.id),
                        "name": f"{candidate.first_name} {candidate.last_name}",
                        "email": candidate.email,
                        "phone": candidate.phone,
                        "current_stage": candidate.current_stage,
                        "ats_score": ats_result.ats_score,
                        "interpretation": interpretation,
                        "skills_match_score": ats_result.skills_match_score,
                        "semantic_score": ats_result.semantic_score,
                        "experience_score": ats_result.experience_score,
                        "keyword_score": ats_result.keyword_score,
                        "matched_skills": ats_result.matched_skills or [],
                        "missing_skills": ats_result.missing_skills or [],
                        "extracted_skills": candidate.extracted_skills or [],
                        "experience_years": candidate.experience_years,
                        "applied_at": candidate.created_at.isoformat(),
                        "resume_path": candidate.resume_path
                    })
                
                # Calculate distribution
                distribution = calculate_ats_distribution(candidates)
                
                # Get job statistics
                job_stats = get_job_statistics(self.db, job_id)
                
                # Log ranking event
                self.logger.log_business_event(
                    "candidates_ranked",
                    {
                        "job_id": job_id,
                        "total_candidates": total_count,
                        "page": page,
                        "per_page": per_page,
                        "filters_applied": bool(filters)
                    }
                )
                
                return {
                    "success": True,
                    "candidates": processed_candidates,
                    "total_count": total_count,
                    "page": page,
                    "per_page": per_page,
                    "total_pages": (total_count + per_page - 1) // per_page,
                    "has_next": offset + per_page < total_count,
                    "has_prev": page > 1,
                    "ats_distribution": distribution,
                    "job_statistics": job_stats,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                self.logger.log_error(
                    error=e,
                    context={
                        "operation": "candidate_ranking",
                        "job_id": job_id,
                        "filters": filters
                    }
                )
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    def select_candidates_bulk(
        self,
        job_id: str,
        selection_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Bulk select candidates based on various strategies
        
        Args:
            job_id: Job ID
            selection_config: Selection configuration
            
        Returns:
            Dictionary with selection results
        """
        with PerformanceLogger(self.logger, "bulk_candidate_selection", {
            "job_id": job_id,
            "selection_config": selection_config
        }):
            try:
                # Get all candidates for the job
                all_candidates = self.db.query(Candidate, ATSResult).join(
                    ATSResult, Candidate.id == ATSResult.candidate_id
                ).filter(Candidate.job_id == job_id).all()
                
                # Apply selection strategy
                selected_candidates = []
                selection_type = selection_config.get('selection_type', 'manual')
                
                if selection_type == 'top_n':
                    selected_candidates = self._select_top_n(all_candidates, selection_config.get('top_n', 5))
                elif selection_type == 'score_threshold':
                    selected_candidates = self._select_by_score_threshold(
                        all_candidates, selection_config.get('min_score', 70.0)
                    )
                elif selection_type == 'manual':
                    selected_candidates = self._select_manual(all_candidates, selection_config.get('candidate_ids', []))
                else:
                    raise ValidationError(f"Invalid selection type: {selection_type}")
                
                # Move selected candidates to next stage
                moved_count = self._move_candidates_stage(
                    selected_candidates, selection_config.get('next_stage', 'screening')
                )
                
                # Optionally reject others
                rejected_count = 0
                if selection_config.get('reject_others', False):
                    rejected_count = self._reject_remaining_candidates(
                        all_candidates, selected_candidates,
                        selection_config.get('rejection_reason', 'Not selected for next round')
                    )
                
                # Log bulk selection event
                self.logger.log_business_event(
                    "bulk_candidate_selection",
                    {
                        "job_id": job_id,
                        "selection_type": selection_type,
                        "selected_count": moved_count,
                        "rejected_count": rejected_count,
                        "next_stage": selection_config.get('next_stage')
                    }
                )
                
                return {
                    "success": True,
                    "selection_type": selection_type,
                    "selected_count": moved_count,
                    "rejected_count": rejected_count,
                    "next_stage": selection_config.get('next_stage'),
                    "selected_candidates": [str(c[0].id) for c in selected_candidates],
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                self.logger.log_error(
                    error=e,
                    context={
                        "operation": "bulk_candidate_selection",
                        "job_id": job_id,
                        "selection_config": selection_config
                    }
                )
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    def get_ats_analytics(
        self,
        job_id: str,
        time_period: Optional[int] = None  # days
    ) -> Dict[str, Any]:
        """
        Get comprehensive ATS analytics for a job
        
        Args:
            job_id: Job ID
            time_period: Optional time period in days
            
        Returns:
            Dictionary with analytics data
        """
        with PerformanceLogger(self.logger, "ats_analytics", {
            "job_id": job_id,
            "time_period": time_period
        }):
            try:
                # Get job details
                job = self.db.query(JobPosting).filter(JobPosting.id == job_id).first()
                if not job:
                    raise NotFoundError("Job not found", resource_type="job", resource_id=job_id)
                
                # Build base query with time filter
                query = self.db.query(Candidate, ATSResult).join(
                    ATSResult, Candidate.id == ATSResult.candidate_id
                ).filter(Candidate.job_id == job_id)
                
                if time_period:
                    cutoff_date = datetime.utcnow() - timedelta(days=time_period)
                    query = query.filter(Candidate.created_at >= cutoff_date)
                
                candidates = query.all()
                
                # Calculate analytics
                analytics = {
                    "job_details": {
                        "id": str(job.id),
                        "title": job.title,
                        "department": job.department.name if job.department else None,
                        "created_at": job.created_at.isoformat()
                    },
                    "candidate_metrics": {
                        "total_candidates": len(candidates),
                        "time_period_days": time_period,
                        "average_ats_score": 0.0,
                        "median_ats_score": 0.0,
                        "ats_distribution": calculate_ats_distribution(candidates),
                        "stage_distribution": self._calculate_stage_distribution(candidates),
                        "application_trends": self._calculate_application_trends(candidates, time_period)
                    },
                    "skill_analysis": self._analyze_skills(candidates),
                    "experience_analysis": self._analyze_experience(candidates),
                    "recommendations": self._generate_analytics_recommendations(candidates, job)
                }
                
                # Calculate average and median ATS scores
                if candidates:
                    scores = [c[1].ats_score for c in candidates if c[1]]
                    analytics["candidate_metrics"]["average_ats_score"] = sum(scores) / len(scores)
                    analytics["candidate_metrics"]["median_ats_score"] = sorted(scores)[len(scores) // 2]
                
                # Log analytics event
                self.logger.log_business_event(
                    "ats_analytics_generated",
                    {
                        "job_id": job_id,
                        "total_candidates": len(candidates),
                        "time_period": time_period
                    }
                )
                
                return {
                    "success": True,
                    "analytics": analytics,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                self.logger.log_error(
                    error=e,
                    context={
                        "operation": "ats_analytics",
                        "job_id": job_id,
                        "time_period": time_period
                    }
                )
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    def update_candidate_stage(
        self,
        candidate_id: str,
        new_stage: str,
        notes: Optional[str] = None,
        changed_by_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Update candidate stage with history tracking
        
        Args:
            candidate_id: Candidate ID
            new_stage: New stage
            notes: Optional notes
            changed_by_id: User ID making the change
            
        Returns:
            Dictionary with update result
        """
        with PerformanceLogger(self.logger, "candidate_stage_update", {
            "candidate_id": candidate_id,
            "new_stage": new_stage
        }):
            try:
                # Get candidate
                candidate = self.db.query(Candidate).filter(Candidate.id == candidate_id).first()
                if not candidate:
                    raise NotFoundError("Candidate not found", resource_type="candidate", resource_id=candidate_id)
                
                # Validate stage
                if new_stage not in CANDIDATE_STAGES:
                    raise ValidationError(f"Invalid stage: {new_stage}")
                
                old_stage = candidate.current_stage
                
                # Update candidate stage
                candidate.current_stage = new_stage
                candidate.updated_at = datetime.utcnow()
                
                # Create stage history
                stage_history = CandidateStageHistory(
                    candidate_id=candidate_id,
                    from_stage=old_stage,
                    to_stage=new_stage,
                    changed_by_id=changed_by_id,
                    notes=notes or f"Stage changed from {old_stage} to {new_stage}"
                )
                self.db.add(stage_history)
                
                # Commit changes
                self.db.commit()
                
                # Log stage change event
                self.logger.log_business_event(
                    "candidate_stage_updated",
                    {
                        "candidate_id": candidate_id,
                        "old_stage": old_stage,
                        "new_stage": new_stage,
                        "changed_by_id": changed_by_id
                    }
                )
                
                return {
                    "success": True,
                    "candidate_id": candidate_id,
                    "old_stage": old_stage,
                    "new_stage": new_stage,
                    "notes": notes,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                self.db.rollback()
                self.logger.log_error(
                    error=e,
                    context={
                        "operation": "candidate_stage_update",
                        "candidate_id": candidate_id,
                        "new_stage": new_stage
                    }
                )
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    def get_candidate_pipeline(
        self,
        job_id: str
    ) -> Dict[str, Any]:
        """
        Get complete candidate pipeline with stage distribution
        
        Args:
            job_id: Job ID
            
        Returns:
            Dictionary with pipeline data
        """
        with PerformanceLogger(self.logger, "candidate_pipeline", {
            "job_id": job_id
        }):
            try:
                # Get all candidates for the job
                candidates = self.db.query(Candidate, ATSResult).join(
                    ATSResult, Candidate.id == ATSResult.candidate_id
                ).filter(Candidate.job_id == job_id).all()
                
                # Calculate pipeline metrics
                pipeline = {
                    "job_id": job_id,
                    "total_candidates": len(candidates),
                    "stage_distribution": self._calculate_stage_distribution(candidates),
                    "conversion_rates": self._calculate_conversion_rates(candidates),
                    "average_time_in_stage": self._calculate_average_time_in_stages(candidates),
                    "drop_off_rates": self._calculate_drop_off_rates(candidates),
                    "pipeline_efficiency": self._calculate_pipeline_efficiency(candidates)
                }
                
                # Log pipeline analysis event
                self.logger.log_business_event(
                    "candidate_pipeline_analyzed",
                    {
                        "job_id": job_id,
                        "total_candidates": len(candidates)
                    }
                )
                
                return {
                    "success": True,
                    "pipeline": pipeline,
                    "timestamp": datetime.utcnow().isoformat()
                }
                
            except Exception as e:
                self.logger.log_error(
                    error=e,
                    context={
                        "operation": "candidate_pipeline",
                        "job_id": job_id
                    }
                )
                return {
                    "success": False,
                    "error": str(e),
                    "timestamp": datetime.utcnow().isoformat()
                }
    
    # ==================== PRIVATE HELPER METHODS ====================
    
    def _extract_resume_info(self, text: str) -> Dict[str, Any]:
        """Extract structured information from resume text"""
        # This is a simplified implementation
        # In production, this would use NLP techniques
        lines = text.split('\n')
        
        # Extract potential skills (simplified)
        common_skills = ['python', 'java', 'javascript', 'react', 'nodejs', 'sql', 'aws', 'docker', 'kubernetes']
        found_skills = [skill for skill in common_skills if skill.lower() in text.lower()]
        
        # Extract experience (simplified)
        experience_years = 0
        for line in lines:
            if 'year' in line.lower() and 'experience' in line.lower():
                try:
                    import re
                    numbers = re.findall(r'\d+', line)
                    if numbers:
                        experience_years = max(experience_years, int(numbers[0]))
                except:
                    pass
        
        # Extract education (simplified)
        education = []
        for line in lines:
            if any(edu in line.lower() for edu in ['bachelor', 'master', 'phd', 'degree', 'university']):
                education.append(line.strip())
        
        return {
            "skills": found_skills,
            "experience_years": experience_years,
            "education": education,
            "text_length": len(text),
            "line_count": len(lines)
        }
    
    def _apply_candidate_filters(
        self,
        query: Any,
        filters: Dict[str, Any]
    ) -> Any:
        """Apply filters to candidate query"""
        if filters.get('min_score'):
            query = query.filter(ATSResult.ats_score >= filters['min_score'])
        
        if filters.get('max_score'):
            query = query.filter(ATSResult.ats_score <= filters['max_score'])
        
        if filters.get('stage'):
            query = query.filter(Candidate.current_stage == filters['stage'])
        
        if filters.get('search'):
            search_term = f"%{filters['search']}%"
            query = query.filter(
                or_(
                    Candidate.first_name.ilike(search_term),
                    Candidate.last_name.ilike(search_term),
                    Candidate.email.ilike(search_term)
                )
            )
        
        if filters.get('skills'):
            for skill in filters['skills']:
                query = query.filter(Candidate.extracted_skills.ilike(f"%{skill}%"))
        
        return query
    
    def _apply_candidate_sorting(
        self,
        query: Any,
        sort_by: str,
        sort_order: str
    ) -> Any:
        """Apply sorting to candidate query"""
        if sort_by not in VALID_SORT_FIELDS['candidates']:
            sort_by = 'ats_score'
        
        if sort_by == 'ats_score':
            if sort_order == 'desc':
                query = query.order_by(desc(ATSResult.ats_score))
            else:
                query = query.order_by(ATSResult.ats_score)
        elif sort_by == 'applied_at':
            if sort_order == 'desc':
                query = query.order_by(desc(Candidate.created_at))
            else:
                query = query.order_by(Candidate.created_at)
        elif sort_by == 'name':
            if sort_order == 'desc':
                query = query.order_by(desc(Candidate.last_name), desc(Candidate.first_name))
            else:
                query = query.order_by(Candidate.last_name, Candidate.first_name)
        
        return query
    
    def _select_top_n(
        self,
        candidates: List[Tuple[Any, Any]],
        top_n: int
    ) -> List[Tuple[Any, Any]]:
        """Select top N candidates by ATS score"""
        # Sort by ATS score descending
        sorted_candidates = sorted(candidates, key=lambda x: x[1].ats_score, reverse=True)
        return sorted_candidates[:top_n]
    
    def _select_by_score_threshold(
        self,
        candidates: List[Tuple[Any, Any]],
        min_score: float
    ) -> List[Tuple[Any, Any]]:
        """Select candidates above score threshold"""
        return [c for c in candidates if c[1].ats_score >= min_score]

    def _select_top_percentile(
        self,
        candidates: List[Tuple[Any, Any]],
        percentile: float
    ) -> List[Tuple[Any, Any]]:
        """Select top X% candidates by ATS score"""
        import math
        sorted_candidates = sorted(candidates, key=lambda x: x[1].ats_score, reverse=True)
        count = max(1, math.ceil(len(sorted_candidates) * percentile / 100))
        return sorted_candidates[:count]
    
    def _select_manual(
        self,
        candidates: List[Tuple[Any, Any]],
        candidate_ids: List[str]
    ) -> List[Tuple[Any, Any]]:
        """Select specific candidates by ID"""
        return [c for c in candidates if str(c[0].id) in candidate_ids]
    
    def _move_candidates_stage(
        self,
        candidates: List[Tuple[Any, Any]],
        new_stage: str
    ) -> int:
        """Move candidates to new stage"""
        moved_count = 0
        for candidate, _ in candidates:
            if candidate.current_stage != new_stage:
                candidate.current_stage = new_stage
                candidate.updated_at = datetime.utcnow()
                
                # Create stage history
                stage_history = CandidateStageHistory(
                    candidate_id=candidate.id,
                    from_stage=candidate.current_stage,
                    to_stage=new_stage,
                    notes=f"Moved to {new_stage} via bulk selection"
                )
                self.db.add(stage_history)
                moved_count += 1
        
        return moved_count
    
    def _reject_remaining_candidates(
        self,
        all_candidates: List[Tuple[Any, Any]],
        selected_candidates: List[Tuple[Any, Any]],
        rejection_reason: str
    ) -> int:
        """Reject remaining candidates"""
        selected_ids = [str(c[0].id) for c in selected_candidates]
        rejected_count = 0
        
        for candidate, _ in all_candidates:
            if str(candidate.id) not in selected_ids and candidate.current_stage == 'applied':
                candidate.current_stage = 'rejected'
                candidate.updated_at = datetime.utcnow()
                
                # Create stage history
                stage_history = CandidateStageHistory(
                    candidate_id=candidate.id,
                    from_stage='applied',
                    to_stage='rejected',
                    notes=rejection_reason
                )
                self.db.add(stage_history)
                rejected_count += 1
        
        return rejected_count
    
    def _calculate_stage_distribution(self, candidates: List[Tuple[Any, Any]]) -> Dict[str, int]:
        """Calculate distribution of candidates across stages"""
        distribution = {stage: 0 for stage in CANDIDATE_STAGES}
        
        for candidate, _ in candidates:
            if candidate.current_stage in distribution:
                distribution[candidate.current_stage] += 1
        
        return distribution
    
    def _calculate_application_trends(
        self,
        candidates: List[Tuple[Any, Any]],
        time_period: Optional[int]
    ) -> Dict[str, Any]:
        """Calculate application trends over time"""
        if not time_period:
            return {"message": "Time period not specified"}
        
        # Group applications by day
        daily_apps = {}
        for candidate, _ in candidates:
            date = candidate.created_at.date().isoformat()
            if date not in daily_apps:
                daily_apps[date] = 0
            daily_apps[date] += 1
        
        return {
            "period_days": time_period,
            "daily_applications": daily_apps,
            "total_applications": len(candidates),
            "average_per_day": len(candidates) / time_period
        }
    
    def _analyze_skills(self, candidates: List[Tuple[Any, Any]]) -> Dict[str, Any]:
        """Analyze skills across all candidates"""
        all_skills = []
        skill_counts = {}
        
        for candidate, _ in candidates:
            skills = candidate.extracted_skills or []
            all_skills.extend(skills)
            
            for skill in skills:
                skill_counts[skill] = skill_counts.get(skill, 0) + 1
        
        # Calculate skill statistics
        unique_skills = list(set(all_skills))
        skill_coverage = {}
        
        for skill, count in skill_counts.items():
            skill_coverage[skill] = {
                "count": count,
                "percentage": (count / len(candidates)) * 100
            }
        
        return {
            "total_unique_skills": len(unique_skills),
            "most_common_skills": sorted(skill_coverage.items(), key=lambda x: x[1]['count'], reverse=True)[:10],
            "skill_coverage": skill_coverage,
            "all_skills": unique_skills
        }
    
    def _analyze_experience(self, candidates: List[Tuple[Any, Any]]) -> Dict[str, Any]:
        """Analyze experience across all candidates"""
        experiences = [c.experience_years for c, _ in candidates if c.experience_years]
        
        if not experiences:
            return {"message": "No experience data available"}
        
        return {
            "average_experience": sum(experiences) / len(experiences),
            "median_experience": sorted(experiences)[len(experiences) // 2],
            "min_experience": min(experiences),
            "max_experience": max(experiences),
            "experience_distribution": self._calculate_experience_distribution(experiences)
        }
    
    def _calculate_experience_distribution(self, experiences: List[int]) -> Dict[str, int]:
        """Calculate experience distribution"""
        distribution = {
            "0-2 years": 0,
            "3-5 years": 0,
            "6-10 years": 0,
            "10+ years": 0
        }
        
        for exp in experiences:
            if exp <= 2:
                distribution["0-2 years"] += 1
            elif exp <= 5:
                distribution["3-5 years"] += 1
            elif exp <= 10:
                distribution["6-10 years"] += 1
            else:
                distribution["10+ years"] += 1
        
        return distribution
    
    def _calculate_conversion_rates(self, candidates: List[Tuple[Any, Any]]) -> Dict[str, float]:
        """Calculate conversion rates between stages"""
        stage_counts = self._calculate_stage_distribution(candidates)
        conversion_rates = {}
        
        # Calculate conversion from each stage to the next
        stages_order = ['applied', 'screening', 'interview_scheduled', 'interviewed', 'offered', 'hired']
        
        for i, stage in enumerate(stages_order[:-1]):
            next_stage = stages_order[i + 1]
            current_count = stage_counts.get(stage, 0)
            next_count = stage_counts.get(next_stage, 0)
            
            if current_count > 0:
                conversion_rates[f"{stage}_to_{next_stage}"] = (next_count / current_count) * 100
            else:
                conversion_rates[f"{stage}_to_{next_stage}"] = 0.0
        
        return conversion_rates
    
    def _calculate_average_time_in_stages(self, candidates: List[Tuple[Any, Any]]) -> Dict[str, float]:
        """Calculate average time candidates spend in each stage"""
        stage_times = {}
        
        for candidate, _ in candidates:
            # Get stage history for this candidate
            history = self.db.query(CandidateStageHistory).filter(
                CandidateStageHistory.candidate_id == candidate.id
            ).order_by(CandidateStageHistory.created_at).all()
            
            # Calculate time in each stage
            for i in range(len(history) - 1):
                from_record = history[i]
                to_record = history[i + 1]
                
                time_diff = (to_record.created_at - from_record.created_at).total_seconds() / 3600  # hours
                
                stage = from_record.to_stage
                if stage not in stage_times:
                    stage_times[stage] = []
                stage_times[stage].append(time_diff)
        
        # Calculate averages
        avg_times = {}
        for stage, times in stage_times.items():
            if times:
                avg_times[stage] = sum(times) / len(times)
        
        return avg_times
    
    def _calculate_drop_off_rates(self, candidates: List[Tuple[Any, Any]]) -> Dict[str, float]:
        """Calculate drop-off rates at each stage"""
        stage_counts = self._calculate_stage_distribution(candidates)
        total_candidates = len(candidates)
        
        drop_off_rates = {}
        stages_order = ['applied', 'screening', 'interview_scheduled', 'interviewed', 'offered']
        
        for stage in stages_order:
            # Candidates that dropped off at this stage (didn't move to next)
            dropped_off = stage_counts.get(stage, 0)
            if total_candidates > 0:
                drop_off_rates[f"{stage}_drop_off"] = (dropped_off / total_candidates) * 100
        
        return drop_off_rates
    
    def _calculate_pipeline_efficiency(self, candidates: List[Tuple[Any, Any]]) -> Dict[str, Any]:
        """Calculate overall pipeline efficiency metrics"""
        if not candidates:
            return {"message": "No candidates available"}
        
        # Calculate efficiency metrics
        stage_counts = self._calculate_stage_distribution(candidates)
        total_candidates = len(candidates)
        
        # Success rate (reached final stage)
        hired_count = stage_counts.get('hired', 0)
        success_rate = (hired_count / total_candidates) * 100 if total_candidates > 0 else 0
        
        # Average ATS score of hired candidates
        hired_candidates = [c for c, _ in candidates if c.current_stage == 'hired']
        avg_hired_ats = 0.0
        if hired_candidates:
            ats_scores = [c[1].ats_score for c, _ in hired_candidates if c[1]]
            avg_hired_ats = sum(ats_scores) / len(ats_scores)
        
        return {
            "total_candidates": total_candidates,
            "hired_candidates": hired_count,
            "success_rate": success_rate,
            "average_hired_ats_score": avg_hired_ats,
            "pipeline_velocity": self._calculate_pipeline_velocity(candidates),
            "quality_of_hire": avg_hired_ats >= 80.0  # High-quality hires have ATS >= 80
        }
    
    def _calculate_pipeline_velocity(self, candidates: List[Tuple[Any, Any]]) -> float:
        """Calculate pipeline velocity (average days from applied to hired)"""
        hired_candidates = [c for c, _ in candidates if c.current_stage == 'hired']
        
        if not hired_candidates:
            return 0.0
        
        total_days = 0
        for candidate in hired_candidates:
            days = (candidate.updated_at - candidate.created_at).days
            total_days += days
        
        return total_days / len(hired_candidates)
    
    def _generate_recommendations(
        self,
        ats_result: Any,
        interpretation: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations based on ATS results"""
        recommendations = []
        
        # Skills-based recommendations
        if ats_result.skills_match_score < 70:
            recommendations.append("Consider highlighting more relevant skills from the job description")
        
        # Experience-based recommendations
        if ats_result.experience_score < 70:
            recommendations.append("Consider emphasizing relevant experience more prominently")
        
        # Semantic-based recommendations
        if ats_result.semantic_score < 70:
            recommendations.append("Consider using more keywords and terminology from the job description")
        
        # Keyword-based recommendations
        if ats_result.keyword_score < 70:
            recommendations.append("Include more specific keywords and technologies mentioned in the job")
        
        # Overall ATS score recommendations
        if ats_result.ats_score < 60:
            recommendations.append("Significant resume improvements needed for better ATS compatibility")
        elif ats_result.ats_score < 80:
            recommendations.append("Moderate resume improvements could increase ATS score")
        
        return recommendations
    
    def _generate_analytics_recommendations(
        self,
        candidates: List[Tuple[Any, Any]],
        job: Any
    ) -> List[str]:
        """Generate recommendations based on analytics"""
        recommendations = []
        
        # Pipeline efficiency recommendations
        stage_counts = self._calculate_stage_distribution(candidates)
        total_candidates = len(candidates)
        
        # High drop-off at screening stage
        screening_drop_off = stage_counts.get('screening', 0)
        applied_count = stage_counts.get('applied', 0)
        
        if applied_count > 0:
            screening_drop_rate = (screening_drop_off / applied_count) * 100
            if screening_drop_rate > 50:
                recommendations.append("Consider reviewing job requirements to reduce screening drop-off rate")
        
        # Low conversion rates
        if stage_counts.get('interviewed', 0) > 0:
            interview_to_offer_rate = (stage_counts.get('offered', 0) / stage_counts.get('interviewed', 0)) * 100
            if interview_to_offer_rate < 30:
                recommendations.append("Consider improving interview process or offer competitiveness")
        
        # Skill gap analysis
        if job.required_skills:
            all_candidate_skills = []
            for candidate, _ in candidates:
                if candidate.extracted_skills:
                    all_candidate_skills.extend(candidate.extracted_skills)
            
            missing_skills = set(job.required_skills) - set(all_candidate_skills)
            if len(missing_skills) > len(job.required_skills) * 0.5:
                recommendations.append(f"Consider sourcing candidates with skills: {', '.join(list(missing_skills))}")
        
        return recommendations


# ==================== CONVENIENCE FUNCTIONS ====================

def get_ats_manager(db: Session) -> ATSManager:
    """Get ATS manager instance"""
    return ATSManager(db)

def create_ats_pipeline(
    resume_file_path: str,
    job_description: str,
    required_skills: List[str],
    required_experience: int,
    db: Session
) -> Dict[str, Any]:
    """
    Complete ATS pipeline from resume to scoring
    
    Args:
        resume_file_path: Path to resume file
        job_description: Job description
        required_skills: Required skills
        required_experience: Required experience
        db: Database session
        
    Returns:
        Complete ATS pipeline results
    """
    manager = get_ats_manager(db)
    
    # Process resume
    resume_result = manager.process_resume_file(resume_file_path)
    if not resume_result['success']:
        return resume_result
    
    # Score candidate
    scoring_result = manager.score_candidate(
        resume_text=resume_result['text'],
        job_description=job_description,
        required_skills=required_skills,
        required_experience=required_experience
    )
    
    return {
        "resume_processing": resume_result,
        "ats_scoring": scoring_result,
        "pipeline_success": resume_result['success'] and scoring_result['success']
    }
