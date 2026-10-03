"""Outbound email queue (RQ + Redis) with a sync-inline fallback.

Mirrors ``src/judge/queue.py``. ``enqueue_email(payload)`` pushes a send job onto
the RQ ``email`` queue; when Redis is unavailable (or ``EMAIL_SYNC=1``) it runs the
send **inline** so the platform still works with no queue infra (dev). Note: the
per-sender rate limiting only applies on the async (Redis) path.

Jobs travel as JSON and the worker runs only the send job (src/mailq/safety.py).
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

from .safety import SEND_JOB, SERIALIZER


def resolve_redis_url() -> str:
    """The email queue's Redis URL.

    ``EMAIL_REDIS_URL`` wins. Otherwise it is the shared ``REDIS_URL`` (or
    ``JUDGE_REDIS_URL``) with the database index swapped for ``EMAIL_REDIS_DB``
    (default 1), so email jobs and counters never sit in the judge's database.
    Host, port and password are kept, so one Redis ACL still covers both.
    Set ``EMAIL_REDIS_DB=0`` to share the judge's database deliberately.
    """
    explicit = os.getenv("EMAIL_REDIS_URL")
    if explicit:
        return explicit
    base = os.getenv("REDIS_URL", os.getenv("JUDGE_REDIS_URL", "redis://localhost:6379/0"))
    db = os.getenv("EMAIL_REDIS_DB", "1").strip()
    parts = urlsplit(base)
    if not db.isdigit() or parts.scheme not in ("redis", "rediss"):
        return base   # unix sockets etc.: leave as configured
    return urlunsplit((parts.scheme, parts.netloc, f"/{db}", parts.query, parts.fragment))


_REDIS_URL = resolve_redis_url()
_QUEUE_NAME = os.getenv("EMAIL_QUEUE", "email")
_JOB_TIMEOUT = int(os.getenv("EMAIL_JOB_TIMEOUT_S", "60"))

_redis = None          # decode_responses=True  — plain string keys (rate limits, llen)
_redis_raw = None      # decode_responses=False — RQ (job hashes hold compressed bytes)
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

    RQ zlib-compresses the serialized job data into the job hash, so a client
    with ``decode_responses=True`` breaks on the first binary read even with the
    JSON serializer. Same reasoning as ``src/judge/queue.py:get_redis_raw``.
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


def email_queue():
    """The email queue, JSON-serialised (see src/mailq/safety.py)."""
    from rq import Queue
    return Queue(_QUEUE_NAME, connection=get_redis_raw(), serializer=SERIALIZER)


def enqueue_email(payload: dict, db=None) -> dict:
    """Queue one email for delivery; returns the dispatch mode.

    Falls back to sending inline when Redis is down or EMAIL_SYNC is set. ``db``
    is the caller's session, used only by that inline send to update the queued
    row it flushed (see ``worker._log``); it never goes into the RQ payload.
    """
    if _sync_forced() or not redis_available():
        from .worker import send_email_job
        res = send_email_job(payload, db=db) or {}
        return {"mode": "sync", **res}

    job = email_queue().enqueue(
        SEND_JOB,
        payload,
        job_timeout=_JOB_TIMEOUT,
        result_ttl=3600,
        failure_ttl=86400,   # keep failures a day for dead-letter inspection
    )
    return {"mode": "async", "job_id": job.id}
