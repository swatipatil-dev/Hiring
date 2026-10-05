"""Hand committed messages to the queue, and keep handing them until they are done.

Two routes onto the queue, both reading the ``email_outbox`` table:

* **After commit** — ``submit`` notes each new message on the session; when that
  session commits, ``_after_root_commit`` publishes them at once from a fresh
  session. Fast path; nothing is published for a transaction that rolls back.
* **The dispatcher** — ``python -m src.email_api.outbox --loop``. Every few
  seconds it claims due rows: ``pending`` ones (Redis was down at commit, a retry
  or deferral came due) and ``published`` ones whose lease ran out (the job was
  lost to a Redis flush or a dead worker). Republishing is safe because the
  worker's claim ignores a message that is already being sent or finished.

The job payload is ``{"message_id", "tenant_id"}`` — no credential, no content.

When Redis is down nothing is published and nothing is sent inline, except
OTP / verification / reset mail (``priority='auth'``): that is delivered in-process
through the very same worker function, rate limit and attempt record included,
because a login code that waits for Redis to come back is a locked-out user.
``EMAIL_SYNC=1`` (development and tests) delivers everything that way.
"""
from __future__ import annotations

import argparse
import logging
import os
import time
import uuid
from datetime import datetime, timedelta
from typing import Iterable, List, Optional, Tuple

from sqlalchemy import bindparam, event, text
from sqlalchemy.orm import Session

from . import constants as C
from . import db as db_mod

logger = logging.getLogger(__name__)

PENDING_KEY = "email_outbox_pending"
PUBLISH_LEASE_S = int(os.getenv("EMAIL_PUBLISH_LEASE_S", "300"))
DISPATCH_INTERVAL_S = float(os.getenv("EMAIL_DISPATCH_INTERVAL_S", "5"))
JOB_TIMEOUT_S = int(os.getenv("EMAIL_JOB_TIMEOUT_S", "60"))
HEARTBEAT_KEY = "email:dispatcher:heartbeat"
_DUE = ("(state = 'pending' AND available_at <= :now) "
        "OR (state = 'published' AND lease_expires_at < :now)")

_IDS = bindparam("ids", expanding=True)

Item = Tuple[str, str, str, bool]   # message_id, tenant_id, priority, defer_inline


def note_pending(session, message_id: str, tenant_id: str, priority: str,
                 defer_inline: bool = False) -> None:
    """Remember a message to publish once ``session`` commits."""
    session.info.setdefault(PENDING_KEY, []).append(
        (message_id, tenant_id, priority, bool(defer_inline)))


@event.listens_for(Session, "after_soft_rollback")
def _after_rollback(session, previous_transaction) -> None:
    # A savepoint rollback (an idempotency race in submit) must not forget
    # messages the outer transaction still holds; only the outermost one does.
    if previous_transaction.parent is None:
        session.info.pop(PENDING_KEY, None)


@event.listens_for(Session, "after_transaction_end")
def _after_root_commit(session, transaction) -> None:
    # Not "after_commit": SQLAlchemy fires that for every SAVEPOINT release as
    # well, which would publish a message before the transaction holding it had
    # committed — invisible to the worker that picks it up. The outermost
    # transaction ending with the list still present means it committed: a
    # rollback has already cleared it in _after_rollback above.
    if transaction.parent is not None or transaction.nested:
        return
    items = session.info.pop(PENDING_KEY, None)
    if items:
        try:
            publish_committed(items)
        except Exception:  # noqa: BLE001 — the dispatcher will publish them
            logger.exception("[EMAIL OUTBOX] post-commit publish failed")


def sync_forced() -> bool:
    return os.getenv("EMAIL_SYNC", "").lower() in ("1", "true", "yes")


def _redis_up() -> bool:
    try:
        from src.mailq.queue import redis_available
        return redis_available()
    except Exception:  # noqa: BLE001
        return False


def publish_committed(items: Iterable[Item]) -> None:
    items = list(items)
    if sync_forced():
        for mid, tid, _prio, _defer in items:
            deliver_inline(mid, tid)
        return
    if _redis_up():
        publish([(mid, tid) for mid, tid, *_ in items])
        return
    auth = [(mid, tid) for mid, tid, prio, defer in items
            if prio == C.PRIORITY_AUTH and not defer]
    for mid, tid in auth:
        deliver_inline(mid, tid)
    waiting = len(items) - len(auth)
    if waiting:
        logger.error("[EMAIL OUTBOX] Redis unavailable: %d message(s) left pending in the "
                     "outbox; the dispatcher will publish them when it is back", waiting)


