"""
Judge0 execution backend — an alternative to the local Docker sandbox.

Judge0 (https://github.com/judge0/judge0) is a self-hosted code-execution server
that runs each submission with the `isolate` sandbox (cgroup/namespace), reusing
warm runtimes instead of starting a fresh container per test case. That makes it
~10x more throughput-efficient than the per-`docker run` model in ``sandbox.py`` —
the reason we use it for the cloud / high-traffic path while keeping the Docker
backend for zero-infra local dev.

Selected by ``JUDGE_BACKEND=judge0`` (see ``runner.py``). Talks to the server over
HTTP at ``JUDGE0_URL`` with an optional ``JUDGE0_TOKEN`` (``X-Auth-Token``).

Verdict SEMANTICS are kept identical to the Docker backend: we deliberately do NOT
hand Judge0 the ``expected_output`` (which would use Judge0's own comparison).
Instead we capture stdout and classify it with the SAME ``verdict.outputs_match`` /
``aggregate_verdict`` helpers the Docker path uses, so a submission gets the same
AC/WA/TLE/MLE/RE/CE verdict on either backend.

On any unreachability / transport error we return a PENDING ``JudgeResult`` (or an
``unavailable`` ``RunOnceResult``) — exactly the shape the Docker backend returns
on ``SandboxUnavailable`` — so all the existing graceful-degradation code
(``interview_grading``, ``worker``) keeps working unchanged.
"""
from __future__ import annotations

import base64
import logging
import os
import time
from typing import List, Optional

import requests

from . import verdict as V
from .languages import get_language

logger = logging.getLogger("judge.judge0")

# ── Judge0 status ids (https://ce.judge0.com/#statuses-and-languages-status) ──
_ST_IN_QUEUE = 1
_ST_PROCESSING = 2
_ST_ACCEPTED = 3            # ran to completion, exit 0 (we then diff stdout ourselves)
_ST_WRONG_ANSWER = 4        # only if we passed expected_output (we don't)
_ST_TLE = 5
_ST_CE = 6
_ST_RE_MIN, _ST_RE_MAX = 7, 12   # runtime errors (SIGSEGV/SIGXFSZ/SIGFPE/SIGABRT/NZEC/other)
_ST_INTERNAL = 13
_ST_EXEC_FORMAT = 14


def base_url() -> Optional[str]:
    url = os.getenv("JUDGE0_URL", "").strip().rstrip("/")
    return url or None


def _headers() -> dict:
    h = {"Content-Type": "application/json"}
    token = os.getenv("JUDGE0_TOKEN", "").strip()
    if token:
        h["X-Auth-Token"] = token
    return h


# Availability probe with the same caching policy as ``sandbox.docker_available``:
# a positive result is cached briefly; a negative result is re-probed every call so
# the judge self-heals the moment the (on-demand) Judge0 VM finishes booting.
_ok: Optional[bool] = None
_checked_at: float = 0.0
_TTL = float(os.getenv("JUDGE0_PROBE_TTL_S", "5"))


def judge0_available() -> bool:
    global _ok, _checked_at
    base = base_url()
    if not base:
        return False
    now = time.monotonic()
    if _ok is True and (now - _checked_at) < _TTL:
        return True
    _ok = _probe(base)
    _checked_at = now
    return _ok


def _probe(base: str) -> bool:
    try:
        r = requests.get(f"{base}/about", headers=_headers(),
                         timeout=float(os.getenv("JUDGE0_CONNECT_TIMEOUT_S", "3")))
        return r.status_code == 200
    except Exception:
        return False


# Judge0 CE language ids. Overridable per language via env (JUDGE0_LANG_PYTHON …)
# so a server with a different language build can be pointed at without code edits.
_DEFAULT_LANG_IDS = {"python": 71, "cpp": 54, "javascript": 63, "java": 62}


def _language_id(language_key: str) -> Optional[int]:
    env_key = f"JUDGE0_LANG_{language_key.upper()}"
    raw = os.getenv(env_key)
    if raw and raw.isdigit():
        return int(raw)
    return _DEFAULT_LANG_IDS.get(language_key)


def _b64(s: str) -> str:
    return base64.b64encode((s or "").encode("utf-8", "replace")).decode("ascii")


def _unb64(s: Optional[str]) -> str:
    if not s:
        return ""
    try:
        return base64.b64decode(s).decode("utf-8", "replace")
    except Exception:
        return ""


def _submission_body(language_id: int, source_code: str, stdin: str,
                     time_limit_ms: int, memory_limit_mb: int) -> dict:
    # Judge0 units: cpu/wall time in SECONDS (float), memory in KILOBYTES.
    cpu_s = max(1.0, time_limit_ms / 1000.0)
    return {
        "language_id": language_id,
        "source_code": _b64(source_code),
        "stdin": _b64(stdin),
        "cpu_time_limit": cpu_s,
        "wall_time_limit": cpu_s + float(os.getenv("JUDGE0_WALL_MARGIN_S", "3")),
        "memory_limit": int(memory_limit_mb * 1024),
    }


