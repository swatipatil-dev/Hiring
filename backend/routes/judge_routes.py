"""
Coding-judge API — problems, test cases, submissions + a live-status WebSocket.

Conventions match the rest of the app:
  * auth via ``Depends(get_current_user)``; admin/HR writes via ``require_role``.
  * tenant isolation is automatic — ``get_current_user`` binds the company and the
    SQLAlchemy hooks in ``src/tenancy/events.py`` scope every query / stamp inserts.
  * Pydantic request/response models are declared inline.

Prefix: ``/api/v1/judge`` (registered in main.py).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import (APIRouter, Depends, HTTPException, Query, Request,
                     WebSocket, WebSocketDisconnect, status)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from jose import jwt, JWTError

from src.auth import get_current_user, require_role, SECRET_KEY, ALGORITHM
from src.config.database_config_mysql import get_db, db_manager
from src.models.models import (
    CodingProblem, ProblemTestCase, CodeSubmission, SubmissionTestResult, User,
    InterviewCodeSubmission, Interview, Candidate,
)
from src.tenancy import set_current_company
from src.judge import languages as judge_languages
from src.judge.queue import enqueue_submission

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/judge", tags=["Coding Judge"])

_ADMIN_ROLES = ("admin", "hr", "superadmin")

# Max number of graded "Run" attempts a candidate gets per coding question during
# a test. Attempts that could not actually be judged (sandbox unavailable) do NOT
# count. Once exhausted, the candidate can only finish via "Submit Test".
MAX_RUN_ATTEMPTS = 3


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
    source_code: str = Field(..., min_length=1)
    contest_id: Optional[str] = None
    proctoring_session_id: Optional[str] = None


class RunRequest(BaseModel):
    """Scratch 'Run Code' — execute once against an optional stdin (no scoring)."""
    language: str
    source_code: str = Field(..., min_length=1)
    stdin: str = ""
    # When set, a SQL run with EMPTY stdin is seeded from this question's sample
    # dataset (the CREATE+INSERT setup) so candidates can try their query against
    # the data shown in the problem without hand-writing seed SQL.
    question_id: Optional[str] = None


class GradeQuestionRequest(BaseModel):
    """Judge a candidate's code for an AI-generated coding question against its test cases."""
    question_id: str
    language: str = "python"
    source_code: str = Field(..., min_length=1)
    # When set, persist this evaluate attempt as an InterviewCodeSubmission for the
    # owning candidate (the exam's per-question Submit). Omitted for admin/HR previews.
    interview_id: Optional[int] = None


# Map UI / common language aliases to the judge's registry keys.
_LANG_ALIASES = {"python3": "python", "py": "python", "node": "javascript",
                 "js": "javascript", "c++": "cpp", "cplusplus": "cpp"}


# ──────────────────────────── helpers ────────────────────────────

