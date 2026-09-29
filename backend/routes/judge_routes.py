"""
Coding-judge API — problems, test cases, submissions + a live-status WebSocket.

Conventions match the rest of the app:
  * auth via ``Depends(get_current_user)``; admin/HR writes via ``require_role``.
  * tenant isolation is automatic — ``get_current_user`` binds the company and the
    SQLAlchemy hooks in ``src/tenancy/events.py`` scope every query / stamp inserts.
  * Pydantic request/response models are declared inline.

Prefix: ``/api/v1/judge`` (registered in main.py).

No ``from __future__ import annotations`` here: /run and /grade-question are wrapped
by slowapi's rate limiter, and FastAPI then resolves string annotations in slowapi's
module, where RunRequest etc. don't exist — the app fails to import (seen on the
production Python 3.11 / pydantic 2.9 stack).
"""

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     WebSocket, WebSocketDisconnect, status)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.auth import get_current_user, require_role, resolve_token_user
from src.config.database_config_mysql import get_db, db_manager
from src.models.models import (
    CodingProblem, ProblemTestCase, CodeSubmission, SubmissionTestResult, User,
    InterviewCodeSubmission, Interview, Candidate,
)
from src.tenancy import set_current_company
from src.judge import languages as judge_languages
from src.judge.queue import enqueue_submission
from src.rate_limit import limiter, user_key
from src.services.interview_access import (candidate_owns_interview, is_staff,
                                           staff_can_access_interview)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/judge", tags=["Coding Judge"])

_ADMIN_ROLES = ("admin", "hr", "superadmin")

# Max number of graded "Run" attempts a candidate gets per coding question during
# a test. Attempts that could not actually be judged (sandbox unavailable) do NOT
# count. Once exhausted, the candidate can only finish via "Submit Test".
MAX_RUN_ATTEMPTS = 3

# Input bounds. Source matches the TEXT column it is stored in (64 KB); stdin is
# generous for hand-written test input. Without them the only ceiling was Apache's
# 64 MB request limit, and the payload then travelled through Redis to the worker.
MAX_SOURCE_CHARS = 64 * 1024
MAX_STDIN_CHARS = 1024 * 1024


# ──────────────────────────── schemas ────────────────────────────

class TestCaseIn(BaseModel):
    input: str = ""
    expected_output: str = ""
    is_hidden: bool = True
    is_sample: bool = False
    weight: int = 1
    ordinal: int = 0


class ProblemCreate(BaseModel):
    title: str
    description: Optional[str] = ""
    difficulty: str = "easy"
    tags: Optional[List[str]] = None
    time_limit_ms: int = 2000
    memory_limit_mb: int = 256
    allowed_languages: Optional[List[str]] = None
    starter_code: Optional[dict] = None
    reference_solution: Optional[str] = None
    status: str = "draft"
    is_public: bool = False
    job_id: Optional[str] = None           # link the problem to a job (for HR test-results)
    test_cases: Optional[List[TestCaseIn]] = None


class ProblemUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    difficulty: Optional[str] = None
    tags: Optional[List[str]] = None
    time_limit_ms: Optional[int] = None
    memory_limit_mb: Optional[int] = None
    allowed_languages: Optional[List[str]] = None
    starter_code: Optional[dict] = None
    reference_solution: Optional[str] = None
    status: Optional[str] = None
    is_public: Optional[bool] = None
    job_id: Optional[str] = None


class SubmissionCreate(BaseModel):
    problem_id: str
    language: str
    source_code: str = Field(..., min_length=1, max_length=MAX_SOURCE_CHARS)
    contest_id: Optional[str] = None
    proctoring_session_id: Optional[str] = None


class RunRequest(BaseModel):
    """Scratch 'Run Code' — execute once against an optional stdin (no scoring)."""
    language: str
    source_code: str = Field(..., min_length=1, max_length=MAX_SOURCE_CHARS)
    stdin: str = Field("", max_length=MAX_STDIN_CHARS)
    # When set, a SQL run with EMPTY stdin is seeded from this question's sample
    # dataset (the CREATE+INSERT setup) so candidates can try their query against
    # the data shown in the problem without hand-writing seed SQL.
    question_id: Optional[str] = None
    # Required with question_id for candidates: the question must be in their own test.
    interview_id: Optional[int] = None


class GradeQuestionRequest(BaseModel):
    """Judge a candidate's code for an AI-generated coding question against its test cases."""
    question_id: str
    language: str = "python"
    source_code: str = Field(..., min_length=1, max_length=MAX_SOURCE_CHARS)
    # When set, persist this evaluate attempt as an InterviewCodeSubmission for the
    # owning candidate (the exam's per-question Submit). Omitted for admin/HR previews.
    interview_id: Optional[int] = None


# Map UI / common language aliases to the judge's registry keys.
_LANG_ALIASES = {"python3": "python", "py": "python", "node": "javascript",
                 "js": "javascript", "c++": "cpp", "cplusplus": "cpp"}


