"""
Judge runner: compile (if needed) → run each test case in the sandbox →
classify per-case verdict → aggregate.

Pure / DB-free so it can be unit-tested and called from the RQ worker or inline
(synchronous fallback). The worker is responsible for loading test cases from the
DB and persisting the returned result.
"""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from typing import List, Optional

from . import verdict as V
from .languages import get_language
from .sandbox import run_in_sandbox, SandboxResult, SandboxUnavailable

# GNU-time wrapping for peak-memory reporting. Off by default (needs /usr/bin/time
# in the image); the hardened images in judge/images/ install it and the compose
# file sets JUDGE_GNU_TIME=1.
_USE_GNU_TIME = os.getenv("JUDGE_GNU_TIME", "0") == "1"
_METRICS_MARK = "__JUDGE_METRICS_BEGIN__"
_RSS_RE = re.compile(r"Maximum resident set size \(kbytes\):\s*(\d+)")


@dataclass
class TestCaseInput:
    id: Optional[str]
    input: str
    expected_output: str
    weight: int = 1
    ordinal: int = 0
    is_hidden: bool = True
    is_sample: bool = False


@dataclass
class CaseResult:
    test_case_id: Optional[str]
    verdict: str
    time_ms: int
    memory_kb: Optional[int]
    exit_code: int
    stdout: str
    stderr: str
    ordinal: int
    is_hidden: bool


@dataclass
class JudgeResult:
    verdict: str
    score: float
    max_score: float
    passed_count: int
    total_count: int
    total_time_ms: int
    max_memory_kb: Optional[int]
    compile_output: Optional[str]
    error_message: Optional[str]
    cases: List[CaseResult] = field(default_factory=list)


def _wrap_run_cmd(run_cmd: List[str]) -> List[str]:
    """Optionally wrap the run command with GNU time to capture peak RSS.

    Keeps program stdout clean; metrics are appended to stderr after a marker.
    """
    if not _USE_GNU_TIME:
        return run_cmd
    inner = " ".join(_sh_quote(a) for a in run_cmd)
    script = (
        f"/usr/bin/time -v -o /tmp/__m {inner}; rc=$?; "
        f"echo {_METRICS_MARK} 1>&2; cat /tmp/__m 1>&2; exit $rc"
    )
    return ["sh", "-c", script]


def _sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def _split_metrics(stderr: str) -> tuple[str, Optional[int]]:
    """Return (program_stderr, peak_memory_kb) splitting off GNU-time metrics."""
    if _METRICS_MARK not in stderr:
        return stderr, None
    prog, _, metrics = stderr.partition(_METRICS_MARK)
    m = _RSS_RE.search(metrics)
    return prog, (int(m.group(1)) if m else None)


def _classify(res: SandboxResult, expected: str) -> tuple[str, str, Optional[int]]:
    """Map a SandboxResult to (verdict, program_stderr, memory_kb)."""
    prog_stderr, mem_kb = _split_metrics(res.stderr)
    if res.timed_out:
        return V.TLE, prog_stderr, mem_kb
    if res.oom_killed:
        return V.MLE, prog_stderr, mem_kb
    if res.exit_code != 0:
        return V.RE, prog_stderr, mem_kb
    if V.outputs_match(res.stdout, expected):
        return V.AC, prog_stderr, mem_kb
    return V.WA, prog_stderr, mem_kb


def active_backend() -> str:
    """Which execution engine to use: 'docker' (default, local sandbox) or
    'judge0' (remote Judge0 server, for the cloud / high-traffic path)."""
    return (os.getenv("JUDGE_BACKEND", "docker").strip().lower() or "docker")


def backend_available() -> bool:
    """True when the active execution backend can actually run code right now.

    Judge0 is a remote server assumed reachable (each call validates and degrades
    gracefully), so it's always considered available here; Docker must be running
    locally. Callers use this to decide whether to attempt code execution instead
    of hard-checking ``docker_available()`` (which is wrong under the Judge0
    cloud backend, where the API host has no Docker)."""
    if active_backend() == "judge0":
        return True
    from .sandbox import docker_available
    return docker_available()


