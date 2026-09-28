"""
In-process SQL judge (SQLite).

NOTE: candidate-code GRADING runs inside the Docker sandbox via ``sql_sandbox.py`` +
``sql_harness.py`` (for CPU/memory/time isolation). This module runs the SAME
harness in-process — it is imported, not copied — for test-case generation
(``sql_testgen.py``), which executes LLM-written seed SQL and the reference query in
the API process, and for tests.

The rules live in ``sql_harness.py`` (one implementation, used both here and in the
container): every case gets a fresh in-memory SQLite database; the setup script can
build it but not ATTACH files or load extensions; the graded query is read-only, and
both have time and row limits. See that module's docstring.

Per test case:
  * ``input``           — setup SQL (CREATE TABLE + INSERT …) seeding the schema/data.
  * ``source_code``     — the candidate's query.
  * reference solution  — the question's ``sample_answer`` (a correct query), when known.

Grading compares the candidate's RESULT SET to the reference solution's result set
(run against the same seed). This is far more robust than string-matching a stored
``expected_output``, which is sensitive to column order / formatting. When no
reference is available we fall back to comparing formatted output to ``expected_output``.
"""
from __future__ import annotations

import time
from typing import List, Optional

from . import sql_harness as H
from . import verdict as V
from .runner import JudgeResult, CaseResult, TestCaseInput, RunOnceResult

# The harness's helpers, re-exported under the names sql_testgen and the tests use.
_split_statements = H._split_statements
_query_result = H._query_result
_format_rows = H._format_rows
_rows_equal = H._rows_equal


def harness_job(candidate_sql: str, test_cases: List[TestCaseInput],
                reference_sql: Optional[str] = None) -> dict:
    """The JSON job ``sql_harness.judge`` takes (in-process or on the sandbox's stdin)."""
    return {
        "candidate_sql": candidate_sql or "",
        "reference_sql": reference_sql,
        "cases": [
            {
                "id": tc.id, "input": tc.input or "",
                "expected_output": tc.expected_output or "",
                "weight": int(tc.weight or 1), "ordinal": tc.ordinal,
                "is_sample": bool(tc.is_sample), "is_hidden": bool(tc.is_hidden),
            }
            for tc in test_cases
        ],
    }


def result_from_harness(data: dict, test_cases: List[TestCaseInput],
                        wall_ms: int = 0) -> JudgeResult:
    """Map the harness's JSON result onto the judge's JudgeResult."""
    max_score = float(sum(max(1, tc.weight) for tc in test_cases)) or 0.0
    cases = [
        CaseResult(
            test_case_id=c.get("test_case_id"), verdict=c.get("verdict", V.RE),
            time_ms=int(c.get("time_ms", 0) or 0), memory_kb=None,
            exit_code=0 if c.get("verdict") != V.RE else 1,
            stdout=c.get("stdout", "") or "", stderr=c.get("stderr", "") or "",
            ordinal=int(c.get("ordinal", 0) or 0), is_hidden=bool(c.get("is_hidden")),
        )
        for c in (data.get("cases") or [])
    ]
    return JudgeResult(
        verdict=data.get("verdict", V.PENDING),
        score=float(data.get("score", 0.0) or 0.0), max_score=max_score,
        passed_count=int(data.get("passed_count", 0) or 0),
        total_count=int(data.get("total_count", len(test_cases)) or len(test_cases)),
        total_time_ms=int(data.get("total_time_ms", wall_ms) or 0),
        max_memory_kb=None, compile_output=None, error_message=None, cases=cases,
    )


def judge_sql(
    candidate_sql: str,
    test_cases: List[TestCaseInput],
    *,
    reference_sql: Optional[str] = None,
) -> JudgeResult:
    """Grade a SQL submission against its test cases, in-process. Never raises."""
    data = H.judge(harness_job(candidate_sql, test_cases, reference_sql))
    return result_from_harness(data, test_cases)


def run_sql_once(query_sql: str, setup_sql: str = "") -> RunOnceResult:
    """Scratch 'Run Code' for SQL — seed the DB then run the query, returning the
    formatted result set (or the SQL error) for display. No scoring."""
    t0 = time.perf_counter()
    try:
        cols, rows = _query_result(setup_sql, query_sql)
        return RunOnceResult(
            status="ok", stdout=_format_rows(cols, rows), stderr="", exit_code=0,
            time_ms=int((time.perf_counter() - t0) * 1000), timed_out=False,
            compile_error=None, error_message=None,
        )
    except Exception as e:
        return RunOnceResult(
            status="ok", stdout="", stderr=str(e), exit_code=1,
            time_ms=int((time.perf_counter() - t0) * 1000),
            timed_out=isinstance(e, H.QueryTimeout),
            compile_error=None, error_message=None,
        )