# ──────────────────────────── helpers ────────────────────────────

def _is_admin(user: User) -> bool:
    return user.role in _ADMIN_ROLES


def _own_company(q, model, user: User):
    """Scope a query to the caller's company. Superadmin alone is platform-wide.

    Explicit on purpose: the tenant auto-filter adds the same predicate, but it was
    once dormant for every request and these routes then served any company's
    reference solutions and hidden tests to any admin or HR account.
    """
    if user.role == "superadmin":
        return q
    return q.filter(model.company_id == user.company_id)


def _problem_or_404(db: Session, problem_id: str, user: User) -> CodingProblem:
    p = _own_company(db.query(CodingProblem), CodingProblem, user).filter(
        CodingProblem.id == problem_id
    ).first()
    if not p:
        raise HTTPException(404, "Problem not found")
    from src.services.audit_log import require_support_access
    require_support_access(db, None, user, p.company_id, f"coding problem {problem_id}")
    return p


def _shared_secret_matches(presented: Optional[str], env_var: str) -> bool:
    """Constant-time check of a shared-secret header against its env var.

    An unset secret never matches. Header only: a secret in a query string ends up
    in access logs, proxy logs and browser history.
    """
    expected = os.getenv(env_var, "").strip()
    return bool(expected and presented
                and hmac.compare_digest(presented.encode(), expected.encode()))


