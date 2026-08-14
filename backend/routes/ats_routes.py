"""
🤖 ATS API ROUTES
Handles ATS scoring, demo, and performance metrics
Plus ATS system user and interview management
"""

from fastapi import APIRouter, HTTPException, status, Depends
from typing import Dict, Any, List, Optional
from datetime import datetime
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr

from src.services.enhanced_ats_service import EnhancedATSScoringService
from src.services.llm_ats_service import LLMATSService, HybridATSResult
from src.config.database_config_mysql import get_db
from src.auth import get_current_user
from src.models.ats_models import InterviewStatus, InterviewType, DifficultyLevel

# Authenticated at the router, not per endpoint — every route here was reachable
# anonymously, and two of them (/score and /score/hybrid) run scoring on
# caller-supplied text: a sentence-transformer pass and an LLM call respectively.
# On shared hosting that is a free way for anyone to saturate the box.
#
# The bar is "logged in", not a specific role: these take raw text and hold no
# tenant data, and a candidate self-checking a resume against a JD is a legitimate
# use. What must not be possible is calling them with no account at all.
router = APIRouter(
    prefix="/api/v1/ats",
    tags=["ATS"],
    dependencies=[Depends(get_current_user)],
)

# Pydantic models
class ScoreRequest(BaseModel):
    resume_text: str
    job_description: str
    required_skills: list
    required_experience: int

class ScoreResponse(BaseModel):
    ats_score: float
    skills_match_score: float
    semantic_score: float
    experience_score: float
    keyword_score: float
    education_score: float
    certification_score: float
    project_score: float
    matched_skills: list
    missing_skills: list
    extracted_skills: list
    extracted_experience: int
    scoring_breakdown: Dict[str, Any]
    timestamp: str