def kick(message_ids: Iterable[str], tenant_id: str, inline_auth: bool = True) -> None:
    """Publish now, or — for auth mail while Redis is down — deliver in-process.

    For callers that persisted with ``defer_inline=True`` so the SMTP round trip
    could happen in a background task instead of inside the request.
    """
    ids = [m for m in message_ids if m]
    if not ids:
        return
    if sync_forced() or (inline_auth and not _redis_up()):
        for mid in ids:
            deliver_inline(mid, tenant_id)
        return
    publish([(mid, tenant_id) for mid in ids])


def deliver_inline(message_id: str, tenant_id: str) -> None:
    from .worker import deliver_message
    try:
        deliver_message(message_id, tenant_id, worker_id="inline")
    except Exception:  # noqa: BLE001 — the outbox row stays; the dispatcher retries
        logger.exception("[EMAIL OUTBOX] inline delivery of %s failed", message_id)


def _enqueue(message_id: str, tenant_id: str) -> None:
    from src.mailq.queue import email_queue
    from src.mailq.safety import DELIVER_JOB
    email_queue().enqueue(
        DELIVER_JOB, {"message_id": message_id, "tenant_id": tenant_id},
        job_timeout=JOB_TIMEOUT_S, result_ttl=3600, failure_ttl=86400)


def _now() -> datetime:
    return datetime.utcnow()


def publish(pairs: List[Tuple[str, str]], session=None) -> int:
    """Claim the given messages' pending rows and enqueue them. Returns how many."""
    if not pairs:
        return 0
    own = session is None
    session = session or db_mod.open_session()
    try:
        token = uuid.uuid4().hex
        now = _now()
        ids = [mid for mid, _ in pairs]
        session.execute(text(
            "UPDATE email_outbox SET state = 'published', lease_owner = :tok, "
            "lease_expires_at = :exp, publish_count = publish_count + 1, updated_at = :now "
            "WHERE state = 'pending' AND message_id IN :ids"
        ).bindparams(_IDS), {"tok": token, "exp": now + timedelta(seconds=PUBLISH_LEASE_S),
                                         "now": now, "ids": ids})
        claimed = _claimed(session, token)
        session.commit()
        return _enqueue_claimed(session, claimed)
    finally:
        if own:
            session.close()


def _claimed(session, token: str) -> List[Tuple[str, str]]:
    rows = session.execute(text(
        "SELECT message_id, company_id FROM email_outbox "
        "WHERE state = 'published' AND lease_owner = :tok"
    ), {"tok": token}).fetchall()
    return [(r[0], r[1]) for r in rows]


def _enqueue_claimed(session, claimed: List[Tuple[str, str]]) -> int:
    done = 0
    for mid, tid in claimed:
        try:
            _enqueue(mid, tid)
            done += 1
        except Exception as e:  # noqa: BLE001
            logger.warning("[EMAIL OUTBOX] enqueue of %s failed: %s", mid, e)
            session.execute(text(
                "UPDATE email_outbox SET state = 'pending', lease_owner = NULL, "
                "lease_expires_at = NULL, available_at = :at, last_publish_error = :err "
                "WHERE message_id = :mid AND state = 'published'"
            ), {"at": _now() + timedelta(seconds=30), "err": str(e)[:255], "mid": mid})
            session.commit()
    return done


_skip_locked: Optional[bool] = None


def supports_skip_locked(session) -> bool:
    """MySQL 8 and MariaDB 10.6+ have ``SKIP LOCKED``; MariaDB 10.5 does not."""
    global _skip_locked
    if _skip_locked is None:
        try:
            version = session.execute(text("SELECT VERSION()")).scalar() or ""
        except Exception:  # noqa: BLE001
            version = ""
        _skip_locked = _version_has_skip_locked(version)
    return _skip_locked