def _post_batch(base: str, submissions: List[dict]) -> List[str]:
    """Create a batch of submissions; return their tokens (in order)."""
    r = requests.post(
        f"{base}/submissions/batch?base64_encoded=true",
        json={"submissions": submissions}, headers=_headers(),
        timeout=float(os.getenv("JUDGE0_REQUEST_TIMEOUT_S", "20")),
    )
    r.raise_for_status()
    return [row.get("token", "") for row in r.json()]


_FIELDS = "token,status_id,stdout,stderr,compile_output,message,time,memory,exit_code"


def _poll_batch(base: str, tokens: List[str], deadline: float) -> List[dict]:
    """Poll until every token reaches a terminal status or the deadline passes."""
    joined = ",".join(t for t in tokens if t)
    url = (f"{base}/submissions/batch?tokens={joined}"
           f"&base64_encoded=true&fields={_FIELDS}")
    interval = float(os.getenv("JUDGE0_POLL_INTERVAL_S", "0.4"))
    while True:
        r = requests.get(url, headers=_headers(),
                         timeout=float(os.getenv("JUDGE0_REQUEST_TIMEOUT_S", "20")))
        r.raise_for_status()
        rows = r.json().get("submissions", [])
        if rows and all((row.get("status_id") or 0) > _ST_PROCESSING for row in rows):
            return rows
        if time.monotonic() >= deadline:
            return rows
        time.sleep(interval)


def _classify(row: dict, expected: str, memory_limit_mb: int):
    """Map one Judge0 result row to (verdict, stdout, stderr, time_ms, memory_kb, exit_code)."""
    status = row.get("status_id") or _ST_INTERNAL
    stdout = _unb64(row.get("stdout"))
    stderr = _unb64(row.get("stderr"))
    mem_kb = row.get("memory")          # already KB
    mem_kb = int(mem_kb) if mem_kb else None
    time_ms = int(round(float(row.get("time") or 0) * 1000))
    exit_code = row.get("exit_code")
    exit_code = int(exit_code) if exit_code is not None else -1

    if status == _ST_TLE:
        vd = V.TLE
    elif status == _ST_CE:
        vd = V.CE
        stderr = _unb64(row.get("compile_output")) or stderr
    elif _ST_RE_MIN <= status <= _ST_RE_MAX or status == _ST_EXEC_FORMAT:
        # No explicit MLE status in Judge0: treat an OOM-shaped runtime error
        # (memory at/over the cap) as MLE, otherwise RE — mirrors sandbox.py.
        cap_kb = memory_limit_mb * 1024
        vd = V.MLE if (mem_kb and mem_kb >= cap_kb * 0.95) else V.RE
    elif status == _ST_ACCEPTED:
        vd = V.AC if V.outputs_match(stdout, expected) else V.WA
    else:  # _ST_INTERNAL or unknown → judge problem, surface as RE so it's visible
        vd = V.RE
        stderr = stderr or _unb64(row.get("message")) or "judge internal error"
    return vd, stdout, stderr, time_ms, mem_kb, exit_code