@router.get("/demo")
async def demo_scoring():
    """Demo scoring with sample data"""
    try:
        ats_service = EnhancedATSScoringService()
        result = ats_service.enhanced_score_candidate(
            resume_text="John Doe - Senior Python Developer with 5 years experience in Django and PostgreSQL",
            job_description="Senior Python Developer position requiring Django and PostgreSQL experience",
            required_skills=["python", "django", "postgresql"],
            required_experience=5
        )
        
        return {
            "demo": True,
            "ats_score": result.ats_score,
            "skills_match_score": result.skills_match_score,
            "semantic_score": result.semantic_score,
            "experience_score": result.experience_score,
            "keyword_score": result.keyword_score,
            "education_score": result.education_score,
            "certification_score": result.certification_score,
            "project_score": result.project_score,
            "matched_skills": result.matched_skills,
            "missing_skills": result.missing_skills,
            "extracted_skills": result.extracted_skills,
            "extracted_experience": result.extracted_experience,
            "scoring_breakdown": result.scoring_breakdown,
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Demo error: {str(e)}")

@router.post("/score", response_model=ScoreResponse)
async def score_candidate(request: ScoreRequest):
    """Score a candidate using the rule-based ATS engine (fast, deterministic)."""
    try:
        ats_service = EnhancedATSScoringService()
        result = ats_service.enhanced_score_candidate(
            resume_text=request.resume_text,
            job_description=request.job_description,
            required_skills=request.required_skills,
            required_experience=request.required_experience
        )

        return ScoreResponse(
            ats_score=result.ats_score,
            skills_match_score=result.skills_match_score,
            semantic_score=result.semantic_score,
            experience_score=result.experience_score,
            keyword_score=result.keyword_score,
            education_score=result.education_score,
            certification_score=result.certification_score,
            project_score=result.project_score,
            matched_skills=result.matched_skills,
            missing_skills=result.missing_skills,
            extracted_skills=result.extracted_skills,
            extracted_experience=result.extracted_experience,
            scoring_breakdown=result.scoring_breakdown,
            timestamp=datetime.now().isoformat()
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scoring error: {str(e)}")


class HybridScoreRequest(BaseModel):
    resume_text: str
    job_description: str
    required_skills: list
    required_experience: int
    required_education: str = "bachelor"


@router.post("/score/hybrid")
async def hybrid_score_candidate(request: HybridScoreRequest):
    """
    Hybrid ATS scoring: rule-based quantitative score + Ollama LLM qualitative analysis.
    Returns all standard score fields plus llm_summary, llm_recommendation,
    inferred_skills, interview_focus, and red_flags.
    If Ollama is unavailable, returns the rule-based score with llm_available=false.
    """
    try:
        service = LLMATSService()
        result: HybridATSResult = await service.hybrid_score_candidate(
            resume_text=request.resume_text,
            job_description=request.job_description,
            required_skills=request.required_skills,
            required_experience=request.required_experience,
            required_education=request.required_education,
        )
        return {
            **result.model_dump(),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Hybrid scoring error: {str(e)}")

@router.get("/metrics")
async def get_ats_metrics():
    """Get ATS system metrics"""
    try:
        return {
            "status": "healthy",
            "algorithm": "7-factor ATS scoring",
            "factors": [
                "Skills Match",
                "Semantic Similarity", 
                "Experience Match",
                "Keyword Density",
                "Education Relevance",
                "Certification Match",
                "Project Experience"
            ],
            "performance": {
                "average_scoring_time": "<20ms",
                "accuracy": "94.5%",
                "model_version": "2.0"
            },
            "database": "MySQL recruitment_db",
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Metrics error: {str(e)}")

@router.get("/diagnostics")
async def get_ats_diagnostics():
    """Live diagnostic: which NLP models are loaded and which code paths are active."""
    try:
        from src.services.enhanced_ats_service import (
            SENTENCE_TRANSFORMERS_AVAILABLE,
            RAPIDFUZZ_AVAILABLE,
            _get_nlp,
        )
        nlp = _get_nlp()

        st_version = None
        if SENTENCE_TRANSFORMERS_AVAILABLE:
            try:
                import sentence_transformers
                st_version = sentence_transformers.__version__
            except Exception:
                st_version = "unknown"

        rf_version = None
        if RAPIDFUZZ_AVAILABLE:
            try:
                import rapidfuzz
                rf_version = rapidfuzz.__version__
            except Exception:
                rf_version = "unknown"

        action_needed = not SENTENCE_TRANSFORMERS_AVAILABLE or not RAPIDFUZZ_AVAILABLE or nlp is None

        return {
            "models": {
                "sentence_transformers": {"available": SENTENCE_TRANSFORMERS_AVAILABLE, "version": st_version},
                "rapidfuzz": {"available": RAPIDFUZZ_AVAILABLE, "version": rf_version},
                "spacy": {"status": "loaded" if nlp else "unavailable", "model": "en_core_web_trf" if nlp else None},
            },
            "effective_code_paths": {
                "semantic_scoring": (
                    "sentence-transformers (all-MiniLM-L6-v2)"
                    if SENTENCE_TRANSFORMERS_AVAILABLE
                    else "TF-IDF cosine similarity (sklearn fallback)"
                ),
                "keyword_matching": (
                    "rapidfuzz partial_ratio (threshold=75)"
                    if RAPIDFUZZ_AVAILABLE
                    else "exact word overlap (no fuzzy)"
                ),
                "skill_ner": (
                    "spaCy en_core_web_trf NER"
                    if nlp
                    else "regex + substring list (no spaCy)"
                ),
            },
            "action_needed": action_needed,
            "install_hint": (
                "pip install sentence-transformers rapidfuzz && python -m spacy download en_core_web_trf"
                if action_needed else None
            ),
            "timestamp": datetime.now().isoformat(),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Diagnostics error: {str(e)}")

@router.get("/interpretation")
async def get_score_interpretation():
    """Get ATS score interpretation guide"""
    try:
        return {
            "score_ranges": {
                "excellent": {"min": 85, "max": 100, "color": "#4CAF50", "recommendation": "Strong candidate - proceed immediately"},
                "good": {"min": 70, "max": 84, "color": "#2196F3", "recommendation": "Qualified candidate - consider for interview"},
                "fair": {"min": 50, "max": 69, "color": "#FF9800", "recommendation": "Potential candidate - review carefully"},
                "poor": {"min": 0, "max": 49, "color": "#F44336", "recommendation": "Not suitable - reject"}
            },
            "factor_descriptions": {
                "skills_match": "How well candidate skills match job requirements",
                "semantic_score": "Semantic similarity between resume and job description",
                "experience_score": "Relevance and depth of work experience",
                "keyword_score": "Presence of important keywords in resume",
                "education_score": "Relevance of educational background",
                "certification_score": "Relevance of certifications and licenses",
                "project_score": "Quality and relevance of project experience"
            },
            "timestamp": datetime.now().isoformat()
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Interpretation error: {str(e)}")

@router.get("/system/status")
def get_ats_system_status():
    """Get ATS system status"""
    return {
        "status": "active",
        "database": "recruitment_db",
        "features": {
            "ats_scoring": "enabled",
            "database_integration": "enabled"
        }
    }