def _is_admin(user: User) -> bool:
    return user.role in _ADMIN_ROLES


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
async def run_code(
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
        from src.models.question_models import Question as InterviewQuestion
        q = db.query(InterviewQuestion).filter(InterviewQuestion.id == body.question_id).first()
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
    """Return the Interview if it belongs to the current candidate, else None.
    Mirrors the ownership check in candidate_routes.submit_interview."""
    cands = db.query(Candidate).filter(Candidate.user_id == user.id).all()
    if not cands:
        cands = db.query(Candidate).filter(Candidate.email == user.email).all()
    cand_ids = [c.id for c in cands]
    qy = db.query(Interview).filter(
        Interview.id == interview_id,
        Interview.company_id == user.company_id,
    )
    qy = qy.filter(Interview.candidate_id.in_(cand_ids)) if cand_ids \
        else qy.filter(Interview.candidate_id.isnot(None))
    return qy.first()


def _runs_used(db: Session, interview_id: int, question_id: str, user_id: str) -> int:
    """Count a candidate's graded run attempts for a question. Only attempts that
    actually produced a verdict count — 'pending' (sandbox-down) runs are free."""
    return (db.query(InterviewCodeSubmission)
            .filter(InterviewCodeSubmission.interview_id == interview_id,
                    InterviewCodeSubmission.question_id == question_id,
                    InterviewCodeSubmission.user_id == user_id,
                    InterviewCodeSubmission.verdict.isnot(None))
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
async def grade_question(
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
    from src.models.question_models import Question as InterviewQuestion
    from src.judge.interview_grading import grade_interview_question

    q = db.query(InterviewQuestion).filter(InterviewQuestion.id == body.question_id).first()
    if not q:
        raise HTTPException(404, "Question not found")
    reveal = current_user.role in _ADMIN_ROLES

    # Candidate in-test run: enforce the per-question run cap BEFORE judging so an
    # over-limit click reveals nothing and burns no compute.
    interview = None
    if body.interview_id is not None:
        interview = _candidate_owns_interview(db, current_user, body.interview_id)
        if not interview:
            raise HTTPException(404, "Interview not found")
        used = _runs_used(db, body.interview_id, q.id, current_user.id)
        if used >= MAX_RUN_ATTEMPTS:
            raise HTTPException(
                429,
                f"Run limit reached ({MAX_RUN_ATTEMPTS}/{MAX_RUN_ATTEMPTS}). "
                f"Use Submit Test to finish.",
            )

    result = grade_interview_question(q, body.language, body.source_code, reveal_hidden=reveal)

    if interview is not None:
        unavailable = bool(result.get("unavailable"))
        verdict = None if unavailable else result.get("verdict")
        sub = InterviewCodeSubmission(
            interview_id=body.interview_id,
            question_id=q.id,
            user_id=current_user.id,
            language=body.language,
            source_code=body.source_code,
            status="pending" if unavailable else "graded",
            verdict=verdict,
            score=float(result.get("points_awarded") or 0),
            max_score=float(result.get("max_points") or 0),
            passed_count=int(result.get("passed") or 0),
            total_count=int(result.get("total") or 0),
            message=result.get("message"),
        )
        db.add(sub)
        db.commit()
        used = _runs_used(db, body.interview_id, q.id, current_user.id)
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
    used = sum(1 for s in rows if s.verdict is not None)
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
    if not interview and current_user.role not in _ADMIN_ROLES:
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
    secret (header ``X-Judge-Reap-Token`` or ``?token=``), or an admin/HR JWT."""
    token = request.headers.get("X-Judge-Reap-Token") or request.query_params.get("token")
    expected = os.getenv("JUDGE_REAP_TOKEN", "").strip()
    authorized = bool(expected and token and token == expected)
    if not authorized:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            try:
                payload = jwt.decode(auth[7:], SECRET_KEY, algorithms=[ALGORITHM])
                u = db.query(User).filter(User.id == payload.get("sub")).first()
                authorized = bool(u and u.role in _ADMIN_ROLES)
            except JWTError:
                authorized = False
    if not authorized:
        raise HTTPException(401, "Unauthorized")

    from src.judge.provisioner import reap_if_idle
    return reap_if_idle(db)


@router.post("/regrade-pending")
async def regrade_pending(
    interview_id: Optional[int] = Query(None),
    limit: int = Query(200, le=1000),
    current_user: User = Depends(require_role(["admin", "hr", "superadmin"])),
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
    """
    from src.models.question_models import Question as InterviewQuestion
    from src.judge.interview_grading import grade_interview_question

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
        g = grade_interview_question(q, s.language, s.source_code or "")
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
            g = grade_interview_question(q, a.get("language") or "python",
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
    )
    db.add(problem)
    db.flush()  # assign id + auto-stamp company_id (before_flush hook)

    for i, tc in enumerate(body.test_cases or []):
        db.add(ProblemTestCase(
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
    # Tenant auto-filter already scopes to the user's company.
    q = db.query(CodingProblem)
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
    p = db.query(CodingProblem).filter(CodingProblem.id == problem_id).first()
    if not p:
        raise HTTPException(404, "Problem not found")
    if not _is_admin(current_user) and p.status != "published":
        raise HTTPException(404, "Problem not found")

    admin = _is_admin(current_user)
    out = _problem_dict(p, include_solution=admin)
    tcs = db.query(ProblemTestCase).filter(
        ProblemTestCase.problem_id == p.id
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
    p = db.query(CodingProblem).filter(CodingProblem.id == problem_id).first()
    if not p:
        raise HTTPException(404, "Problem not found")
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
    p = db.query(CodingProblem).filter(CodingProblem.id == problem_id).first()
    if not p:
        raise HTTPException(404, "Problem not found")
    created = []
    for i, tc in enumerate(body):
        row = ProblemTestCase(
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
    tcs = db.query(ProblemTestCase).filter(
        ProblemTestCase.problem_id == problem_id
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

    problem = db.query(CodingProblem).filter(CodingProblem.id == body.problem_id).first()
    if not problem:
        raise HTTPException(404, "Problem not found")
    if problem.allowed_languages and body.language not in problem.allowed_languages:
        raise HTTPException(400, f"Language '{body.language}' not allowed for this problem")

    sub = CodeSubmission(
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
    sub = db.query(CodeSubmission).filter(CodeSubmission.id == submission_id).first()
    if not sub:
        raise HTTPException(404, "Submission not found")
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
    q = db.query(CodeSubmission)
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
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
    except JWTError:
        raise ValueError("invalid token")

    db = db_manager.get_db_session()
    try:
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise ValueError("unknown user")
        set_current_company(user.company_id if user.role != "superadmin" else None)
        sub = db.query(CodeSubmission).filter(CodeSubmission.id == submission_id).first()
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