def judge_via_judge0(language_key: str, source_code: str, test_cases,
                     *, time_limit_ms: int = 2000, memory_limit_mb: int = 256):
    """Judge a submission against all test cases on Judge0. Returns a ``JudgeResult``
    (lazy-imported from runner) with the same shape/semantics as the Docker path.
    Never raises: on any failure returns a PENDING result so callers degrade."""
    from .runner import JudgeResult, CaseResult

    max_score = float(sum(max(1, tc.weight) for tc in test_cases)) or 0.0

    lang = get_language(language_key)
    if lang.harness == "sql":
        # SQL grading (result-set comparison via sql_harness) is not yet wired for
        # the Judge0 backend — see docs/CPANEL_DEPLOYMENT.md. Degrade rather than
        # mis-grade.
        return JudgeResult(verdict=V.PENDING, score=0.0, max_score=max_score,
                           passed_count=0, total_count=len(test_cases),
                           total_time_ms=0, max_memory_kb=None, compile_output=None,
                           error_message="SQL is not supported on the Judge0 backend yet.",
                           cases=[])

    base = base_url()
    language_id = _language_id(language_key)
    if not base or not language_id or not judge0_available():
        return JudgeResult(verdict=V.PENDING, score=0.0, max_score=max_score,
                           passed_count=0, total_count=len(test_cases),
                           total_time_ms=0, max_memory_kb=None, compile_output=None,
                           error_message=("Judge0 is not reachable. Start the judge VM "
                                          "or set JUDGE0_URL."),
                           cases=[])

    ordered = sorted(test_cases, key=lambda t: t.ordinal)
    try:
        bodies = [_submission_body(language_id, source_code, tc.input or "",
                                   time_limit_ms, memory_limit_mb) for tc in ordered]
        tokens = _post_batch(base, bodies)
        # Deadline = sum of per-case wall budgets + a fixed slack for queueing.
        budget_s = (time_limit_ms / 1000.0 + 3) * max(1, len(ordered)) + 10
        rows = _poll_batch(base, tokens, time.monotonic() + budget_s)
    except Exception as exc:
        logger.warning("judge0 batch failed: %s", exc)
        return JudgeResult(verdict=V.PENDING, score=0.0, max_score=max_score,
                           passed_count=0, total_count=len(test_cases),
                           total_time_ms=0, max_memory_kb=None, compile_output=None,
                           error_message=f"Judge0 error: {exc}", cases=[])

    by_token = {row.get("token"): row for row in rows}

    cases: List[CaseResult] = []
    score = 0.0
    passed = 0
    total_time = 0
    peak_mem: Optional[int] = None
    compile_output: Optional[str] = None

    for tc, token in zip(ordered, tokens):
        row = by_token.get(token) or {}
        vd, stdout, stderr, time_ms, mem_kb, exit_code = _classify(
            row, tc.expected_output or "", memory_limit_mb)
        if vd == V.CE and compile_output is None:
            compile_output = stderr[:20000]
        if vd == V.AC:
            passed += 1
            score += max(1, tc.weight)
        total_time = max(total_time, time_ms)
        if mem_kb is not None:
            peak_mem = mem_kb if peak_mem is None else max(peak_mem, mem_kb)
        cases.append(CaseResult(
            test_case_id=tc.id, verdict=vd, time_ms=time_ms, memory_kb=mem_kb,
            exit_code=exit_code, stdout=stdout[:10000], stderr=stderr[:10000],
            ordinal=tc.ordinal, is_hidden=tc.is_hidden,
        ))

    # A compile error fails identically on every case; report it once as CE.
    if cases and all(c.verdict == V.CE for c in cases):
        return JudgeResult(verdict=V.CE, score=0.0, max_score=max_score,
                           passed_count=0, total_count=len(test_cases),
                           total_time_ms=total_time, max_memory_kb=None,
                           compile_output=compile_output, error_message=None, cases=[])

    overall = V.aggregate_verdict([c.verdict for c in cases])
    return JudgeResult(verdict=overall, score=score, max_score=max_score,
                       passed_count=passed, total_count=len(test_cases),
                       total_time_ms=total_time, max_memory_kb=peak_mem,
                       compile_output=compile_output, error_message=None, cases=cases)


def run_once_via_judge0(language_key: str, source_code: str, stdin: str = "",
                        *, time_limit_ms: int = 5000, memory_limit_mb: int = 256):
    """Scratch 'Run Code' on Judge0. Returns a ``RunOnceResult``; never raises."""
    from .runner import RunOnceResult

    lang = get_language(language_key)
    if lang.harness == "sql":
        return RunOnceResult(status="unavailable", stdout="", stderr="", exit_code=-1,
                             time_ms=0, timed_out=False, compile_error=None,
                             error_message="SQL is not supported on the Judge0 backend yet.")

    base = base_url()
    language_id = _language_id(language_key)
    if not base or not language_id or not judge0_available():
        return RunOnceResult(status="unavailable", stdout="", stderr="", exit_code=-1,
                             time_ms=0, timed_out=False, compile_error=None,
                             error_message="Judge0 is not reachable. Start the judge VM or set JUDGE0_URL.")

    try:
        body = _submission_body(language_id, source_code, stdin or "",
                                time_limit_ms, memory_limit_mb)
        tokens = _post_batch(base, [body])
        rows = _poll_batch(base, tokens,
                           time.monotonic() + time_limit_ms / 1000.0 + 13)
        row = (rows or [{}])[0]
    except Exception as exc:
        logger.warning("judge0 run_once failed: %s", exc)
        return RunOnceResult(status="unavailable", stdout="", stderr="", exit_code=-1,
                             time_ms=0, timed_out=False, compile_error=None,
                             error_message=f"Judge0 error: {exc}")

    status = row.get("status_id") or _ST_INTERNAL
    if status == _ST_CE:
        return RunOnceResult(status="compile_error", stdout="", stderr="", exit_code=-1,
                             time_ms=0, timed_out=False,
                             compile_error=_unb64(row.get("compile_output"))[:20000],
                             error_message=None)
    return RunOnceResult(
        status="ok", stdout=_unb64(row.get("stdout")), stderr=_unb64(row.get("stderr")),
        exit_code=int(row.get("exit_code") or 0),
        time_ms=int(round(float(row.get("time") or 0) * 1000)),
        timed_out=(status == _ST_TLE), compile_error=None, error_message=None,
    )
