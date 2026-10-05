"""Per-sender hourly rate limiting via a Redis fixed-window counter.

Keyed by the sending account (SMTP user), so each mailbox throttles to its own
mail-server cap. ``reserve`` claims a slot in the current hour; when the window is
full the caller defers the send instead of letting it fail. All operations
fail-open (never block mail because Redis hiccuped).

``reserve_send`` adds two fairness rules on top, for the account cap is shared:

* Priority mail (OTPs) may use the whole window; everything else stops
  ``EMAIL_PRIORITY_RESERVE_PCT`` percent short of it, so a bulk send can never use
  up the slots a login code needs.
* On a mailbox the platform shares between companies — the env default
  (``SMTP_USER``) and the Innomax HR/official mailboxes — each company also has its
  own sub-limit, ``EMAIL_TENANT_HOURLY_LIMIT`` (default half the account cap), so
  one company's bulk send can't delay another company's mail. A company's own
  mailbox is its own to exhaust and gets no sub-limit.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from .queue import get_redis

DEFAULT_HOURLY_LIMIT = int(os.getenv("EMAIL_HOURLY_LIMIT", "100"))
PRIORITY_RESERVE_PCT = int(os.getenv("EMAIL_PRIORITY_RESERVE_PCT", "20"))
TENANT_HOURLY_LIMIT = int(os.getenv("EMAIL_TENANT_HOURLY_LIMIT", "0"))  # 0 = half the account cap

# The account key used when no mailbox is configured and the platform SMTP sends.
SHARED_ACCOUNT = "__default__"


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


def is_shared_account(account: str) -> bool:
    """True for a mailbox the platform sends from on behalf of many companies."""
    shared = {SHARED_ACCOUNT.lower()}
    for var in ("SMTP_USER", "INNOMAX_HR_USER", "INNOMAX_OFFICIAL_USER"):
        value = (os.getenv(var) or "").strip().lower()
        if value:
            shared.add(value)
    return (account or "").strip().lower() in shared


def _tenant_bucket(account: str, company_id) -> str:
    return f"{account}|co:{company_id or 'none'}"


def reserve_send(account: str, limit, company_id=None, priority: bool = False,
                 shared=None):
    """Claim a slot for one message, applying the fairness rules in the module doc.

    Returns the list of bucket names claimed (pass it to ``release_send`` to give
    them back), or None when the message must be deferred. Fail-open like
    ``reserve``.

    ``shared`` says outright whether the account sends for several companies;
    the email API knows this from the connection row. Left as None, the legacy
    guess from the environment's mailbox names applies.
    """
    lim = effective_limit(limit)
    claimed = []
    if shared is None:
        shared = is_shared_account(account)
    if not priority and shared:
        tenant_lim = TENANT_HOURLY_LIMIT if TENANT_HOURLY_LIMIT > 0 else max(1, lim // 2)
        bucket = _tenant_bucket(account, company_id)
        if not reserve(bucket, tenant_lim):
            return None
        claimed.append(bucket)

    account_lim = lim if priority else max(1, lim - (lim * PRIORITY_RESERVE_PCT) // 100)
    if not reserve(account, account_lim):
        release_send(claimed)
        return None
    claimed.append(account)
    return claimed


def release_send(buckets) -> None:
    """Give back every bucket a ``reserve_send`` claimed."""
    for bucket in buckets or []:
        release(bucket)
