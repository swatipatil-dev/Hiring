"""Per-sender hourly rate limiting via a Redis fixed-window counter.

Keyed by the sending account (SMTP user), so each mailbox throttles to its own
mail-server cap. ``reserve`` claims a slot in the current hour; when the window is
full the caller defers the send instead of letting it fail. All operations
fail-open (never block mail because Redis hiccuped).
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .queue import get_redis

DEFAULT_HOURLY_LIMIT = int(os.getenv("EMAIL_HOURLY_LIMIT", "100"))


def _window_key(account: str) -> str:
    now = datetime.now(timezone.utc)
    return f"email:sent:{account}:{now:%Y%m%d%H}"


def seconds_to_next_window() -> int:
    """Seconds until the top of the next hour (when the window resets)."""
    now = datetime.now(timezone.utc)
    return max(1, 3600 - (now.minute * 60 + now.second))


def effective_limit(limit) -> int:
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 0
    return limit if limit > 0 else DEFAULT_HOURLY_LIMIT


def reserve(account: str, limit) -> bool:
    """Atomically claim a send slot in the current hour window.

    Returns False when the window is already full (caller should defer). Returns
    True when Redis is unavailable (fail-open — no throttling without infra).
    """
    r = get_redis()
    if not r:
        return True
    lim = effective_limit(limit)
    key = _window_key(account)
    try:
        n = r.incr(key)
        if n == 1:
            r.expire(key, 7200)   # 2h TTL: covers the window + clock skew
        if n > lim:
            r.decr(key)
            return False
        return True
    except Exception:
        return True


def release(account: str) -> None:
    """Give a reserved slot back (e.g. the send failed and will be retried)."""
    r = get_redis()
    if not r:
        return
    try:
        r.decr(_window_key(account))
    except Exception:
        pass