def execution_available() -> bool:
    """True when code can be executed *somewhere* — here or in the judge worker.

    ``backend_available()`` answers a narrower question: "can THIS process run a
    sandbox right now". In the API process that is False by design, because it has
    no Docker access (see src/judge/worker.py). Callers that merely need to know
    whether execution is possible at all must use this instead, or they silently
    skip work the worker would happily have done — which is how AI-generated test
    cases ended up unvalidated on the cPanel deployment.
    """
    if active_backend() == "judge0":
        return True
    from .queue import sync_forced, redis_available
    if sync_forced():
        # Inline mode: this process must be able to run Docker itself.
        from .sandbox import docker_available
        return docker_available()
    return redis_available()


def judge_submission(
    language_key: str,
    source_code: str,
    test_cases: List[TestCaseInput],
    *,
    time_limit_ms: int = 2000,
    memory_limit_mb: int = 256,
) -> JudgeResult:
    if active_backend() == "judge0":
        from .judge0_client import judge_via_judge0
        return judge_via_judge0(language_key, source_code, test_cases,
                                time_limit_ms=time_limit_ms,
                                memory_limit_mb=memory_limit_mb)
    lang = get_language(language_key)
    if lang.harness == "sql":
        # SQL is graded by sql_harness.py INSIDE the sandbox (no stdin/stdout program).
        from .sql_sandbox import judge_sql_sandboxed
        return judge_sql_sandboxed(source_code, test_cases, image=lang.image,
                                   time_limit_ms=time_limit_ms, memory_limit_mb=memory_limit_mb)
    workdir = tempfile.mkdtemp(prefix="judge_", dir=os.getenv("JUDGE_WORK_ROOT") or None)
    # The sandbox runs as uid 65534 (nobody); make the bind-mounted workdir
    # world-writable so the compile step can emit its binary on Linux hosts.
    try:
        os.chmod(workdir, 0o777)
    except OSError:
        pass

    max_score = float(sum(max(1, tc.weight) for tc in test_cases)) or 0.0

    try:
        # 1) write source
        with open(os.path.join(workdir, lang.source_file), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(source_code)

        # 2) compile (compiled languages only)
        if lang.compile_cmd:
            c = run_in_sandbox(
                lang.image, lang.compile_cmd, workdir=workdir,
                time_limit_ms=lang.compile_time_limit_ms,
                memory_mb=max(memory_limit_mb, 512),  # compilers are memory-hungry
                writable=True,
            )
            if c.exit_code != 0 or c.timed_out:
                return JudgeResult(
                    verdict=V.CE, score=0.0, max_score=max_score,
                    passed_count=0, total_count=len(test_cases),
                    total_time_ms=c.wall_ms, max_memory_kb=None,
                    compile_output=(c.stderr or c.stdout)[:20000],
                    error_message=None, cases=[],
                )

        # 3) run each test case
        run_cmd = _wrap_run_cmd(lang.run_cmd)
        cases: List[CaseResult] = []
        score = 0.0
        passed = 0
        total_time = 0
        peak_mem: Optional[int] = None

        for tc in sorted(test_cases, key=lambda t: t.ordinal):
            res = run_in_sandbox(
                lang.image, run_cmd, workdir=workdir,
                stdin=tc.input or "",
                time_limit_ms=time_limit_ms, memory_mb=memory_limit_mb,
            )
            vd, prog_stderr, mem_kb = _classify(res, tc.expected_output or "")
            if vd == V.AC:
                passed += 1
                score += max(1, tc.weight)
            total_time = max(total_time, res.wall_ms)
            if mem_kb is not None:
                peak_mem = mem_kb if peak_mem is None else max(peak_mem, mem_kb)
            cases.append(CaseResult(
                test_case_id=tc.id, verdict=vd, time_ms=res.wall_ms,
                memory_kb=mem_kb, exit_code=res.exit_code,
                stdout=res.stdout[:10000], stderr=prog_stderr[:10000],
                ordinal=tc.ordinal, is_hidden=tc.is_hidden,
            ))

        overall = V.aggregate_verdict([c.verdict for c in cases])
        return JudgeResult(
            verdict=overall, score=score, max_score=max_score,
            passed_count=passed, total_count=len(test_cases),
            total_time_ms=total_time, max_memory_kb=peak_mem,
            compile_output=None, error_message=None, cases=cases,
        )
    except SandboxUnavailable as e:
        return JudgeResult(
            verdict=V.PENDING, score=0.0, max_score=max_score,
            passed_count=0, total_count=len(test_cases),
            total_time_ms=0, max_memory_kb=None, compile_output=None,
            error_message=str(e), cases=[],
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


@dataclass
class RunOnceResult:
    status: str                 # 'ok' | 'compile_error' | 'unavailable'
    stdout: str
    stderr: str
    exit_code: int
    time_ms: int
    timed_out: bool
    compile_error: Optional[str]
    error_message: Optional[str]


def run_once(
    language_key: str,
    source_code: str,
    stdin: str = "",
    *,
    time_limit_ms: int = 5000,
    memory_limit_mb: int = 256,
) -> RunOnceResult:
    """Compile (if needed) and run the code ONCE against ``stdin`` — a scratch
    "Run Code" used by the candidate editor. No test-case comparison here; the
    caller compares stdout to the expected output if it wants a pass/fail hint.
    """
    if active_backend() == "judge0":
        from .judge0_client import run_once_via_judge0
        return run_once_via_judge0(language_key, source_code, stdin,
                                   time_limit_ms=time_limit_ms,
                                   memory_limit_mb=memory_limit_mb)
    lang = get_language(language_key)
    if lang.harness == "sql":
        # For SQL the 'stdin' carries the setup script (schema + seed data).
        from .sql_sandbox import run_sql_once_sandboxed
        return run_sql_once_sandboxed(source_code, stdin or "", image=lang.image)
    workdir = tempfile.mkdtemp(prefix="judgerun_", dir=os.getenv("JUDGE_WORK_ROOT") or None)
    try:
        os.chmod(workdir, 0o777)
    except OSError:
        pass
    try:
        with open(os.path.join(workdir, lang.source_file), "w",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(source_code)

        if lang.compile_cmd:
            c = run_in_sandbox(
                lang.image, lang.compile_cmd, workdir=workdir,
                time_limit_ms=lang.compile_time_limit_ms,
                memory_mb=max(memory_limit_mb, 512), writable=True,
            )
            if c.exit_code != 0 or c.timed_out:
                return RunOnceResult(
                    status="compile_error", stdout="", stderr="", exit_code=c.exit_code,
                    time_ms=c.wall_ms, timed_out=c.timed_out,
                    compile_error=(c.stderr or c.stdout)[:20000], error_message=None,
                )

        # Raw run command (no GNU-time wrapper) so stdout stays clean for display.
        res = run_in_sandbox(
            lang.image, lang.run_cmd, workdir=workdir, stdin=stdin or "",
            time_limit_ms=time_limit_ms, memory_mb=memory_limit_mb,
        )
        return RunOnceResult(
            status="ok", stdout=res.stdout, stderr=res.stderr, exit_code=res.exit_code,
            time_ms=res.wall_ms, timed_out=res.timed_out, compile_error=None,
            error_message=None,
        )
    except SandboxUnavailable as e:
        return RunOnceResult(
            status="unavailable", stdout="", stderr="", exit_code=-1, time_ms=0,
            timed_out=False, compile_error=None, error_message=str(e),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
