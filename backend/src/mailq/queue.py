"""Outbound email queue (RQ + Redis) with a sync-inline fallback.

Mirrors ``src/judge/queue.py``. ``enqueue_email(payload)`` pushes a send job onto
the RQ ``email`` queue; when Redis is unavailable (or ``EMAIL_SYNC=1``) it runs the
send **inline** so the platform still works with no queue infra (dev). Note: the
per-sender rate limiting only applies on the async (Redis) path.
"""
from __future__ import annotations

import os

_REDIS_URL = os.getenv(
    "EMAIL_REDIS_URL",
    os.getenv("JUDGE_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0")),
)
_QUEUE_NAME = os.getenv("EMAIL_QUEUE", "email")
_JOB_TIMEOUT = int(os.getenv("EMAIL_JOB_TIMEOUT_S", "60"))

_redis = None          # decode_responses=True  — plain string keys (rate limits, llen)
_redis_raw = None      # decode_responses=False — RQ (pickled payloads)
_redis_checked = False
_redis_ok = False


def get_redis():
    """Lazily create a shared Redis client (or None if redis-py isn't installed).

    Decodes to str — for our own string/counter keys. NOT for RQ; see get_redis_raw().
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

    RQ stores job arguments and results as pickle, so a client with
    ``decode_responses=True`` breaks on the first binary read. Same reasoning as
    ``src/judge/queue.py:get_redis_raw`` — see the fuller note there.
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
    """Cached liveness check so we don't ping Redis on every send."""
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
    return os.getenv("EMAIL_SYNC", "").lower() in ("1", "true", "yes")


def enqueue_email(payload: dict) -> dict:
    """Queue one email for delivery; returns the dispatch mode.

    Falls back to sending inline when Redis is down or EMAIL_SYNC is set.
    """
    if _sync_forced() or not redis_available():
        from .worker import send_email_job
        res = send_email_job(payload) or {}
        return {"mode": "sync", **res}

    from rq import Queue
    q = Queue(_QUEUE_NAME, connection=get_redis_raw())
    job = q.enqueue(
        "src.mailq.worker.send_email_job",
        payload,
        job_timeout=_JOB_TIMEOUT,
        result_ttl=3600,
        failure_ttl=86400,   # keep failures a day for dead-letter inspection
    )
    return {"mode": "async", "job_id": job.id}
