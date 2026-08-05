
"""
📋 QUESTION SCHEMAS
Pydantic schemas for interview question management
Merged from New folder (7) - adapted for UUID-based IDs
"""

from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime
from enum import Enum


class QuestionType(str, Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER = "short_answer"
    CODING = "coding"
    BEHAVIORAL = "behavioral"
    TECHNICAL = "technical"
    SITUATIONAL = "situational"
    CASE_STUDY = "case_study"
    APTITUDE = "aptitude"
    COMMUNICATION = "communication"


class DifficultyLevel(str, Enum):
    EASY = "Easy"
    MEDIUM = "Medium"
    HARD = "Hard"


class QuestionStatus(str, Enum):
    GENERATED = "generated"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"


# ==================== GENERATION REQUEST/CONFIG ====================

class QuestionGenerationRequest(BaseModel):
    """Matches New folder (7) QuestionGenerationRequest"""
    question_count: int = Field(default=5, ge=1, le=50)
    question_types: Optional[List[QuestionType]] = None
    difficulty_distribution: Optional[Dict[str, int]] = None
    categories: Optional[List[str]] = None
    mcq_angles: Optional[List[str]] = None  # angle slugs for MCQs, e.g. ["theory", "code_debugging"]
    regenerate_all: bool = False
    per_type_counts: Optional[Dict[str, int]] = None  # e.g. {"multiple_choice": 8, "coding": 2}
    coding_language: Optional[str] = None  # target language for coding questions: python/java/cpp/javascript/sql


class QuestionGenerationConfig(BaseModel):
    """Used internally to build prompts from interview data"""
    job_position: str
    interview_type: str
    difficulty_level: str
    skills_config: Dict[str, Any] = Field(default_factory=dict)
    question_format: Dict[str, Any] = Field(default_factory=dict)
    evaluation_rules: Dict[str, Any] = Field(default_factory=dict)
    ai_generation_rules: Dict[str, Any] = Field(default_factory=dict)


# ==================== QUESTION SCHEMAS ====================

class QuestionBase(BaseModel):
    question_text: str
    question_type: QuestionType
    difficulty_level: DifficultyLevel
    category: Optional[str] = None
    bloom_taxonomy_level: Optional[str] = None
    time_limit_minutes: int = 5
    points: int = 10


class QuestionCreate(QuestionBase):
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    code_template: Optional[str] = None
    # coding test cases: [{"input","expected_output","is_sample","weight"}]
    test_cases: Optional[List[dict]] = None
    language: Optional[str] = None  # coding language (python/java/cpp/javascript/sql)


class QuestionUpdate(BaseModel):
    question_text: Optional[str] = None
    question_type: Optional[QuestionType] = None
    difficulty_level: Optional[DifficultyLevel] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    code_template: Optional[str] = None
    # coding test cases: [{"input","expected_output","is_sample","weight"}]
    test_cases: Optional[List[dict]] = None
    language: Optional[str] = None  # coding language (python/java/cpp/javascript/sql)
    category: Optional[str] = None
    bloom_taxonomy_level: Optional[str] = None
    time_limit_minutes: Optional[int] = None
    points: Optional[int] = None
    status: Optional[QuestionStatus] = None
    is_approved: Optional[bool] = None
    approval_notes: Optional[str] = None


class Question(QuestionBase):
    id: Optional[str] = None
    interview_id: int
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    code_template: Optional[str] = None
    test_cases: Optional[List[dict]] = None
    language: Optional[str] = None  # coding language (python/java/cpp/javascript/sql)
    status: QuestionStatus = QuestionStatus.GENERATED
    is_approved: bool = False
    approved_by: Optional[str] = None
    approval_notes: Optional[str] = None
    skill_name: Optional[str] = None
    generation_model: Optional[str] = None
    generation_temperature: Optional[float] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# ==================== RESPONSE SCHEMAS ====================

class QuestionGenerationResponse(BaseModel):
    success: bool
    message: str
    questions_generated: int
    questions: List[Question]


class QuestionListResponse(BaseModel):
    questions: List[Question]
    total: int
    approved_count: int
    generated_count: int


# ==================== INTERVIEW QUESTION GENERATION (enhanced route) ====================

class InterviewQuestionGeneration(BaseModel):
    interview_id: int
    num_questions: int = Field(default=5, ge=1, le=20)
    question_types: List[str] = Field(default=["technical", "behavioral"])
    difficulty_level: DifficultyLevel = Field(default=DifficultyLevel.MEDIUM)
    focus_areas: List[str] = Field(default_factory=list)
    regenerate_all: bool = Field(default=False)


class InterviewQuestionResponse(BaseModel):
    success: bool
    message: str
    interview_id: int
    questions_generated: int
    questions: List[Question]


# ==================== AI SERVICE SCHEMAS ====================

class OllamaHealthCheck(BaseModel):
    ollama_healthy: bool
    model: str
    error: Optional[str] = None


class QuestionGenerationStats(BaseModel):
    total_interviews: int
    total_questions: int
    questions_by_type: Dict[str, int]
    questions_by_difficulty: Dict[str, int]
    average_questions_per_interview: float


# ==================== SKILL-BASED GENERATION & TEST SETS ====================

class SkillTypeConfig(BaseModel):
    """One question type within a skill"""
    question_type: str  # "multiple_choice", "coding", etc.
    count_per_set: int = Field(ge=1, le=20)
    pool_size: Optional[int] = None  # total to generate for review; auto-calc if null


class SkillConfig(BaseModel):
    """Configuration for one skill"""
    skill_name: str = Field(min_length=1)
    difficulty: str = Field(default="MEDIUM")
    question_types: List[SkillTypeConfig]


class GeneratePoolRequest(BaseModel):
    """Generate pool questions for one skill+type combo"""
    skill_name: str = Field(..., min_length=1)
    question_type: str = Field(default="multiple_choice")
    difficulty: str = Field(default="MEDIUM")
    pool_size: int = Field(ge=1, le=100)
    marks_per_question: int = Field(default=1, ge=1, le=100)
    language: Optional[str] = None  # coding questions only: python/java/cpp/javascript/sql
    # Minutes to stamp on each generated question. The wizard sends the test's configured
    # per-type time, which is not yet on the DRAFT interview at generation time.
    time_per_question: Optional[int] = Field(default=None, ge=1, le=180)


class SkillTypeTarget(BaseModel):
    """Per-skill, per-type target for set assembly"""
    skill_name: str
    question_type: str
    count_per_set: int = Field(ge=1, le=20)


class AssembleSetsRequest(BaseModel):
    """Request to assemble test sets from accepted pool questions"""
    num_sets: int = Field(default=2, ge=1, le=10)
    skill_type_targets: List[SkillTypeTarget]


class TestSetQuestionResponse(BaseModel):
    id: str
    question_id: str
    question_order: int
    question: Question

    class Config:
        from_attributes = True


class TestSetResponse(BaseModel):
    id: str
    set_number: int
    questions: List[TestSetQuestionResponse]

    class Config:
        from_attributes = True


class AssembleSetsResponse(BaseModel):
    success: bool
    message: str
    num_sets: int
    sets: List[TestSetResponse]


# ==================== ON-THE-FLY ASSEMBLY SCHEMAS ====================

class PoolTargetStatus(BaseModel):
    skill_name: str
    question_type: str
    needed: int
    available: int
    status: str  # "ready", "tight", "insufficient"


class ConfigureAssemblyResponse(BaseModel):
    success: bool
    message: str
    skill_type_targets: List[SkillTypeTarget]
    pool_status: List[PoolTargetStatus]


class ImportQuestionsRequest(BaseModel):
    """Request to import previously generated questions into an interview pool"""
    question_ids: List[str]


class ManualMCQItem(BaseModel):
    """A single manually-entered multiple-choice question."""
    question_text: str
    options: List[str]            # 2-6 option texts (typically 4)
    correct_answer: str           # "A"-"D" (or full option text / 1-4; normalized server-side)


class ManualQuestionsRequest(BaseModel):
    """Request to add manually-entered MCQs into an interview's question pool."""
    skill_name: str
    question_type: str = "multiple_choice"
    difficulty: str = "MEDIUM"
    marks_per_question: int = 1
    questions: List[ManualMCQItem]


# ==================== LEGACY SCHEMAS (kept for compatibility) ====================

class QuestionEvaluationRequest(BaseModel):
    question_id: str
    answer: str
    evaluation_criteria: Optional[Dict[str, Any]] = None


class QuestionEvaluationResponse(BaseModel):
    success: bool
    evaluation: Dict[str, Any]
    score: int
    strengths: List[str]
    improvements: List[str]
    assessment: str


class FollowupQuestionRequest(BaseModel):
    question_id: str
    answer: str


class FollowupQuestionResponse(BaseModel):
    success: bool
    followup_questions: List[Dict[str, Any]]