def _version_has_skip_locked(version: str) -> bool:
    nums = [int(p) for p in version.split("-")[0].split(".")[:2] if p.isdigit()]
    if len(nums) < 2:
        return False
    if "mariadb" in version.lower():
        return tuple(nums) >= (10, 6)
    return nums[0] >= 8


def claim_due(session, limit: int = 100, *, skip_locked: Optional[bool] = None) -> List[Tuple[str, str]]:
    """Atomically claim up to ``limit`` due outbox rows for this dispatcher.

    Safe to run in several dispatchers at once: each row is claimed by exactly
    one token, either under ``SKIP LOCKED`` or by a single conditional UPDATE.
    """
    now = _now()
    token = uuid.uuid4().hex
    params = {"now": now, "tok": token, "n": int(limit),
              "exp": now + timedelta(seconds=PUBLISH_LEASE_S)}
    use_skip = supports_skip_locked(session) if skip_locked is None else skip_locked
    if use_skip:
        ids = [r[0] for r in session.execute(text(
            f"SELECT id FROM email_outbox WHERE {_DUE} ORDER BY available_at "
            "LIMIT :n FOR UPDATE SKIP LOCKED"), params).fetchall()]
        if ids:
            session.execute(text(
                "UPDATE email_outbox SET state = 'published', lease_owner = :tok, "
                "lease_expires_at = :exp, publish_count = publish_count + 1, updated_at = :now "
                "WHERE id IN :ids").bindparams(_IDS),
                {**params, "ids": ids})
    else:
        session.execute(text(
            "UPDATE email_outbox SET state = 'published', lease_owner = :tok, "
            "lease_expires_at = :exp, publish_count = publish_count + 1, updated_at = :now "
            f"WHERE {_DUE} ORDER BY available_at LIMIT :n"), params)
    claimed = _claimed(session, token)
    session.commit()
    return claimed


def backlog(session) -> dict:
    """Numbers for the admin delivery panel."""
    row = session.execute(text(
        "SELECT COUNT(*), MIN(available_at) FROM email_outbox WHERE state = 'pending'"
    )).fetchone()
    pending, oldest = (row[0] or 0), row[1]
    return {"pending": int(pending),
            "oldest_age_s": int((_now() - oldest).total_seconds()) if oldest else None}


def _heartbeat() -> None:
    try:
        from src.mailq.queue import get_redis
        r = get_redis()
        if r:
            r.set(HEARTBEAT_KEY, _now().isoformat(), ex=120)
    except Exception:  # noqa: BLE001
        pass


def heartbeat_age_s() -> Optional[int]:
    try:
        from src.mailq.queue import get_redis, redis_available
        if not redis_available():
            return None
        value = get_redis().get(HEARTBEAT_KEY)
        if not value:
            return None
        return int((_now() - datetime.fromisoformat(value)).total_seconds())
    except Exception:  # noqa: BLE001
        return None


def run_once(limit: int = 100) -> int:
    """One dispatcher pass. Returns how many messages were published."""
    session = db_mod.open_session()
    try:
        if not _redis_up():
            stats = backlog(session)
            if stats["pending"]:
                logger.error("[EMAIL OUTBOX] Redis unavailable: %d pending, oldest %ss",
                             stats["pending"], stats["oldest_age_s"])
            return 0
        _heartbeat()
        claimed = claim_due(session, limit)
        return _enqueue_claimed(session, claimed)
    finally:
        session.close()


def main(argv=None) -> int:  # pragma: no cover - process entry point
    parser = argparse.ArgumentParser(description="Publish due email outbox rows to the queue")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--loop", action="store_true", help="run until stopped")
    mode.add_argument("--once", action="store_true", help="one pass, then exit")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # Mappers only. Not init_db(): the dispatcher must never create or alter
    # tables — that is the API's startup and Alembic's job.
    import src.models.models  # noqa: F401
    if args.once:
        print(f"published {run_once()}")
        return 0
    logger.info("[EMAIL OUTBOX] dispatcher started, interval %ss", DISPATCH_INTERVAL_S)
    while True:
        try:
            n = run_once()
            if n:
                logger.info("[EMAIL OUTBOX] published %d", n)
        except Exception:  # noqa: BLE001 — keep the loop alive; log and carry on
            logger.exception("[EMAIL OUTBOX] dispatcher pass failed")
        time.sleep(DISPATCH_INTERVAL_S)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
