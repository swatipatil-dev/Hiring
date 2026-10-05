"""Hourly sending limits per upstream account, with a database fallback.

The Redis counters in ``src/mailq/ratelimit.py`` stay the primary mechanism,
keyed by the connection's ``account_key`` so every connection logging in to the
same account shares one bucket, with the same OTP headroom and per-company
fairness on shared platform accounts.

That limiter fails *open* without Redis. The email API no longer sends anything
inline while Redis is down — except auth mail — but that exception must not
become unlimited, so when Redis is unavailable the hour's attempts are counted
from ``email_attempts`` instead. Counting is not atomic; it is a ceiling against
runaway sending, not an exact quota.
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from src.mailq import ratelimit


def _hour_start() -> datetime:
    now = datetime.utcnow()
    return now.replace(minute=0, second=0, microsecond=0)


def _redis_up() -> bool:
    try:
        from src.mailq.queue import redis_available
        return redis_available()
    except Exception:  # noqa: BLE001
        return False


def reserve(db, account_key: str, limit, *, company_id: Optional[str], priority: bool,
            shared: bool) -> Optional[List[str]]:
    """Claim one send slot. Returns buckets to ``release`` later, or None to defer."""
    if _redis_up():
        return ratelimit.reserve_send(account_key, limit, company_id=company_id,
                                      priority=priority, shared=shared)
    return [] if _db_has_room(db, account_key, limit, company_id, priority, shared) else None


def _db_has_room(db, account_key, limit, company_id, priority, shared) -> bool:
    from sqlalchemy import func
    from src.models.models import EmailAttempt
    lim = ratelimit.effective_limit(limit)
    cap = lim if priority else max(1, lim - (lim * ratelimit.PRIORITY_RESERVE_PCT) // 100)
    since = _hour_start()
    base = (db.query(func.count(EmailAttempt.id))
            .filter(EmailAttempt.account_key == account_key, EmailAttempt.started_at >= since))
    if (base.scalar() or 0) >= cap:
        return False
    if shared and not priority:
        tenant_cap = (ratelimit.TENANT_HOURLY_LIMIT if ratelimit.TENANT_HOURLY_LIMIT > 0
                      else max(1, lim // 2))
        mine = base.filter(EmailAttempt.company_id == company_id).scalar() or 0
        if mine >= tenant_cap:
            return False
    return True


def release(claimed: Optional[List[str]]) -> None:
    """Give slots back when nothing reached the provider (a failure to retry)."""
    if claimed:
        ratelimit.release_send(claimed)


def seconds_to_next_window() -> int:
    return ratelimit.seconds_to_next_window()