def _problem_dict(p: CodingProblem, *, include_solution: bool) -> dict:
    d = {
        "id": p.id, "title": p.title, "slug": p.slug,
        "description": p.description, "difficulty": p.difficulty,
        "tags": p.tags, "time_limit_ms": p.time_limit_ms,
        "memory_limit_mb": p.memory_limit_mb,
        "allowed_languages": p.allowed_languages,
        "starter_code": p.starter_code, "status": p.status,
        "is_public": p.is_public, "job_id": p.job_id, "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }
    if include_solution:
        d["reference_solution"] = p.reference_solution
    return d


def _testcase_dict(tc: ProblemTestCase, *, reveal: bool) -> dict:
    d = {"id": tc.id, "is_hidden": tc.is_hidden, "is_sample": tc.is_sample,
         "weight": tc.weight, "ordinal": tc.ordinal}
    if reveal or not tc.is_hidden:
        d["input"] = tc.input
        d["expected_output"] = tc.expected_output
    return d


def _submission_dict(sub: CodeSubmission, results: list, *, reveal_hidden: bool) -> dict:
    return {
        "id": sub.id, "problem_id": sub.problem_id, "language": sub.language,
        "status": sub.status, "verdict": sub.verdict,
        "score": sub.score, "max_score": sub.max_score,
        "passed_count": sub.passed_count, "total_count": sub.total_count,
        "total_time_ms": sub.total_time_ms, "max_memory_kb": sub.max_memory_kb,
        "compile_output": sub.compile_output, "error_message": sub.error_message,
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
        "judged_at": sub.judged_at.isoformat() if sub.judged_at else None,
        "results": [
            {
                "ordinal": r.ordinal, "verdict": r.verdict,
                "time_ms": r.time_ms, "memory_kb": r.memory_kb,
                "is_hidden": r.is_hidden,
                "stdout": (r.stdout if (reveal_hidden or not r.is_hidden) else None),
                "stderr": (r.stderr if (reveal_hidden or not r.is_hidden) else None),
            }
            for r in sorted(results, key=lambda x: x.ordinal)
        ],
    }


# ──────────────────────────── metadata ────────────────────────────

@router.get("/languages")
async def list_languages(current_user: User = Depends(get_current_user)):
    """Language catalog for the editor's language picker."""
    return {"languages": judge_languages.language_catalog()}


@router.post("/run")
@limiter.limit("20/minute", key_func=user_key)
async def run_code(
    request: Request,
    body: RunRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run code once against an optional stdin and return its output.

    Powers the candidate editor's 'Run Code' button — no scoring, just real
    execution (stdout/stderr/errors) in the hardened sandbox.

    For SQL, ``stdin`` is the setup script (schema + seed data). When it's empty
    and a ``question_id`` is given, we seed from the question's SAMPLE test case so
    the candidate's query runs against the dataset shown in the problem — the setup
    is applied server-side and never returned, so hidden cases aren't exposed.
    """
    lang = _LANG_ALIASES.get(body.language.lower(), body.language.lower())
    if lang not in judge_languages.supported_languages():
        raise HTTPException(
            400,
            f"Language '{body.language}' is not runnable yet. "
            f"Supported: {', '.join(judge_languages.supported_languages())}.",
        )

    stdin = body.stdin or ""
    seeded_from_sample = False
    if lang == "sql" and not stdin.strip() and body.question_id:
        q, _ = _question_for_caller(db, current_user, body.question_id, body.interview_id)
        if q and getattr(q, "test_cases", None):
            try:
                cases = json.loads(q.test_cases) or []
                sample = next((c for c in cases
                               if isinstance(c, dict) and c.get("is_sample") and (c.get("input") or "").strip()),
                              None)
                if sample:
                    stdin = sample.get("input", "") or ""
                    seeded_from_sample = True
            except Exception:
                pass

    # Executed in the judge worker, not here: the API process has no Docker
    # access by design (see src/judge/worker.py). run_in_worker blocks until the
    # result is back, so this stays a normal synchronous response.
    from src.judge.queue import run_in_worker, SandboxUnavailable
    try:
        r = await asyncio.to_thread(
            run_in_worker, "src.judge.worker.run_once_job",
            lang, body.source_code, stdin,
        )
    except SandboxUnavailable as e:
        # Nothing ran. Report it as the judge's own "unavailable" status rather
        # than a 500, so the editor shows a retryable message.
        logger.warning("Run Code unavailable: %s", e)
        return {
            "status": "unavailable", "stdout": "", "stderr": "",
            "exit_code": -1, "time_ms": 0, "timed_out": False,
            "compile_error": None,
            "error_message": "The code execution service is temporarily "
                             "unavailable. Please try again in a moment.",
            "seeded_from_sample": seeded_from_sample,
        }

    return {
        "status": r.status, "stdout": r.stdout, "stderr": r.stderr,
        "exit_code": r.exit_code, "time_ms": r.time_ms, "timed_out": r.timed_out,
        "compile_error": r.compile_error, "error_message": r.error_message,
        "seeded_from_sample": seeded_from_sample,
    }


def _candidate_owns_interview(db: Session, user: User, interview_id: int):
    """Return the Interview if it is the current candidate's own, else None.
    The same rule as candidate_routes.submit_interview (src/services/interview_access)."""
    return candidate_owns_interview(db, user, interview_id)


def _question_for_caller(db: Session, user: User, question_id: str,
                         interview_id: Optional[int]):
    """Resolve a coding question the caller may run code against, or raise 404.

    Candidates: only a question of their own, unfinished interview. Hidden test cases
    are graded here, so letting a candidate grade any question id (or grade without an
    interview, which also skipped the run cap) let them probe hidden tests at will.
    Staff: any question in their company, with or without an interview (previews).
    """
    from src.models.question_models import Question as InterviewQuestion
    q = db.query(InterviewQuestion).filter(InterviewQuestion.id == question_id).first()
    if not q:
        raise HTTPException(404, "Question not found")
    if is_staff(user):
        owner = db.query(Interview).filter(Interview.id == q.interview_id).first()
        if not staff_can_access_interview(user, owner, db):
            raise HTTPException(404, "Question not found")
        return q, None
    if interview_id is None:
        raise HTTPException(404, "Question not found")
    interview = _candidate_owns_interview(db, user, interview_id)
    if not interview or q.interview_id != interview.id:
        raise HTTPException(404, "Question not found")
    if interview.status == "COMPLETED":
        raise HTTPException(403, "Test already submitted.")
    return q, interview


def _counts_as_run():
    """An attempt uses up a run once it is being judged or has a verdict; attempts
    stored while the sandbox was down ('pending', no verdict) are free."""
    from sqlalchemy import or_
    return or_(InterviewCodeSubmission.verdict.isnot(None),
               InterviewCodeSubmission.status == "running")


def _runs_used(db: Session, interview_id: int, question_id: str, user_id: str) -> int:
    """Count a candidate's run attempts for a question (see _counts_as_run)."""
    return (db.query(InterviewCodeSubmission)
            .filter(InterviewCodeSubmission.interview_id == interview_id,
                    InterviewCodeSubmission.question_id == question_id,
                    InterviewCodeSubmission.user_id == user_id,
                    _counts_as_run())
            .count())


def _interview_submission_dict(s: InterviewCodeSubmission) -> dict:
    """Serialize to the shape the exam's Submissions tab expects."""
    return {
        "id": s.id,
        "ts": int(s.created_at.timestamp() * 1000) if s.created_at else None,
        "language": s.language,
        "verdict": s.verdict,
        "points_awarded": s.score,
        "max_points": s.max_score,
        "passed": s.passed_count,
        "total": s.total_count,
        "unavailable": s.status == "pending",
        "note": s.message,
    }


@router.post("/grade-question")
@limiter.limit("10/minute", key_func=user_key)
async def grade_question(
    request: Request,
    body: GradeQuestionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Immediate per-question grading: run the candidate's code against the
    coding question's test cases and return the verdict + passed/total. Used by
    the test editor's 'Submit' button. Hidden-case I/O is withheld from candidates.

    When ``interview_id`` is supplied (the candidate's in-test Submit), the attempt
    is persisted as an InterviewCodeSubmission — including 'pending' attempts made
    while the judge sandbox is unavailable — so nothing is lost across devices.
    """
    from src.judge.interview_grading import grade_interview_question

    q, interview = _question_for_caller(db, current_user, body.question_id, body.interview_id)
    reveal = current_user.role in _ADMIN_ROLES

    # Candidate in-test run: claim a run BEFORE judging, so an over-limit click
    # reveals nothing and burns no compute. The interview row is locked while the
    # runs are counted and the claim is written, so parallel clicks queue here
    # instead of all passing the check before any of them is recorded.
    sub = None
    if interview is not None:
        db.query(Interview).filter(Interview.id == interview.id).with_for_update().first()
        used = _runs_used(db, interview.id, q.id, current_user.id)
        if used >= MAX_RUN_ATTEMPTS:
            db.rollback()
            raise HTTPException(
                429,
                f"Run limit reached ({MAX_RUN_ATTEMPTS}/{MAX_RUN_ATTEMPTS}). "
                f"Use Submit Test to finish.",
            )
        sub = InterviewCodeSubmission(
            company_id=interview.company_id,
            interview_id=interview.id,
            question_id=q.id,
            user_id=current_user.id,
            language=body.language,
            source_code=body.source_code,
            status="running",
        )
        db.add(sub)
        db.commit()          # releases the lock; this attempt now counts

    try:
        # Judging waits on the worker for seconds; keep the event loop free meanwhile.
        result = await asyncio.to_thread(
            grade_interview_question, q, body.language, body.source_code,
            reveal_hidden=reveal)
    except Exception:
        if sub is not None:  # nothing was judged: give the run back
            sub.status = "pending"
            sub.message = "Grading failed before a result was produced."
            db.commit()
        raise

    if sub is not None:
        unavailable = bool(result.get("unavailable"))
        sub.status = "pending" if unavailable else "graded"
        sub.verdict = None if unavailable else result.get("verdict")
        sub.score = float(result.get("points_awarded") or 0)
        sub.max_score = float(result.get("max_points") or 0)
        sub.passed_count = int(result.get("passed") or 0)
        sub.total_count = int(result.get("total") or 0)
        sub.message = result.get("message")
        db.commit()
        used = _runs_used(db, interview.id, q.id, current_user.id)
        result = {
            **result,
            "submission_id": sub.id,
            "runs_used": used,
            "runs_left": max(0, MAX_RUN_ATTEMPTS - used),
            "max_runs": MAX_RUN_ATTEMPTS,
        }

    return result


@router.get("/interview-submissions")
async def list_interview_submissions(
    interview_id: int = Query(...),
    question_id: str = Query(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A candidate's evaluate-attempt history for one in-test coding question,
    oldest first (the Submissions tab reverses to show newest on top)."""
    interview = _candidate_owns_interview(db, current_user, interview_id)
    if not interview:
        raise HTTPException(404, "Interview not found")
    rows = (db.query(InterviewCodeSubmission)
            .filter(InterviewCodeSubmission.interview_id == interview_id,
                    InterviewCodeSubmission.question_id == question_id,
                    InterviewCodeSubmission.user_id == current_user.id)
            .order_by(InterviewCodeSubmission.created_at.asc())
            .all())
    used = sum(1 for s in rows if s.verdict is not None or s.status == "running")
    return {
        "submissions": [_interview_submission_dict(s) for s in rows],
        "runs_used": used,
        "runs_left": max(0, MAX_RUN_ATTEMPTS - used),
        "max_runs": MAX_RUN_ATTEMPTS,
    }


# ──────────────────────────── judge ops: health / warmup / reap / regrade ────────────────────────────

def _engine_ready() -> tuple[bool, dict]:
    """Is the configured execution backend reachable right now?"""
    from src.judge.runner import active_backend
    backend = active_backend()
    if backend == "judge0":
        from src.judge.judge0_client import judge0_available, base_url
        reachable = judge0_available()
        return reachable, {"backend": "judge0", "url_configured": bool(base_url()),
                           "reachable": reachable}
    from src.judge.sandbox import docker_available
    reachable = docker_available()
    return reachable, {"backend": "docker", "reachable": reachable}


@router.get("/health")
async def judge_health(current_user: User = Depends(get_current_user)):
    """Operator/UI probe: is the coding judge ready to grade right now?

    Returns the execution backend's reachability and, when on-demand autostart is
    enabled, the judge VM's power state — so HR sees 'judge down' BEFORE starting an
    assessment instead of discovering it mid-test.
    """
    ready, engine = _engine_ready()
    from src.judge.provisioner import autostart_enabled, instance_status
    vm = {"autostart": True, "status": instance_status()} if autostart_enabled() \
        else {"autostart": False}
    return {"ready": ready, "engine": engine, "vm": vm}


class WarmupRequest(BaseModel):
    interview_id: int


@router.post("/warmup")
async def warmup_judge(
    body: WarmupRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Boot the on-demand judge VM at the START of a test (called when the test
    loads). The candidate works through MCQs first, hiding the VM's ~1 min boot, so
    the judge is ready by the time they reach the coding question. No-op unless the
    test actually contains a coding question and ``JUDGE_AUTOSTART=1``."""
    interview = _candidate_owns_interview(db, current_user, body.interview_id)
    if not interview and current_user.role in _ADMIN_ROLES:
        # Staff may warm the judge for their own company's tests only; otherwise the
        # answer below says whether any company's interview has coding questions.
        interview = _own_company(db.query(Interview), Interview, current_user).filter(
            Interview.id == body.interview_id
        ).first()
    if not interview:
        raise HTTPException(404, "Interview not found")

    from src.models.question_models import Question as InterviewQuestion
    has_coding = db.query(InterviewQuestion).filter(
        InterviewQuestion.interview_id == body.interview_id,
        InterviewQuestion.question_type == "coding",
    ).first() is not None
    if not has_coding:
        return {"warmed": False, "reason": "no_coding_questions"}

    from src.judge.provisioner import ensure_judge_running, touch_activity
    touch_activity(db)                  # heartbeat: keep the VM warm while active
    provision = ensure_judge_running()  # best-effort boot; never raises
    ready, _engine = _engine_ready()
    return {"warmed": True, "ready": ready, "provision": provision}


@router.post("/reap")
async def reap_judge(request: Request, db: Session = Depends(get_db)):
    """Stop the judge VM if it's been idle past ``JUDGE_IDLE_STOP_MIN``. Intended to
    be hit periodically by Cloud Scheduler. Auth: the ``JUDGE_REAP_TOKEN`` shared
    secret (header ``X-Judge-Reap-Token``), or an admin/HR JWT."""
    authorized = _shared_secret_matches(request.headers.get("X-Judge-Reap-Token"),
                                        "JUDGE_REAP_TOKEN")
    if not authorized:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            try:
                u, _ = resolve_token_user(db, auth[7:])
                authorized = u.role in _ADMIN_ROLES
            except HTTPException:
                authorized = False
    if not authorized:
        raise HTTPException(401, "Unauthorized")

    from src.judge.provisioner import reap_if_idle
    return reap_if_idle(db)


@router.post("/regrade-pending")
async def regrade_pending(
    request: Request,
    interview_id: Optional[int] = Query(None),
    limit: int = Query(200, le=1000),
    db: Session = Depends(get_db),
):
    """Re-judge coding work that couldn't be graded while the judge was down — the
    real fulfilment of the 'your code is saved, graded later' promise.

    Two passes:
      1. ``InterviewCodeSubmission`` rows still ``status='pending'`` (per-question
         in-test attempts) → re-grade and persist the verdict/score.
      2. ``COMPLETED`` interviews whose ``answers_json`` carries a ``pending_regrade``
         coding answer → re-grade and fold the awarded points back into the
         interview's ``test_score`` / ``total_marks``.
    Submissions that still can't be graded (judge still down) are left pending.

    Idempotent: pass 1 flips ``pending``→``graded`` and pass 2 pops the
    ``pending_regrade`` key, so a second run finds nothing. Safe on a timer.

    Auth: the ``JUDGE_REGRADE_TOKEN`` shared secret (header ``X-Judge-Regrade-Token``),
    or an admin/HR JWT — same shape as ``/reap``.

    **Scope differs by caller, deliberately.** ``Interview`` and
    ``InterviewCodeSubmission`` are tenant-scoped, and the read auto-filter is a
    no-op when no company is bound. So an HR JWT sweeps only that company (what the
    HR "re-grade now" button wants), while the token-authenticated timer sweeps every
    tenant (what a platform sweeper wants). The token path binds company to None
    explicitly so it can never inherit a stale contextvar from a previous request.
    """
    from src.models.question_models import Question as InterviewQuestion
    from src.judge.interview_grading import grade_interview_question

    # Both branches bind the tenant contextvar explicitly. There is no
    # get_current_user dependency on this route to do it, and the contextvar is
    # per-thread state that would otherwise carry over from an earlier request.
    authorized = _shared_secret_matches(request.headers.get("X-Judge-Regrade-Token"),
                                        "JUDGE_REGRADE_TOKEN")
    if authorized:
        set_current_company(None)   # platform-wide sweep
    else:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            try:
                u, _ = resolve_token_user(db, auth[7:])
                authorized = u.role in _ADMIN_ROLES
                if authorized:
                    set_current_company(u.company_id if u.role != "superadmin" else None)
            except HTTPException:
                authorized = False
    if not authorized:
        raise HTTPException(401, "Unauthorized")

    # An on-demand judge VM is stopped precisely because nobody is testing, so an
    # unattended sweeper would otherwise always hit the 503 below and never recover
    # anything. Boot it first, then re-check.
    ready, engine = _engine_ready()
    if not ready:
        from src.judge.provisioner import autostart_enabled, ensure_judge_running
        if autostart_enabled():
            ensure_judge_running()   # best-effort; never raises
            ready, engine = _engine_ready()
    if not ready:
        raise HTTPException(503, {"message": "Judge backend is not reachable; "
                                             "start it before re-grading.", "engine": engine})

    q_cache: dict = {}

    def _question(qid):
        if qid not in q_cache:
            q_cache[qid] = db.query(InterviewQuestion).filter(InterviewQuestion.id == qid).first()
        return q_cache[qid]

    # ── pass 1: pending per-question submissions ──
    sub_q = db.query(InterviewCodeSubmission).filter(InterviewCodeSubmission.status == "pending")
    if interview_id is not None:
        sub_q = sub_q.filter(InterviewCodeSubmission.interview_id == interview_id)
    pending_subs = sub_q.limit(limit).all()
    subs_regraded = 0
    for s in pending_subs:
        q = _question(s.question_id)
        if not q:
            continue
        # Off the event loop: a sweep can grade hundreds of answers.
        g = await asyncio.to_thread(grade_interview_question, q, s.language, s.source_code or "")
        if g.get("unavailable"):
            continue
        s.status = "graded"
        s.verdict = g.get("verdict")
        s.score = float(g.get("points_awarded") or 0)
        s.max_score = float(g.get("max_points") or 0)
        s.passed_count = int(g.get("passed") or 0)
        s.total_count = int(g.get("total") or 0)
        s.message = g.get("message")
        subs_regraded += 1

    # ── pass 2: interviews with pending coding answers in answers_json ──
    iv_q = db.query(Interview).filter(
        Interview.status == "COMPLETED",
        Interview.answers_json.like("%pending_regrade%"),
    )
    if interview_id is not None:
        iv_q = iv_q.filter(Interview.id == interview_id)
    interviews = iv_q.limit(limit).all()
    interviews_updated = 0
    answers_regraded = 0
    still_pending = 0
    for interview in interviews:
        try:
            answers = json.loads(interview.answers_json) if interview.answers_json else []
        except (ValueError, TypeError):
            continue
        changed = False
        for a in answers:
            if not (isinstance(a, dict) and a.get("pending_regrade")):
                continue
            q = _question(a.get("question_id"))
            if not q:
                continue
            g = await asyncio.to_thread(grade_interview_question, q, a.get("language") or "python",
                                        a.get("selected_option") or "")
            if g.get("unavailable"):
                still_pending += 1
                continue
            awarded = float(g.get("points_awarded") or 0)
            max_points = float(g.get("max_points") or a.get("max_points") or 0)
            a.pop("pending_regrade", None)
            a["verdict"] = g.get("verdict")
            a["is_correct"] = g.get("verdict") == "AC"
            a["passed"] = g.get("passed")
            a["total"] = g.get("total")
            a["points"] = awarded
            a["max_points"] = max_points
            a["message"] = g.get("message")
            interview.test_score = (interview.test_score or 0) + awarded
            interview.total_marks = (interview.total_marks or 0) + max_points
            answers_regraded += 1
            changed = True
        if changed:
            interview.answers_json = json.dumps(answers)
            interview.updated_at = datetime.utcnow()
            interviews_updated += 1

    db.commit()
    return {
        "engine": engine,
        "submissions_regraded": subs_regraded,
        "interviews_updated": interviews_updated,
        "answers_regraded": answers_regraded,
        "still_pending": still_pending,
    }


# ──────────────────────────── problems ────────────────────────────

@router.post("/problems", status_code=status.HTTP_201_CREATED)
async def create_problem(
    body: ProblemCreate,
    current_user: User = Depends(require_role(["admin", "hr", "superadmin"])),
    db: Session = Depends(get_db),
):
    # validate languages
    if body.allowed_languages:
        bad = [l for l in body.allowed_languages if l not in judge_languages.supported_languages()]
        if bad:
            raise HTTPException(400, f"Unsupported language(s): {', '.join(bad)}")

    slug = "-".join((body.title or "problem").lower().split())[:280]
    problem = CodingProblem(
        title=body.title, slug=slug, description=body.description or "",
        difficulty=body.difficulty, tags=body.tags,
        time_limit_ms=body.time_limit_ms, memory_limit_mb=body.memory_limit_mb,
        allowed_languages=body.allowed_languages, starter_code=body.starter_code,
        reference_solution=body.reference_solution, status=body.status,
        is_public=body.is_public, job_id=body.job_id, created_by=current_user.id,
        company_id=current_user.company_id,
    )
    db.add(problem)
    db.flush()  # assign id

    for i, tc in enumerate(body.test_cases or []):
        db.add(ProblemTestCase(
            company_id=problem.company_id,
            problem_id=problem.id, input=tc.input, expected_output=tc.expected_output,
            is_hidden=tc.is_hidden, is_sample=tc.is_sample,
            weight=tc.weight, ordinal=tc.ordinal or i,
        ))
    db.commit()
    db.refresh(problem)
    return _problem_dict(problem, include_solution=True)


@router.get("/problems")
async def list_problems(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    difficulty: Optional[str] = None,
):
    q = _own_company(db.query(CodingProblem), CodingProblem, current_user)
    if difficulty:
        q = q.filter(CodingProblem.difficulty == difficulty)
    if not _is_admin(current_user):
        q = q.filter(CodingProblem.status == "published")
    problems = q.order_by(CodingProblem.created_at.desc()).all()
    return {"problems": [_problem_dict(p, include_solution=False) for p in problems]}


@router.get("/problems/{problem_id}")
async def get_problem(
    problem_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    p = _problem_or_404(db, problem_id, current_user)
    if not _is_admin(current_user) and p.status != "published":
        raise HTTPException(404, "Problem not found")

    admin = _is_admin(current_user)
    out = _problem_dict(p, include_solution=admin)
    tcs = db.query(ProblemTestCase).filter(
        ProblemTestCase.problem_id == p.id,
        ProblemTestCase.company_id == p.company_id,
    ).order_by(ProblemTestCase.ordinal).all()
    # candidates only ever see sample (non-hidden) cases
    out["test_cases"] = [
        _testcase_dict(tc, reveal=admin) for tc in tcs
        if admin or tc.is_sample or not tc.is_hidden
    ]
    return out


@router.put("/problems/{problem_id}")
async def update_problem(
    problem_id: str,
    body: ProblemUpdate,
    current_user: User = Depends(require_role(["admin", "hr", "superadmin"])),
    db: Session = Depends(get_db),
):
    p = _problem_or_404(db, problem_id, current_user)
    for field, value in body.dict(exclude_unset=True).items():
        setattr(p, field, value)
    p.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(p)
    return _problem_dict(p, include_solution=True)


@router.post("/problems/{problem_id}/testcases", status_code=status.HTTP_201_CREATED)
async def add_testcases(
    problem_id: str,
    body: List[TestCaseIn],
    current_user: User = Depends(require_role(["admin", "hr", "superadmin"])),
    db: Session = Depends(get_db),
):
    p = _problem_or_404(db, problem_id, current_user)
    created = []
    for i, tc in enumerate(body):
        row = ProblemTestCase(
            company_id=p.company_id,
            problem_id=p.id, input=tc.input, expected_output=tc.expected_output,
            is_hidden=tc.is_hidden, is_sample=tc.is_sample,
            weight=tc.weight, ordinal=tc.ordinal or i,
        )
        db.add(row)
        created.append(row)
    db.commit()
    return {"created": len(created)}


@router.get("/problems/{problem_id}/testcases")
async def list_testcases(
    problem_id: str,
    current_user: User = Depends(require_role(["admin", "hr", "superadmin"])),
    db: Session = Depends(get_db),
):
    """Admin/HR only — includes hidden cases."""
    p = _problem_or_404(db, problem_id, current_user)
    tcs = db.query(ProblemTestCase).filter(
        ProblemTestCase.problem_id == p.id,
        ProblemTestCase.company_id == p.company_id,
    ).order_by(ProblemTestCase.ordinal).all()
    return {"test_cases": [_testcase_dict(tc, reveal=True) for tc in tcs]}


# ──────────────────────────── submissions ────────────────────────────

@router.post("/submissions", status_code=status.HTTP_201_CREATED)
async def submit_code(
    request: Request,
    body: SubmissionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if body.language not in judge_languages.supported_languages():
        raise HTTPException(400, f"Unsupported language '{body.language}'")

    problem = _problem_or_404(db, body.problem_id, current_user)
    if not _is_admin(current_user) and problem.status != "published":
        raise HTTPException(404, "Problem not found")
    if problem.allowed_languages and body.language not in problem.allowed_languages:
        raise HTTPException(400, f"Language '{body.language}' not allowed for this problem")

    sub = CodeSubmission(
        company_id=problem.company_id,
        problem_id=problem.id, user_id=current_user.id, language=body.language,
        source_code=body.source_code, contest_id=body.contest_id,
        proctoring_session_id=body.proctoring_session_id,
        status="queued", verdict="PENDING", submitted_at=datetime.utcnow(),
    )
    db.add(sub)
    db.commit()
    db.refresh(sub)

    submission_id = sub.id
    # IMPORTANT: enqueue must happen after commit so the worker (separate process,
    # separate transaction) can read the row. The sync fallback runs inline here.
    try:
        dispatch = enqueue_submission(submission_id)
    except Exception as exc:
        # Don't lose the submission; mark it errored so the user sees a clear status.
        sub = db.query(CodeSubmission).filter(CodeSubmission.id == submission_id).first()
        if sub:
            sub.status = "error"
            sub.error_message = f"dispatch failed: {exc}"
            db.commit()
        raise HTTPException(503, f"Could not dispatch judging: {exc}")

    # In JUDGE_SYNC mode the worker already ran (in its own session) by now. End
    # this request's read snapshot with a commit so the re-read reflects the
    # worker's committed result rather than a stale REPEATABLE-READ snapshot.
    db.commit()
    sub = db.query(CodeSubmission).filter(CodeSubmission.id == submission_id).first()
    return {"submission_id": submission_id, "dispatch": dispatch,
            "status": sub.status, "verdict": sub.verdict}


@router.get("/submissions/{submission_id}")
async def get_submission(
    submission_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    sub = _own_company(db.query(CodeSubmission), CodeSubmission, current_user).filter(
        CodeSubmission.id == submission_id
    ).first()
    if not sub:
        raise HTTPException(404, "Submission not found")
    from src.services.audit_log import require_support_access
    require_support_access(db, None, current_user, sub.company_id, f"code submission {submission_id}")
    admin = _is_admin(current_user)
    if not admin and sub.user_id != current_user.id:
        raise HTTPException(403, "Not your submission")
    results = db.query(SubmissionTestResult).filter(
        SubmissionTestResult.submission_id == sub.id
    ).all()
    return _submission_dict(sub, results, reveal_hidden=admin)


@router.get("/submissions")
async def list_submissions(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    problem_id: Optional[str] = None,
    limit: int = Query(50, le=200),
):
    q = _own_company(db.query(CodeSubmission), CodeSubmission, current_user)
    if not _is_admin(current_user):
        q = q.filter(CodeSubmission.user_id == current_user.id)
    if problem_id:
        q = q.filter(CodeSubmission.problem_id == problem_id)
    subs = q.order_by(CodeSubmission.submitted_at.desc()).limit(limit).all()
    return {"submissions": [
        {
            "id": s.id, "problem_id": s.problem_id, "language": s.language,
            "status": s.status, "verdict": s.verdict, "score": s.score,
            "max_score": s.max_score, "passed_count": s.passed_count,
            "total_count": s.total_count,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        }
        for s in subs
    ]}


# ──────────────────────────── live status WebSocket ────────────────────────────

def _ws_authorize(token: Optional[str], submission_id: str):
    """Validate the JWT (query param) and that the submission belongs to the caller.

    Returns (user, submission) on success, or raises ValueError. Uses a short-lived
    session bound to the user's tenant so the auto-filter applies.
    """
    if not token:
        raise ValueError("missing token")

    db = db_manager.get_db_session()
    try:
        try:
            # The same checks as every HTTP route: revoked, MFA-step, inactive and
            # suspended-company tokens are all refused, not just forged ones.
            user, _ = resolve_token_user(db, token)
        except HTTPException:
            raise ValueError("invalid token")
        set_current_company(user.company_id if user.role != "superadmin" else None)
        sub = _own_company(db.query(CodeSubmission), CodeSubmission, user).filter(
            CodeSubmission.id == submission_id
        ).first()
        if not sub:
            raise ValueError("submission not found")
        if user.role not in _ADMIN_ROLES and sub.user_id != user.id:
            raise ValueError("forbidden")
        return user.id, user.role
    finally:
        db.close()
        set_current_company(None)


@router.websocket("/ws/{submission_id}")
async def submission_ws(ws: WebSocket, submission_id: str, token: Optional[str] = None):
    """Stream queued → running → done for a submission.

    Auth: pass the JWT as ``?token=...``. Polls the DB (works with or without
    Redis); the worker also publishes to Redis pub/sub for the horizontal-scale
    path. Hidden-case I/O is never sent unless the viewer is admin/HR.
    """
    await ws.accept()
    try:
        viewer_id, viewer_role = _ws_authorize(token, submission_id)
    except ValueError as e:
        await ws.send_json({"error": str(e)})
        await ws.close(code=4401)
        return

    reveal = viewer_role in _ADMIN_ROLES
    last_signature = None
    max_ticks = int(300 / 0.4)  # ~5 min ceiling
    try:
        for _ in range(max_ticks):
            db = db_manager.get_db_session()
            try:
                sub = db.query(CodeSubmission).filter(
                    CodeSubmission.id == submission_id
                ).first()
                if not sub:
                    await ws.send_json({"error": "submission not found"})
                    break
                results = db.query(SubmissionTestResult).filter(
                    SubmissionTestResult.submission_id == sub.id
                ).all()
                payload = _submission_dict(sub, results, reveal_hidden=reveal)
            finally:
                db.close()

            signature = json.dumps(payload, sort_keys=True, default=str)
            if signature != last_signature:
                await ws.send_json(payload)
                last_signature = signature
            if payload["status"] in ("done", "error"):
                break
            await asyncio.sleep(0.4)
    except WebSocketDisconnect:
        return
    finally:
        try:
            await ws.close()
        except Exception:
            pass
