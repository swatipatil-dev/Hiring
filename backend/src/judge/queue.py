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

import json
import os
from typing import Optional

_REDIS_URL = os.getenv("JUDGE_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
_QUEUE_NAME = os.getenv("JUDGE_QUEUE", "judge")
_JOB_TIMEOUT = int(os.getenv("JUDGE_JOB_TIMEOUT_S", "300"))

_redis = None
_redis_checked = False
_redis_ok = False


def _channel(submission_id: str) -> str:
    return f"submission:{submission_id}"


def get_redis():
    """Lazily create a shared Redis client (or None if redis-py isn't installed)."""
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


def enqueue_submission(submission_id: str) -> dict:
    """Queue a submission for judging; returns the dispatch mode."""
    if _sync_forced() or not redis_available():
        # Inline fallback — blocks the caller while judging. Fine for dev/no-Redis.
        from .worker import judge_submission_job
        judge_submission_job(submission_id)
        return {"mode": "sync", "submission_id": submission_id}

    from rq import Queue
    q = Queue(_QUEUE_NAME, connection=get_redis())
    job = q.enqueue(
        "src.judge.worker.judge_submission_job",
        submission_id,
        job_timeout=_JOB_TIMEOUT,
        result_ttl=3600,
        failure_ttl=86400,   # keep failures a day for the dead-letter inspection
    )
    return {"mode": "async", "submission_id": submission_id, "job_id": job.id}


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
