"""
RQ worker entrypoint for judging a submission.

``judge_submission_job(submission_id)`` is the function enqueued by
``queue.enqueue_submission`` (and called inline in the synchronous fallback).
It loads the submission + problem + test cases, runs the sandboxed judge, persists
per-case results, updates the submission, and publishes status to the pub/sub
channel the WebSocket streams from.

Run a worker (inside the Linux judge-worker container) with:
    rq worker judge --url redis://redis:6379/0
"""
from __future__ import annotations

import logging
from datetime import datetime

from src.config.database_config_mysql import db_manager
from src.models.models import (
    CodeSubmission, CodingProblem, ProblemTestCase, SubmissionTestResult,
)
from . import verdict as V
from .queue import publish_status
from .runner import TestCaseInput, judge_submission

logger = logging.getLogger("judge.worker")


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
