"""
Submission queue + pub/sub.

* ``enqueue_submission(id)`` pushes a judging job onto the RQ queue backed by
  Redis. When Redis is unavailable (or ``JUDGE_SYNC=1``), it falls back to running
  the judge **inline** so the platform still works in a no-infra dev setup.
* ``publish_status(id, payload)`` / ``subscribe(id)`` implement the real-time
  channel the WebSocket endpoint uses to stream ``queued → running → done`` to the
  browser. Channel name: ``submission:{id}``.
"""
from __future__ import annotations

import importlib
import json
import os
import time
from typing import Optional

from .rq_safety import SERIALIZER, check_allowed, rebuild_result, to_plain

_REDIS_URL = os.getenv("JUDGE_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
_QUEUE_NAME = os.getenv("JUDGE_QUEUE", "judge")
_JOB_TIMEOUT = int(os.getenv("JUDGE_JOB_TIMEOUT_S", "300"))

# How long the HTTP request will wait for a worker to finish. Must stay BELOW the
# reverse proxy's read timeout and above the worst realistic judging time
# (per-case limit x case count). _JOB_TIMEOUT remains the worker-side ceiling.
_WAIT_TIMEOUT = int(os.getenv("JUDGE_WAIT_TIMEOUT_S", "120"))
_POLL_INTERVAL = float(os.getenv("JUDGE_POLL_INTERVAL_S", "0.15"))

_redis = None          # decode_responses=True  — pub/sub JSON
_redis_raw = None      # decode_responses=False — RQ (pickled payloads)
_redis_checked = False
_redis_ok = False


def redis_url_has_password(url: Optional[str] = None) -> bool:
    """Whether the queue's Redis URL carries a password (redis://:pw@host/db)."""
    from urllib.parse import urlparse
    try:
        return bool(urlparse(url or _REDIS_URL).password)
    except ValueError:
        return False


def _queue():
    """The judge queue, JSON-serialised (see src/judge/rq_safety.py)."""
    from rq import Queue
    return Queue(_QUEUE_NAME, connection=get_redis_raw(), serializer=SERIALIZER)


def _channel(submission_id: str) -> str:
    return f"submission:{submission_id}"


def get_redis():
    """Shared Redis client for the JSON pub/sub channel — decodes to str.

    NOT for RQ. See get_redis_raw().
    """
    global _redis
    if _redis is None:
        try:
            import redis  # noqa
            _redis = redis.Redis.from_url(
                _REDIS_URL, socket_connect_timeout=2, socket_timeout=5,
                decode_responses=True,
            )
        except Exception:
            _redis = None
    return _redis


def get_redis_raw():
    """Shared Redis client for RQ — returns bytes, never decodes.

    RQ stores job arguments and results as **pickle**. Handing it a client with
    ``decode_responses=True`` makes every read try to UTF-8 decode binary pickle
    data, which fails as:

        'utf-8' codec can't decode byte 0x9c in position 1: invalid start byte

    (0x9c is pickle protocol framing.) Enqueueing appears to work — writing bytes
    is fine — so the fault only shows up when a result is read back, which is
    exactly what run_in_worker does. Every RQ Queue and Job must use this client.
    """
    global _redis_raw
    if _redis_raw is None:
        try:
            import redis  # noqa
            _redis_raw = redis.Redis.from_url(
                _REDIS_URL, socket_connect_timeout=2, socket_timeout=5,
                decode_responses=False,
            )
        except Exception:
            _redis_raw = None
    return _redis_raw


def redis_available() -> bool:
    """Cached liveness check so we don't ping Redis on every call."""
    global _redis_checked, _redis_ok
    if not _redis_checked:
        _redis_checked = True
        r = get_redis()
        try:
            _redis_ok = bool(r) and r.ping()
        except Exception:
            _redis_ok = False
    return _redis_ok


def _sync_forced() -> bool:
    return os.getenv("JUDGE_SYNC", "").lower() in ("1", "true", "yes")


def sync_forced() -> bool:
    """Public form of the JUDGE_SYNC check, for callers outside this module."""
    return _sync_forced()


def enqueue_submission(submission_id: str) -> dict:
    """Queue a submission for judging; returns the dispatch mode."""
    if _sync_forced() or not redis_available():
        # Inline fallback — blocks the caller while judging. Fine for dev/no-Redis.
        from .worker import judge_submission_job
        judge_submission_job(submission_id)
        return {"mode": "sync", "submission_id": submission_id}

    q = _queue()
    job = q.enqueue(
        "src.judge.worker.judge_submission_job",
        submission_id,
        job_timeout=_JOB_TIMEOUT,
        result_ttl=3600,
        failure_ttl=86400,   # keep failures a day for the dead-letter inspection
    )
    return {"mode": "async", "submission_id": submission_id, "job_id": job.id}


class SandboxUnavailable(RuntimeError):
    """No worker could be reached, so nothing was executed.

    Distinct from "the code ran and failed": callers translate this into the
    judge's existing PENDING/unavailable signal, which the UI already renders as
    a retryable message and which candidate_routes defers rather than scoring 0.
    """


def run_in_worker(func_path: str, *args, wait_s: Optional[int] = None, **kwargs):
    """Execute a sandbox job in the judge worker and return its result.

    Blocks until the worker finishes, so callers keep the synchronous behaviour
    they had when execution was inline — the candidate still gets output in the
    same HTTP response.

    Why this exists: the API process deliberately has no Docker access and no
    write access to JUDGE_WORK_ROOT (Docker group membership is root-equivalent
    and the API is internet-facing). The worker runs as the only user that does.
    See src/judge/worker.py.

    JUDGE_SYNC=1 runs inline instead — for local development, where one user
    holds both Docker and the work directory.

    Raises:
        SandboxUnavailable: Redis is down, or the worker did not finish in time.
            Nothing was executed.
        Exception: whatever the job raised, re-raised here so existing
            try/except blocks around the old inline calls behave unchanged.
    """
    check_allowed(func_path)            # the worker would refuse it anyway
    args = to_plain(list(args))
    kwargs = to_plain(kwargs)

    if _sync_forced():
        return rebuild_result(func_path, _call_locally(func_path, *args, **kwargs))

    if not redis_available():
        # Deliberately NOT falling back to inline: in production that fails with
        # a permission error the candidate would see as an opaque 500. Signalling
        # unavailability routes into the existing graceful-degradation path.
        raise SandboxUnavailable(
            "the code execution service is not reachable (queue unavailable)"
        )

    from rq.job import Job

    q = _queue()
    job = q.enqueue(func_path, *args, job_timeout=_JOB_TIMEOUT,
                    result_ttl=600, failure_ttl=86400, **kwargs)

    deadline = time.monotonic() + (wait_s or _WAIT_TIMEOUT)
    while True:
        # Re-fetch rather than refresh(): with a worker pool the job may be
        # picked up by any process, and a stale local object never updates.
        job = Job.fetch(job.id, connection=get_redis_raw(), serializer=SERIALIZER)
        if job.is_finished:
            return rebuild_result(func_path, job.return_value())
        if job.is_failed:
            raise RuntimeError(job.exc_info or "judge job failed in the worker")
        if time.monotonic() >= deadline:
            try:
                job.cancel()
            except Exception:
                pass
            raise SandboxUnavailable(
                f"code execution did not finish within {wait_s or _WAIT_TIMEOUT}s"
            )
        time.sleep(_POLL_INTERVAL)


def _call_locally(func_path: str, *args, **kwargs):
    """Import and call a job function in this process (JUDGE_SYNC=1 only)."""
    module_path, _, func_name = func_path.rpartition(".")
    module = importlib.import_module(module_path)
    return getattr(module, func_name)(*args, **kwargs)


def publish_status(submission_id: str, payload: dict) -> None:
    """Publish a status update to the submission's channel (no-op without Redis)."""
    r = get_redis()
    if not r:
        return
    try:
        r.publish(_channel(submission_id), json.dumps(payload))
        # Also stash the latest snapshot so a late WS subscriber can catch up.
        r.setex(f"submission_status:{submission_id}", 3600, json.dumps(payload))
    except Exception:
        pass


def latest_status(submission_id: str) -> Optional[dict]:
    r = get_redis()
    if not r:
        return None
    try:
        raw = r.get(f"submission_status:{submission_id}")
        return json.loads(raw) if raw else None
    except Exception:
        return None
