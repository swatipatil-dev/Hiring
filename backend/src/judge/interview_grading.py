"""
Grade a candidate's code answer for an AI-generated coding question
(`interview_questions` row) against its stored `test_cases` JSON, using the judge
engine. Shared by the immediate per-question endpoint (`/judge/grade-question`)
and the final test submission (`candidate_routes.submit_interview`).
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from . import verdict as V
# TestCaseInput only — the runner's execution functions are no longer called from
# this process; they run in the judge worker via queue.run_in_worker.
from .runner import TestCaseInput

logger = logging.getLogger("judge.interview_grading")

_LANG_ALIASES = {"python3": "python", "py": "python", "node": "javascript",
                 "js": "javascript", "c++": "cpp", "cplusplus": "cpp"}

# Per-test-case limits (NOT the candidate's overall test time, which is in minutes).
_CASE_TIME_MS = 3000
_CASE_MEM_MB = 256

# Shown to candidates when the sandbox can't run (e.g. Docker down). Deliberately
# free of operator jargon — the raw cause is logged server-side instead.
JUDGE_UNAVAILABLE_MESSAGE = (
    "Automatic code checking is temporarily unavailable. Your code is saved — "
    "finish with Submit Test and it will be graded later."
)


def _empty(points: int, message: str | None = None, *, unavailable: bool = False) -> dict:
    return {"verdict": None, "score": 0.0, "max_score": 0.0, "passed": 0,
            "total": 0, "points_awarded": 0, "max_points": points or 0,
            "cases": [], "message": message, "unavailable": unavailable}


def grade_interview_question(question, language: str, source_code: str,
                             *, reveal_hidden: bool = False) -> dict:
    """`question` is the `Question` ORM row (has `.test_cases` JSON + `.points`).

    Returns verdict/score/passed/total + `points_awarded` (per-test-case partial
    credit of the question's points). Hidden-case I/O is withheld unless
    `reveal_hidden` (admin/HR). Never raises — degrades to an error message.
    """
    points = getattr(question, "points", 0) or 0
    lang = _LANG_ALIASES.get((language or "python").lower(), (language or "python").lower())

    try:
        cases_raw = json.loads(question.test_cases) if getattr(question, "test_cases", None) else []
    except Exception:
        cases_raw = []
    if not isinstance(cases_raw, list) or not cases_raw:
        return _empty(points, "This question has no test cases (regenerate it to enable auto-grading).")

    if not (source_code or "").strip():
        return _empty(points, "No code submitted.")

    tcs = [
        TestCaseInput(
            id=str(i), input=c.get("input", "") or "",
            expected_output=c.get("expected_output", "") or "",
            is_sample=bool(c.get("is_sample")), weight=int(c.get("weight", 1) or 1), ordinal=i,
        )
        for i, c in enumerate(cases_raw) if isinstance(c, dict)
    ]

    # Both branches execute in the judge worker, never in this process: the API
    # runs as a user with no Docker access by design (see src/judge/worker.py).
    # run_in_worker blocks until the result is back, so grading stays synchronous.
    from .queue import run_in_worker, SandboxUnavailable
    try:
        if lang == "sql":
            # SQL is graded INSIDE the sandbox (sql_harness.py) against the reference
            # query (sample_answer), comparing result sets order-independently rather
            # than string-matching expected_output.
            from .languages import get_language as _get_lang
            from .sql_testgen import coerce_reference_sql
            result = run_in_worker(
                "src.judge.worker.judge_sql_job",
                source_code, tcs,
                # Only the reference's final SELECT: a reference that builds its own
                # tables would fail against the read-only graded connection, silently
                # dropping grading back to string-matching expected_output.
                coerce_reference_sql(getattr(question, "sample_answer", None)),
                _get_lang("sql").image,
                _CASE_TIME_MS, _CASE_MEM_MB)
        else:
            result = run_in_worker(
                "src.judge.worker.judge_code_job",
                lang, source_code, tcs, _CASE_TIME_MS, _CASE_MEM_MB)
    except SandboxUnavailable as e:
        # Nothing executed. Take the existing unavailable path so the candidate is
        # not scored 0 for an infrastructure problem — candidate_routes defers
        # these for regrade rather than banking a zero.
        logger.warning("judge sandbox unavailable: %s", e)
        return _empty(points, JUDGE_UNAVAILABLE_MESSAGE, unavailable=True)
    except Exception as e:
        logger.exception("grade_interview_question failed")
        return _empty(points, f"Judge error: {e}")

    # Sandbox unavailable (Docker down) — degrade gracefully, don't fake a verdict
    # and don't leak operator jargon to the candidate.
    if result.verdict == V.PENDING and result.error_message:
        logger.warning("judge sandbox unavailable: %s", result.error_message)
        return _empty(points, JUDGE_UNAVAILABLE_MESSAGE, unavailable=True)

    points_awarded = round(points * (result.score / result.max_score)) if result.max_score else 0

    out_cases = []
    for cr in result.cases:
        i = int(cr.test_case_id) if (cr.test_case_id or "").isdigit() else None
        is_sample = bool(cases_raw[i].get("is_sample")) if (i is not None and i < len(cases_raw)) else False
        out_cases.append({
            "ordinal": cr.ordinal, "verdict": cr.verdict, "is_sample": is_sample,
            "time_ms": cr.time_ms, "memory_kb": cr.memory_kb,
            "stdout": (cr.stdout if (reveal_hidden or is_sample) else None),
            "stderr": (cr.stderr if (reveal_hidden or is_sample) else None),
        })

    return {
        "verdict": result.verdict, "score": result.score, "max_score": result.max_score,
        "passed": result.passed_count, "total": result.total_count,
        "points_awarded": points_awarded, "max_points": points,
        "compile_output": result.compile_output, "cases": out_cases, "message": None,
        "error_message": result.error_message, "unavailable": False,
    }
