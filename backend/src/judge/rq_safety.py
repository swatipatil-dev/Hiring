"""What the judge worker will agree to run.

The worker is the one process on the box with Docker access, which is
root-equivalent. RQ, left to its defaults, trusts the queue completely: a job names
ANY importable function (``os.system`` would do) and carries its arguments as a
pickle, and unpickling alone can execute code. So anyone able to write to Redis —
another account on this shared server if Redis has no password, or an attacker who
got into the API — could run commands as the worker.

Two changes close that, and both are needed:

* ``SafeJob`` refuses any job whose function is not one of the judge's own entry
  points, before anything is called.
* Jobs and results travel as JSON (``SERIALIZER``), so reading a job off the queue
  can never execute anything. Dataclasses are flattened to dicts on the way in
  (``to_plain``) and rebuilt on the way out (``rebuild_result``).

The worker is started with both (see backend/entrypoint.sh); the API enqueues and
reads with the same serializer (src/judge/queue.py). Redis authentication is the
third layer — see docs/CPANEL_DEPLOYMENT.md.
"""
from __future__ import annotations

import dataclasses
from typing import Any

from rq.job import Job
from rq.serializers import JSONSerializer

SERIALIZER = JSONSerializer

ALLOWED_JOBS = frozenset({
    "src.judge.worker.judge_submission_job",
    "src.judge.worker.run_once_job",
    "src.judge.worker.judge_code_job",
    "src.judge.worker.judge_sql_job",
    "src.judge.worker.hidden_testgen_job",
})


class ForbiddenJob(RuntimeError):
    """A job named a function the judge worker does not run."""


def check_allowed(func_name: str) -> None:
    if func_name not in ALLOWED_JOBS:
        raise ForbiddenJob(f"refusing to run {func_name!r}: not a judge job")


class SafeJob(Job):
    """An RQ Job that only runs the judge's own entry points."""

    def _execute(self) -> Any:
        # func_name comes from the JSON payload; nothing has been imported or called yet.
        check_allowed(self.func_name)
        if self.instance is not None:
            raise ForbiddenJob("refusing to run a bound method")
        return super()._execute()


def to_plain(value: Any) -> Any:
    """Dataclasses (and lists/dicts of them) as JSON-safe plain data."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return dataclasses.asdict(value)
    if isinstance(value, (list, tuple)):
        return [to_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: to_plain(v) for k, v in value.items()}
    return value


def as_test_case(value):
    """A TestCaseInput from either a dict (off the queue) or a TestCaseInput."""
    from .runner import TestCaseInput
    return value if isinstance(value, TestCaseInput) else TestCaseInput(**value)


def rebuild_result(func_path: str, value: Any) -> Any:
    """Turn a job's plain-dict result back into the dataclass its callers expect."""
    if not isinstance(value, dict):
        return value
    from .runner import CaseResult, JudgeResult, RunOnceResult
    name = func_path.rsplit(".", 1)[-1]
    if name == "run_once_job":
        return RunOnceResult(**value)
    if name in ("judge_code_job", "judge_sql_job"):
        data = dict(value)
        data["cases"] = [c if isinstance(c, CaseResult) else CaseResult(**c)
                         for c in data.get("cases") or []]
        return JudgeResult(**data)
    return value
