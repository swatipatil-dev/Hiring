"""
📋 ENHANCED QUESTION SCHEMAS
Enhanced Pydantic schemas for interview question management with detailed fields
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
    DESCRIPTIVE = "descriptive"

class DifficultyLevel(str, Enum):
    EASY = "Easy"
    MEDIUM = "Medium"
    HARD = "Hard"

class QuestionStatus(str, Enum):
    GENERATED = "generated"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"

class BloomTaxonomyLevel(str, Enum):
    REMEMBER = "Remember"
    UNDERSTAND = "Understand"
    APPLY = "Apply"
    ANALYZE = "Analyze"
    EVALUATE = "Evaluate"
    CREATE = "Create"

# Enhanced Request Models
class EnhancedQuestionGenerationRequest(BaseModel):
    question_count: int = Field(default=5, ge=1, le=50)
    question_types: List[str] = Field(default=["multiple_choice"])
    difficulty_distribution: Dict[str, int] = Field(default_factory=dict)
    categories: List[str] = Field(default_factory=list)
    regenerate_all: bool = Field(default=False)

# Enhanced Response Models
class EnhancedQuestion(BaseModel):
    question_text: str
    question_type: QuestionType
    difficulty_level: DifficultyLevel
    category: str = ""
    bloom_taxonomy_level: BloomTaxonomyLevel = BloomTaxonomyLevel.APPLY
    time_limit_minutes: int = 5
    points: int = 10
    id: Optional[int] = None
    interview_id: Optional[int] = None
    options: Optional[List[str]] = None
    correct_answer: Optional[str] = None
    sample_answer: Optional[str] = None
    code_template: Optional[str] = None
    status: QuestionStatus = QuestionStatus.GENERATED
    is_approved: bool = False
    approved_by: Optional[str] = None
    approval_notes: Optional[str] = None
    generation_model: str = "phi3:mini"
    generation_temperature: float = 0.7
    created_at: datetime
    updated_at: datetime

class EnhancedQuestionGenerationResponse(BaseModel):
    success: bool = True
    message: str = "Questions generated successfully"
    questions_generated: int
    questions: List[EnhancedQuestion]
