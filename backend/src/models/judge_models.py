"""
CODING-JUDGE MODELS — coding problems, test cases, submissions, per-test results.

These power the online coding-assessment / judge feature (HackerRank/LeetCode style).
They follow the repo conventions exactly:
  * UUID-string primary keys (``String(36)``, ``default=_uuid``)
  * tenant isolation via the ``TenantScoped`` mixin + nullable ``company_id`` FK,
    so the read/write hooks in ``src/tenancy/events.py`` auto-scope every query.
  * JSON columns for list/dict payloads.

They share the single ``Base`` from ``models.py`` (imported at the bottom of that
module) so ``Base.metadata.create_all()`` in ``init_db()`` creates them on startup
and the alembic autogenerate sees them.
"""
from sqlalchemy import (Column, Integer, String, Float, DateTime, Boolean,
                        Text, ForeignKey, JSON, Index)
from sqlalchemy.orm import relationship

from .models import Base, _uuid
from ..tenancy import TenantScoped
from datetime import datetime


# ── verdict / status vocabularies (kept as plain strings, like the rest of the schema) ──
# Submission lifecycle status
SUBMISSION_STATUSES = ("queued", "running", "done", "error")
# Verdicts (judge outcomes)
VERDICTS = ("AC", "WA", "TLE", "MLE", "RE", "CE", "PENDING")
#   AC  = Accepted              WA = Wrong Answer
#   TLE = Time Limit Exceeded   MLE = Memory Limit Exceeded
#   RE  = Runtime Error         CE = Compilation Error
DIFFICULTIES = ("easy", "medium", "hard")


class CodingProblem(Base, TenantScoped):
    """An algorithmic problem an admin/HR authors and a candidate solves."""
    __tablename__ = "coding_problems"

    id                = Column(String(36), primary_key=True, default=_uuid)
    company_id        = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)

    title             = Column(String(255), nullable=False)
    slug              = Column(String(280), nullable=True, index=True)
    description       = Column(Text, nullable=True)          # markdown statement
    difficulty        = Column(String(10), default="easy")    # easy | medium | hard
    tags              = Column(JSON, nullable=True)           # ["arrays", "dp", ...]

    # Resource limits applied to every submission against this problem.
    time_limit_ms     = Column(Integer, default=2000)         # per test case, wall clock
    memory_limit_mb   = Column(Integer, default=256)

    allowed_languages = Column(JSON, nullable=True)           # ["python","cpp","javascript"]; null = all supported
    starter_code      = Column(JSON, nullable=True)           # {"python": "...", "cpp": "..."}
    reference_solution= Column(Text, nullable=True)           # admin-only canonical solution

    # Optional links into the existing hiring flow (an interview/job can embed a problem).
    interview_id      = Column(Integer, nullable=True, index=True)
    job_id            = Column(String(36), nullable=True, index=True)

    created_by        = Column(String(36), ForeignKey("users.id"), nullable=True)
    status            = Column(String(12), default="draft")   # draft | published | archived
    is_public         = Column(Boolean, default=False)        # visible on the public practice list

    created_at        = Column(DateTime, default=datetime.utcnow)
    updated_at        = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    test_cases  = relationship("ProblemTestCase", back_populates="problem",
                               cascade="all, delete-orphan")
    submissions = relationship("CodeSubmission", back_populates="problem")


class ProblemTestCase(Base, TenantScoped):
    """A single input/expected-output pair used to judge a submission.

    ``is_hidden`` cases drive scoring but their input/expected_output are NEVER
    returned to candidates; ``is_sample`` cases are shown on the problem page.
    """
    __tablename__ = "problem_test_cases"

    id              = Column(String(36), primary_key=True, default=_uuid)
    company_id      = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    problem_id      = Column(String(36), ForeignKey("coding_problems.id"), nullable=False, index=True)

    input           = Column(Text, nullable=True)             # fed to STDIN
    expected_output = Column(Text, nullable=True)             # compared against STDOUT
    is_hidden       = Column(Boolean, default=True)
    is_sample       = Column(Boolean, default=False)          # shown to candidate on the problem page
    weight          = Column(Integer, default=1)              # points awarded when this case passes
    ordinal         = Column(Integer, default=0)              # display / execution order

    created_at      = Column(DateTime, default=datetime.utcnow)

    problem = relationship("CodingProblem", back_populates="test_cases")


