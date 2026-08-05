"""
Host-side adapter that grades SQL **inside the Docker sandbox**.

SQL used to be judged in-process (``sql_runner.judge_sql``). To give it the same
CPU / memory / wall-clock isolation as the other languages — without losing the
robust result-set comparison — we instead run the grading logic inside the hardened
``hireflow-judge-python`` container via ``sql_harness.py`` (CPython's stdlib ships
SQLite, so no new image is needed).

This module builds a JSON job, ships ``sql_harness.py`` + the job into the sandbox,
and maps the harness's JSON result back to the same ``JudgeResult`` / ``RunOnceResult``
dataclasses the rest of the judge already uses — so callers are drop-in.

Test-case GENERATION still uses the in-process ``sql_runner`` (it's trusted and runs
the reference query, never candidate input).
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from typing import List, Optional

from . import verdict as V
from .runner import JudgeResult, CaseResult, TestCaseInput, RunOnceResult
from .sandbox import run_in_sandbox, SandboxUnavailable

_HARNESS_SRC = os.path.join(os.path.dirname(__file__), "sql_harness.py")
_RUN_CMD = ["python3", "sql_harness.py"]


def _prepare_workdir() -> str:
    workdir = tempfile.mkdtemp(prefix="judgesql_", dir=os.getenv("JUDGE_WORK_ROOT") or None)
    try:
        os.chmod(workdir, 0o777)
    except OSError:
        pass
    shutil.copyfile(_HARNESS_SRC, os.path.join(workdir, "sql_harness.py"))
    return workdir


def judge_sql_sandboxed(
    candidate_sql: str,
    test_cases: List[TestCaseInput],
    *,
    reference_sql: Optional[str] = None,
    image: str,
    time_limit_ms: int = 5000,
    memory_limit_mb: int = 256,
) -> JudgeResult:
    """Grade a SQL submission against its test cases inside the sandbox. Never raises."""
    max_score = float(sum(max(1, tc.weight) for tc in test_cases)) or 0.0
    job = {
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

    workdir = _prepare_workdir()
    try:
        res = run_in_sandbox(
            image, _RUN_CMD, workdir=workdir, stdin=json.dumps(job),
            time_limit_ms=time_limit_ms, memory_mb=memory_limit_mb,
        )
    except SandboxUnavailable as e:
        return JudgeResult(
            verdict=V.PENDING, score=0.0, max_score=max_score,
            passed_count=0, total_count=len(test_cases), total_time_ms=0,
            max_memory_kb=None, compile_output=None, error_message=str(e), cases=[],
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    # Catastrophic container failures (the harness itself never times out on
    # interview-grade data): map to a single overall verdict.
    if res.timed_out:
        return _all(V.TLE, test_cases, max_score, res.wall_ms, "SQL grading timed out.")
    if res.oom_killed:
        return _all(V.MLE, test_cases, max_score, res.wall_ms, "SQL grading exceeded the memory limit.")

    try:
        data = json.loads(res.stdout)
    except (ValueError, TypeError):
        return JudgeResult(
            verdict=V.RE, score=0.0, max_score=max_score,
            passed_count=0, total_count=len(test_cases), total_time_ms=res.wall_ms,
            max_memory_kb=None, compile_output=None,
            error_message=(res.stderr or "SQL harness produced no result.")[:20000], cases=[],
        )

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
        total_time_ms=int(data.get("total_time_ms", res.wall_ms) or 0),
        max_memory_kb=None, compile_output=None, error_message=None, cases=cases,
    )


def _all(verdict: str, test_cases, max_score: float, wall_ms: int, msg: str) -> JudgeResult:
    return JudgeResult(
        verdict=verdict, score=0.0, max_score=max_score, passed_count=0,
        total_count=len(test_cases), total_time_ms=wall_ms, max_memory_kb=None,
        compile_output=None, error_message=msg, cases=[],
    )


def run_sql_once_sandboxed(
    query_sql: str, setup_sql: str = "", *, image: str,
    time_limit_ms: int = 5000, memory_limit_mb: int = 256,
) -> RunOnceResult:
    """Scratch 'Run Code' for SQL inside the sandbox — seed then run, return the
    formatted result set (or the SQL error). No scoring."""
    workdir = _prepare_workdir()
    job = {"candidate_sql": query_sql or "", "reference_sql": None,
           "cases": [{"id": "run", "input": setup_sql or "", "expected_output": "",
                      "weight": 1, "ordinal": 0, "is_sample": True, "is_hidden": False}]}
    try:
        res = run_in_sandbox(
            image, _RUN_CMD, workdir=workdir, stdin=json.dumps(job),
            time_limit_ms=time_limit_ms, memory_mb=memory_limit_mb,
        )
    except SandboxUnavailable as e:
        return RunOnceResult(status="unavailable", stdout="", stderr="", exit_code=-1,
                             time_ms=0, timed_out=False, compile_error=None, error_message=str(e))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    if res.timed_out:
        return RunOnceResult(status="ok", stdout="", stderr="Query timed out.", exit_code=1,
                             time_ms=res.wall_ms, timed_out=True, compile_error=None, error_message=None)
    try:
        data = json.loads(res.stdout)
        c = (data.get("cases") or [{}])[0]
    except (ValueError, TypeError, IndexError):
        return RunOnceResult(status="ok", stdout="", stderr=(res.stderr or "No result.")[:10000],
                             exit_code=1, time_ms=res.wall_ms, timed_out=False,
                             compile_error=None, error_message=None)
    # A SQL error surfaces as a per-case RE with the message in stderr (exit_code stays 0,
    # mirroring the original run_sql_once which never marked the run itself as failed).
    return RunOnceResult(
        status="ok", stdout=c.get("stdout", "") or "", stderr=c.get("stderr", "") or "",
        exit_code=0, time_ms=int(c.get("time_ms", res.wall_ms) or 0), timed_out=False,
        compile_error=None, error_message=None,
    )
