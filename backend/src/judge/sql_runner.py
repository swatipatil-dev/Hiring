"""
In-process SQL judge (SQLite).

NOTE: candidate-code GRADING now runs inside the Docker sandbox via
``sql_sandbox.py`` + ``sql_harness.py`` (for CPU/memory/time isolation). The pure
helpers here (``_query_result``, ``_format_rows``, …) are still used in-process for
TRUSTED test-case generation/repair (``sql_testgen.py``), which runs the reference
query — never candidate input. ``judge_sql`` / ``run_sql_once`` remain as the
reference implementation the harness mirrors (see ``test_judge_sql.py``).

SQL submissions don't fit the stdin/stdout Docker sandbox: there's no program to
compile, just a query to run against a seeded schema. So this runner executes the
candidate's SQL in an ephemeral in-memory SQLite database — created fresh per test
case, seeded from the case's setup script, and discarded immediately after. The DB
never touches disk and is unreachable outside the process, so a destructive query
(DROP/DELETE) only wrecks the candidate's own throwaway copy.

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

import sqlite3
import time
from typing import List, Optional, Tuple

from . import verdict as V
from .runner import JudgeResult, CaseResult, TestCaseInput, RunOnceResult


def _split_statements(sql: str) -> List[str]:
    """Split a SQL script into individual statements on top-level semicolons.

    Quote-aware (single quotes, double quotes) so semicolons inside string
    literals don't split a statement. Good enough for interview-grade SQL.
    """
    statements: List[str] = []
    buf: List[str] = []
    quote: Optional[str] = None
    for ch in sql or "":
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == ";":
            stmt = "".join(buf).strip()
            if stmt:
                statements.append(stmt)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _run_script(conn: sqlite3.Connection, sql: str) -> Tuple[Optional[List[str]], List[tuple]]:
    """Execute every statement in ``sql``; return (columns, rows) of the LAST
    statement that produced a result set (e.g. the trailing SELECT)."""
    cols: Optional[List[str]] = None
    rows: List[tuple] = []
    cur = conn.cursor()
    for stmt in _split_statements(sql):
        cur.execute(stmt)
        if cur.description is not None:
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
    return cols, rows


def _query_result(setup_sql: str, query_sql: str) -> Tuple[Optional[List[str]], List[tuple]]:
    """Seed a fresh in-memory DB with ``setup_sql`` then run ``query_sql``."""
    conn = sqlite3.connect(":memory:")
    try:
        if (setup_sql or "").strip():
            conn.executescript(setup_sql)
        return _run_script(conn, query_sql)
    finally:
        conn.close()


def _format_rows(cols: Optional[List[str]], rows: List[tuple]) -> str:
    """Render a result set as comma-separated columns, one row per line."""
    if not rows:
        return ""
    return "\n".join(
        ",".join("" if v is None else str(v) for v in row) for row in rows
    )


def _rows_equal(a: List[tuple], b: List[tuple]) -> bool:
    """Compare two result sets order-independently (multiset of stringified rows).

    Interview SQL rarely pins row order, so an order-independent match avoids
    false WAs while still catching genuinely different result sets.
    """
    def norm(rows: List[tuple]):
        return sorted(
            tuple("" if v is None else str(v) for v in row) for row in rows
        )
    return norm(a) == norm(b)


def judge_sql(
    candidate_sql: str,
    test_cases: List[TestCaseInput],
    *,
    reference_sql: Optional[str] = None,
) -> JudgeResult:
    """Grade a SQL submission against its test cases. Never raises."""
    max_score = float(sum(max(1, tc.weight) for tc in test_cases)) or 0.0
    cases: List[CaseResult] = []
    score = 0.0
    passed = 0
    total_time = 0

    for tc in sorted(test_cases, key=lambda t: t.ordinal):
        setup = tc.input or ""
        t0 = time.perf_counter()
        try:
            cand_cols, cand_rows = _query_result(setup, candidate_sql)
        except Exception as e:
            elapsed = int((time.perf_counter() - t0) * 1000)
            cases.append(CaseResult(
                test_case_id=tc.id, verdict=V.RE, time_ms=elapsed, memory_kb=None,
                exit_code=1, stdout="", stderr=str(e)[:10000],
                ordinal=tc.ordinal, is_hidden=tc.is_hidden,
            ))
            continue

        ref_rows: Optional[List[tuple]] = None
        if (reference_sql or "").strip():
            try:
                _, ref_rows = _query_result(setup, reference_sql)
            except Exception:
                ref_rows = None

        if ref_rows is not None:
            ok = _rows_equal(cand_rows, ref_rows)
        else:
            ok = V.outputs_match(_format_rows(cand_cols, cand_rows), tc.expected_output or "")

        elapsed = int((time.perf_counter() - t0) * 1000)
        total_time = max(total_time, elapsed)
        vd = V.AC if ok else V.WA
        if vd == V.AC:
            passed += 1
            score += max(1, tc.weight)
        cases.append(CaseResult(
            test_case_id=tc.id, verdict=vd, time_ms=elapsed, memory_kb=None,
            exit_code=0, stdout=_format_rows(cand_cols, cand_rows)[:10000], stderr="",
            ordinal=tc.ordinal, is_hidden=tc.is_hidden,
        ))

    overall = V.aggregate_verdict([c.verdict for c in cases])
    return JudgeResult(
        verdict=overall, score=score, max_score=max_score,
        passed_count=passed, total_count=len(test_cases),
        total_time_ms=total_time, max_memory_kb=None,
        compile_output=None, error_message=None, cases=cases,
    )


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
            time_ms=int((time.perf_counter() - t0) * 1000), timed_out=False,
            compile_error=None, error_message=None,
        )