class CodeSubmission(Base, TenantScoped):
    """A candidate's code submission against a problem; judged asynchronously."""
    __tablename__ = "code_submissions"

    id                   = Column(String(36), primary_key=True, default=_uuid)
    company_id           = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    problem_id           = Column(String(36), ForeignKey("coding_problems.id"), nullable=False, index=True)
    user_id              = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)
    contest_id           = Column(String(36), nullable=True, index=True)   # FK added in Phase 2

    language             = Column(String(20), nullable=False)  # python | cpp | javascript | java | go
    source_code          = Column(Text, nullable=False)

    status               = Column(String(12), default="queued")   # queued | running | done | error
    verdict              = Column(String(8), default="PENDING")    # AC | WA | TLE | MLE | RE | CE
    score                = Column(Float, default=0.0)              # weighted points earned
    max_score            = Column(Float, default=0.0)              # total weight available

    total_time_ms        = Column(Integer, nullable=True)          # max wall time across cases
    max_memory_kb        = Column(Integer, nullable=True)          # peak memory across cases
    passed_count         = Column(Integer, default=0)
    total_count          = Column(Integer, default=0)
    compile_output       = Column(Text, nullable=True)             # compiler stderr on CE
    error_message        = Column(Text, nullable=True)             # judge/internal error detail

    # Optional tie-in to the existing proctoring session for an assessment.
    proctoring_session_id = Column(String(36), nullable=True, index=True)

    submitted_at         = Column(DateTime, default=datetime.utcnow, index=True)
    judged_at            = Column(DateTime, nullable=True)

    problem = relationship("CodingProblem", back_populates="submissions")
    results = relationship("SubmissionTestResult", back_populates="submission",
                           cascade="all, delete-orphan")


class SubmissionTestResult(Base):
    """Per-test-case outcome for a submission. Always accessed via its parent
    submission (which is tenant-scoped), so it carries no ``company_id`` itself."""
    __tablename__ = "submission_results"

    id            = Column(String(36), primary_key=True, default=_uuid)
    submission_id = Column(String(36), ForeignKey("code_submissions.id"), nullable=False, index=True)
    test_case_id  = Column(String(36), ForeignKey("problem_test_cases.id"), nullable=True)

    verdict       = Column(String(8), default="PENDING")   # AC | WA | TLE | MLE | RE | CE
    time_ms       = Column(Integer, nullable=True)
    memory_kb     = Column(Integer, nullable=True)
    exit_code     = Column(Integer, nullable=True)
    stdout        = Column(Text, nullable=True)            # truncated
    stderr        = Column(Text, nullable=True)            # truncated
    ordinal       = Column(Integer, default=0)
    is_hidden     = Column(Boolean, default=True)          # mirror of the case; gate what UI shows

    submission = relationship("CodeSubmission", back_populates="results")


class InterviewCodeSubmission(Base, TenantScoped):
    """A candidate's per-question 'evaluate' attempt on a coding question that is
    embedded in an online interview test (an ``interview_questions`` row — NOT a
    standalone ``coding_problems`` row, hence a separate table from ``CodeSubmission``).

    One row is written each time the candidate clicks Submit/Evaluate during the
    test. When the judge sandbox is unavailable (Docker down) the attempt is stored
    with ``status='pending'`` and a null verdict so nothing is lost.
    """
    __tablename__ = "interview_code_submissions"

    id            = Column(String(36), primary_key=True, default=_uuid)
    company_id    = Column(String(36), ForeignKey("companies.id"), nullable=True, index=True)
    interview_id  = Column(Integer, ForeignKey("interviews.id"), nullable=True, index=True)
    question_id   = Column(String(36), ForeignKey("interview_questions.id"), nullable=False, index=True)
    user_id       = Column(String(36), ForeignKey("users.id"), nullable=True, index=True)

    language      = Column(String(20), nullable=False)
    source_code   = Column(Text, nullable=False)

    status        = Column(String(12), default="graded")   # graded | pending
    verdict       = Column(String(8), nullable=True)        # AC | WA | TLE | MLE | RE | CE | null
    score         = Column(Float, default=0.0)              # points awarded
    max_score     = Column(Float, default=0.0)              # points available
    passed_count  = Column(Integer, default=0)
    total_count   = Column(Integer, default=0)
    message       = Column(Text, nullable=True)             # e.g. "auto-grade offline" / grader note

    created_at    = Column(DateTime, default=datetime.utcnow, index=True)


class JudgeRuntimeState(Base):
    """Single-row control state for the on-demand judge VM (global, not tenant-scoped).

    The API never holds the judge VM warm by itself: ``warmup`` stamps
    ``last_active_at`` (a heartbeat) and flips ``desired_running`` on; the idle
    reaper stops the VM when ``last_active_at`` is older than the idle threshold.
    A single timestamp (rather than a per-test counter) means *any* activity from
    *any* overlapping assessment keeps the VM alive, and an abandoned test simply
    lets the heartbeat go stale so the reaper can shut it down. Keyed by ``name`` so
    a future second fleet can have its own row.
    """
    __tablename__ = "judge_runtime_state"

    name            = Column(String(40), primary_key=True, default="judge_vm")
    desired_running = Column(Boolean, default=False)   # operator/heartbeat intent
    last_active_at  = Column(DateTime, nullable=True)  # most recent warmup / grade activity
    last_started_at = Column(DateTime, nullable=True)  # when we last issued a VM start
    last_stopped_at = Column(DateTime, nullable=True)  # when the reaper last issued a stop
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# Composite index that speeds up the most common listing query
# (a user's submissions for a problem, newest first).
Index("ix_code_submissions_problem_user", CodeSubmission.problem_id, CodeSubmission.user_id)
# A candidate's attempts for one interview question, newest first.
Index("ix_interview_code_submissions_q_user", InterviewCodeSubmission.question_id, InterviewCodeSubmission.user_id)
