"""
QUESTION MODELS — interview_questions, question_set_assignments, candidate_question_history
TestSet + TestSetQuestion merged into QuestionSetAssignment (interview_id + set_number + order)

Every table here is TenantScoped. A row's company is its interview's company: the
`_inherit_interview_company` flush hook below copies it on insert, so rows created
outside a request (question generation workers, scripts, tests) are never NULL-company
orphans that the tenant filter would hide from their own company.
"""

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Boolean, Float, event, select
from sqlalchemy.orm import relationship, Session
from .models import Base, Interview
from ..tenancy import TenantScoped
from datetime import datetime
import enum
import uuid


class QuestionType(str, enum.Enum):
    MULTIPLE_CHOICE = "multiple_choice"
    SHORT_ANSWER    = "short_answer"
    CODING          = "coding"
    BEHAVIORAL      = "behavioral"
    TECHNICAL       = "technical"
    SITUATIONAL     = "situational"


class DifficultyLevel(str, enum.Enum):
    EASY   = "Easy"
    MEDIUM = "Medium"
    HARD   = "Hard"


class QuestionStatus(str, enum.Enum):
    GENERATED = "generated"
    APPROVED  = "approved"
    REJECTED  = "rejected"
    MODIFIED  = "modified"


class Question(Base, TenantScoped):
    __tablename__ = "interview_questions"

    id                   = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id           = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    interview_id         = Column(Integer, ForeignKey("interviews.id"), nullable=True, index=True)
    question_text        = Column(Text, nullable=False)
    question_type        = Column(String(50), nullable=False)
    difficulty_level     = Column(String(20), nullable=False)
    category             = Column(String(100), nullable=True)
    bloom_taxonomy_level = Column(String(50), nullable=True)
    options              = Column(Text, nullable=True)     # JSON for MCQ
    correct_answer       = Column(Text, nullable=True)
    sample_answer        = Column(Text, nullable=True)
    code_template        = Column(Text, nullable=True)
    # JSON list for coding questions: [{"input","expected_output","is_sample","weight"}]
    test_cases           = Column(Text, nullable=True)
    # Target programming language for a coding question (judge key: python/java/cpp/
    # javascript/sql). NULL for non-coding questions and legacy coding questions
    # (which are treated as python). Chosen by HR at generation time.
    language             = Column(String(20), nullable=True)
    time_limit_minutes   = Column(Integer, default=5)
    points               = Column(Integer, default=1)
    status               = Column(String(20), default="generated")
    is_approved          = Column(Boolean, default=False)
    approved_by          = Column(String(255), nullable=True)
    approval_notes       = Column(Text, nullable=True)
    skill_name           = Column(String(100), nullable=True)
    is_pool_question     = Column(Boolean, default=False)
    generation_prompt    = Column(Text, nullable=True)
    generation_model     = Column(String(100), nullable=True)
    generation_temperature = Column(String(10), nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)
    updated_at           = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    interview        = relationship("Interview", backref="questions")
    set_assignments  = relationship("QuestionSetAssignment", back_populates="question",
                                    cascade="all, delete-orphan")


class QuestionSetAssignment(Base, TenantScoped):
    """Replaces TestSet + TestSetQuestion. Each row links a question to an interview set."""
    __tablename__ = "question_set_assignments"

    id             = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id     = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    interview_id   = Column(Integer, ForeignKey("interviews.id"), nullable=False, index=True)
    question_id    = Column(String(36), ForeignKey("interview_questions.id"), nullable=False)
    set_number     = Column(Integer, nullable=False, default=1)
    question_order = Column(Integer, default=0)

    interview = relationship("Interview", backref="set_assignments")
    question  = relationship("Question", back_populates="set_assignments")


class CandidateQuestionHistory(Base, TenantScoped):
    """Tracks which pool questions a candidate has already been assigned (retake prevention)."""
    __tablename__ = "candidate_question_history"

    id                 = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id         = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    candidate_id       = Column(String(36), ForeignKey("candidates.id"), nullable=False, index=True)
    source_question_id = Column(String(36), ForeignKey("interview_questions.id"), nullable=False)
    interview_id       = Column(Integer, ForeignKey("interviews.id"), nullable=False)
    assigned_at        = Column(DateTime, default=datetime.utcnow)


class CommunicationAnswer(Base, TenantScoped):
    """
    A candidate's spoken answer to a `communication` question.

    Stores the uploaded recording (for HR playback) plus the transcript and the
    delivery metrics measured from it: speech pace, filler/clarity, acoustic
    confidence, and speaking/pause ratio. One row per (interview, question) —
    re-recording upserts the existing row.
    """
    __tablename__ = "communication_answers"

    id                  = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    company_id          = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    interview_id        = Column(Integer, ForeignKey("interviews.id"), nullable=False, index=True)
    question_id         = Column(String(36), ForeignKey("interview_questions.id"), nullable=False, index=True)
    candidate_id        = Column(String(36), ForeignKey("candidates.id"), nullable=True, index=True)

    audio_path          = Column(String(500), nullable=True)   # served under /uploads/voice_answers/
    transcript          = Column(Text, nullable=True)
    duration_seconds    = Column(Float, nullable=True)

    # Delivery metrics
    words_per_minute    = Column(Float, nullable=True)
    filler_count        = Column(Integer, nullable=True)
    filler_ratio        = Column(Float, nullable=True)
    pause_count         = Column(Integer, nullable=True)
    speaking_ratio      = Column(Float, nullable=True)
    acoustic_confidence = Column(Float, nullable=True)

    communication_score = Column(Float, nullable=True)         # 0-100 composite
    breakdown_json      = Column(Text, nullable=True)          # per-metric sub-scores + feedback

    created_at          = Column(DateTime, default=datetime.utcnow)
    updated_at          = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


_INTERVIEW_CHILDREN = (Question, QuestionSetAssignment, CandidateQuestionHistory, CommunicationAnswer)


@event.listens_for(Session, "before_flush")
def _inherit_interview_company(session, flush_context, instances):
    """Copy the parent interview's company onto new question-family rows that lack one.

    Runs after the generic tenant stamp (src/tenancy/events.py), so it only fills rows
    created with no tenant bound. An explicit company_id is never overwritten.
    """
    for obj in session.new:
        if not isinstance(obj, _INTERVIEW_CHILDREN) or obj.company_id:
            continue
        interview = obj.__dict__.get("interview")
        if interview is not None:
            obj.company_id = interview.company_id
        elif obj.interview_id is not None:
            obj.company_id = session.execute(
                select(Interview.company_id).where(Interview.id == obj.interview_id),
                execution_options={"_tenant_bypass": True},
            ).scalar()
