"""
RQ worker entrypoint for judging a submission.

``judge_submission_job(submission_id)`` is the function enqueued by
``queue.enqueue_submission`` (and called inline in the synchronous fallback).
It loads the submission + problem + test cases, runs the sandboxed judge, persists
per-case results, updates the submission, and publishes status to the pub/sub
channel the WebSocket streams from.

The three ``*_job`` wrappers below exist for a different reason: **only this
process is allowed to touch Docker.** The API service runs as a user with no
Docker access and no write access to ``JUDGE_WORK_ROOT``, deliberately — Docker
group membership is root-equivalent and the API is internet-facing. So the API
enqueues and waits (``queue.run_in_worker``) rather than executing sandboxes
itself.

They are deliberately thin: no DB, no persistence, just "run this and hand back
the result". Arguments and results cross the queue as JSON, not pickle, so they are
plain dicts here; ``queue.run_in_worker`` rebuilds the dataclasses on the API side.
Only the functions listed in ``rq_safety.ALLOWED_JOBS`` can be run at all.

Run a worker with (see backend/entrypoint.sh, ROLE=worker):
    rq worker-pool -n 3 judge --url redis://127.0.0.1:6379/5
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from src.config.database_config_mysql import db_manager
from src.models.models import (
    CodeSubmission, CodingProblem, ProblemTestCase, SubmissionTestResult,
)
from . import verdict as V
from .queue import publish_status, redis_url_has_password
from .rq_safety import as_test_case, to_plain
from .runner import TestCaseInput, judge_submission

logger = logging.getLogger("judge.worker")


def _harden_work_root() -> None:
    """Keep other local accounts out of candidates' job directories.

    Each job directory is world-writable (0777) because the container runs as
    ``nobody`` and must write its compiled binary there. That is only safe if nobody
    else on this shared server can reach them, so the root that holds them is made
    owner-only. Docker's daemon (root) and the container, which mounts one job
    directory directly, are unaffected.
    """
    root = os.getenv("JUDGE_WORK_ROOT")
    if not root or not os.path.isdir(root):
        return
    try:
        os.chmod(root, 0o700)
    except OSError as exc:
        logger.warning("could not restrict %s to its owner: %s", root, exc)


def _warn_about_weak_isolation() -> None:
    if os.getenv("ENVIRONMENT", "").lower() != "production":
        return
    if not os.getenv("JUDGE_RUNTIME"):
        logger.warning(
            "JUDGE_RUNTIME is not set: candidate code runs under Docker's default runtime, "
            "sharing the host kernel. Install gVisor and set JUDGE_RUNTIME=runsc "
            "(docs/CODING_JUDGE.md, 'Security model').")
    if not redis_url_has_password():
        logger.error(
            "the judge queue's Redis URL has no password. On a shared server any local "
            "account can then queue work for this worker. Set requirepass and put the "
            "password in REDIS_URL (docs/CPANEL_DEPLOYMENT.md).")


_harden_work_root()
_warn_about_weak_isolation()


def _status_payload(sub: CodeSubmission, cases=None) -> dict:
    """Build a client-safe status snapshot. Hidden-case I/O is never included."""
    payload = {
        "submission_id": sub.id,
        "status": sub.status,
        "verdict": sub.verdict,
        "score": sub.score,
        "max_score": sub.max_score,
        "passed": sub.passed_count,
        "total": sub.total_count,
        "total_time_ms": sub.total_time_ms,
        "max_memory_kb": sub.max_memory_kb,
        "compile_output": sub.compile_output,
        "error_message": sub.error_message,
    }
    if cases is not None:
        payload["results"] = [
            {
                "ordinal": c.ordinal,
                "verdict": c.verdict,
                "time_ms": c.time_ms,
                "memory_kb": c.memory_kb,
                "is_hidden": c.is_hidden,
                # Only reveal program output for visible (sample) cases.
                "stdout": (None if c.is_hidden else c.stdout),
                "stderr": (None if c.is_hidden else c.stderr),
            }
            for c in sorted(cases, key=lambda x: x.ordinal)
        ]
    return payload


def judge_submission_job(submission_id: str) -> dict:
    db = db_manager.get_db_session()
    try:
        sub: CodeSubmission = db.query(CodeSubmission).filter(
            CodeSubmission.id == submission_id
        ).first()
        if not sub:
            logger.warning("judge: submission %s not found", submission_id)
            return {"error": "not_found"}

        # mark running + notify
        sub.status = "running"
        sub.verdict = V.PENDING
        db.commit()
        publish_status(submission_id, _status_payload(sub))

        problem: CodingProblem = db.query(CodingProblem).filter(
            CodingProblem.id == sub.problem_id
        ).first()
        if not problem:
            sub.status = "error"
            sub.error_message = "problem not found"
            db.commit()
            publish_status(submission_id, _status_payload(sub))
            return {"error": "problem_not_found"}

        tcs = db.query(ProblemTestCase).filter(
            ProblemTestCase.problem_id == problem.id
        ).all()
        cases = [
            TestCaseInput(
                id=tc.id, input=tc.input or "", expected_output=tc.expected_output or "",
                weight=tc.weight or 1, ordinal=tc.ordinal or 0,
                is_hidden=bool(tc.is_hidden), is_sample=bool(tc.is_sample),
            )
            for tc in tcs
        ]

        # run the judge
        result = judge_submission(
            sub.language, sub.source_code, cases,
            time_limit_ms=problem.time_limit_ms or 2000,
            memory_limit_mb=problem.memory_limit_mb or 256,
        )

        # If the sandbox was unavailable, surface an error rather than a false verdict.
        if result.verdict == V.PENDING and result.error_message:
            sub.status = "error"
            sub.error_message = result.error_message
            db.commit()
            publish_status(submission_id, _status_payload(sub))
            return {"error": "sandbox_unavailable", "detail": result.error_message}

        # persist per-case results
        db.query(SubmissionTestResult).filter(
            SubmissionTestResult.submission_id == sub.id
        ).delete()
        result_rows = []
        for c in result.cases:
            row = SubmissionTestResult(
                submission_id=sub.id, test_case_id=c.test_case_id,
                verdict=c.verdict, time_ms=c.time_ms, memory_kb=c.memory_kb,
                exit_code=c.exit_code, stdout=c.stdout, stderr=c.stderr,
                ordinal=c.ordinal, is_hidden=c.is_hidden,
            )
            db.add(row)
            result_rows.append(row)

        # update submission
        sub.status = "done"
        sub.verdict = result.verdict
        sub.score = result.score
        sub.max_score = result.max_score
        sub.passed_count = result.passed_count
        sub.total_count = result.total_count
        sub.total_time_ms = result.total_time_ms
        sub.max_memory_kb = result.max_memory_kb
        sub.compile_output = result.compile_output
        sub.judged_at = datetime.utcnow()
        db.commit()

        publish_status(submission_id, _status_payload(sub, cases=result_rows))
        logger.info("judge: submission %s -> %s (%d/%d)", submission_id,
                    sub.verdict, sub.passed_count, sub.total_count)
        return {"verdict": sub.verdict, "passed": sub.passed_count, "total": sub.total_count}

    except Exception as exc:  # never let a job crash silently
        logger.exception("judge: error judging %s", submission_id)
        try:
            sub = db.query(CodeSubmission).filter(CodeSubmission.id == submission_id).first()
            if sub:
                sub.status = "error"
                sub.error_message = f"{type(exc).__name__}: {exc}"
                db.commit()
                publish_status(submission_id, _status_payload(sub))
        except Exception:
            db.rollback()
        return {"error": "exception", "detail": str(exc)}
    finally:
        db.close()


# ─────────────────────────────────────────────────────────────────────────────
#  Sandbox-only jobs (no database, no persistence)
# ─────────────────────────────────────────────────────────────────────────────
#
# These run the sandbox on behalf of the API process, which cannot do it itself
# (see the module docstring). They intentionally do NOT swallow exceptions: the
# dispatcher re-raises them in the caller so existing try/except blocks in
# routes/judge_routes.py and interview_grading.py keep behaving as they did when
# execution was inline.


def run_once_job(language_key: str, source_code: str, stdin: str = "",
                 time_limit_ms: int = 5000, memory_limit_mb: int = 256):
    """Scratch 'Run Code' — compile and run once, no test-case comparison."""
    from .runner import run_once
    logger.info("run_once_job: lang=%s bytes=%d", language_key, len(source_code or ""))
    return to_plain(run_once(language_key, source_code, stdin,
                             time_limit_ms=time_limit_ms, memory_limit_mb=memory_limit_mb))


def judge_code_job(language_key: str, source_code: str, test_cases,
                   time_limit_ms: int = 5000, memory_limit_mb: int = 256):
    """Graded run against test cases (TestCaseInput fields, as dicts off the queue)."""
    from .runner import judge_submission
    logger.info("judge_code_job: lang=%s cases=%d", language_key, len(test_cases or []))
    return to_plain(judge_submission(language_key, source_code,
                                     [as_test_case(c) for c in test_cases or []],
                                     time_limit_ms=time_limit_ms,
                                     memory_limit_mb=memory_limit_mb))


def judge_sql_job(source_code: str, test_cases, reference_sql=None, image=None,
                  time_limit_ms: int = 5000, memory_limit_mb: int = 256):
    """SQL grading. Separate entry point because SQL is graded by sql_harness.py
    INSIDE the sandbox, comparing result sets rather than stdout strings."""
    from .sql_sandbox import judge_sql_sandboxed
    logger.info("judge_sql_job: cases=%d ref=%s", len(test_cases or []),
                bool(reference_sql))
    return to_plain(judge_sql_sandboxed(source_code, [as_test_case(c) for c in test_cases or []],
                                        reference_sql=reference_sql, image=image,
                                        time_limit_ms=time_limit_ms,
                                        memory_limit_mb=memory_limit_mb))


def hidden_testgen_job(question_text: str, sample_answer: str, category=None,
                       existing=None, min_hidden: int = 3, max_total: int = 6):
    """Validate and top up a question's test cases.

    The WHOLE function runs here rather than each of its sandbox calls being
    dispatched individually — it executes the reference solution once per case,
    so per-call dispatch would mean N Redis round-trips instead of one. Its
    arguments and return value are plain str/list/dict, so it pickles cleanly.

    Inside this process Docker IS available, so hidden_testgen's own
    backend_available() gate passes and needs no change.
    """
    from .hidden_testgen import ensure_hidden_testcases
    logger.info("hidden_testgen_job: existing=%d min_hidden=%d",
                len(existing or []), min_hidden)
    return ensure_hidden_testcases(question_text, sample_answer, category, existing,
                                   min_hidden=min_hidden, max_total=max_total)
