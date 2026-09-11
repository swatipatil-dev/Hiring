"""Persist what happened to each outbound email.

Delivery is otherwise unknowable: the send helpers report failure by return
value rather than by raising, most callers discard that return, and retry state
lives only in Redis. This module writes the row that makes "did candidate X get
their offer letter?" answerable from the database.

Two calling contexts, and the difference matters:

* **In a request** — pass the caller's session and ``commit=False``. The row
  joins the caller's transaction, so it rolls back with a failed request and is
  assertable in tests through the ``db`` fixture.
* **In the RQ worker** — pass ``db=None``. A session is opened here, following
  the judge worker (``src/judge/worker.py:72-171``): commit, rollback on error,
  close in ``finally``.

A helper that *always* opened its own session would be untestable: conftest
binds the ``db`` fixture to ``recruitment_test_db`` inside an uncommitted outer
transaction, while ``db_manager`` binds ``DATABASE_URL`` — a different database
locally, and a different connection everywhere. Its writes would escape the test
rollback, and its FK checks would block on the fixture's uncommitted parent rows
until ``innodb_lock_wait_timeout``.

Every function here swallows its own failures. Recording that a message was sent
must never be the reason a message fails to send — the same posture as
``pipeline_notifications``.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# status values, mirroring email_service.delivery_outcome plus the worker's hold
QUEUED = "queued"
SENT = "sent"
FAILED = "failed"
DEFERRED = "deferred"


def new_log_id() -> str:
    """Allocate an id up front so it can travel in the RQ payload."""
    return str(uuid.uuid4())


def _open_session():
    """Open a standalone session for the worker path.

    Deliberately not ``next(get_db())`` — that pulls the session out of the
    FastAPI dependency generator without advancing it, so the generator's
    ``finally: db.close()`` never runs and the connection leaks. See the outage
    note in ``proctoring/routes.py``.
    """
    from src.config.database_config_mysql import db_manager
    return db_manager.get_db_session()


def _apply(row, fields: Dict[str, Any]) -> None:
    for key, value in fields.items():
        if value is not None:
            setattr(row, key, value)


def record(db, *, to_email: str, status: str = QUEUED,
           log_id: Optional[str] = None,
           company_id: Optional[str] = None,
           step_key: Optional[str] = None,
           subject: Optional[str] = None,
           sender_user: Optional[str] = None,
           error: Optional[str] = None,
           error_type: Optional[str] = None,
           attempt: int = 0,
           reference_type: Optional[str] = None,
           reference_id: Optional[str] = None,
           commit: bool = True) -> Optional[str]:
    """Insert one delivery row. Returns its id, or None if the write failed.

    ``db=None`` opens and closes a session here (the worker path). Never raises.
    """
    if not to_email:
        return None
    owned = db is None
    session = _open_session() if owned else db
    if session is None:
        return None
    try:
        from src.models.models import EmailLog
        row = EmailLog(
            id=log_id or new_log_id(),
            company_id=company_id,
            step_key=step_key,
            to_email=to_email[:255],
            subject=(subject or "")[:500] or None,
            sender_user=(sender_user or "")[:255] or None,
            status=status,
            error_type=(error_type or "")[:20] or None,
            error=error,
            attempt=attempt,
            reference_type=reference_type,
            reference_id=reference_id,
            created_at=datetime.utcnow(),
        )
        session.add(row)
        if commit or owned:
            session.commit()
        else:
            session.flush()
        return row.id
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"email_log record failed for {to_email}: {e}")
        try:
            session.rollback()
        except Exception:
            pass
        return None
    finally:
        if owned:
            try:
                session.close()
            except Exception:
                pass


def finalize(db, log_id: Optional[str], *, status: str,
             to_email: Optional[str] = None,
             error: Optional[str] = None,
             error_type: Optional[str] = None,
             attempt: Optional[int] = None,
             sender_user: Optional[str] = None,
             company_id: Optional[str] = None,
             step_key: Optional[str] = None,
             subject: Optional[str] = None,
             commit: bool = True) -> Optional[str]:
    """Update the row ``log_id`` with a terminal (or deferred) outcome.

    Falls back to inserting a fresh row when ``log_id`` is unknown — a send whose
    queued row was never written should still leave evidence rather than
    disappearing. Never raises.
    """
    if not log_id:
        return record(db, to_email=to_email or "", status=status,
                      company_id=company_id, step_key=step_key, subject=subject,
                      sender_user=sender_user, error=error, error_type=error_type,
                      attempt=attempt or 0, commit=commit)

    owned = db is None
    session = _open_session() if owned else db
    if session is None:
        return None
    try:
        from src.models.models import EmailLog
        row = session.query(EmailLog).filter(EmailLog.id == log_id).first()
        if row is None:
            # The queued row is missing (pruned, or the enqueue write failed).
            # Insert it on the session already open here rather than losing the
            # outcome entirely.
            row = EmailLog(id=log_id, to_email=(to_email or "")[:255],
                           created_at=datetime.utcnow())
            session.add(row)
        row.status = status
        row.error = error
        row.error_type = (error_type or "")[:20] or None
        _apply(row, {"attempt": attempt, "sender_user": sender_user,
                     "company_id": company_id, "step_key": step_key,
                     "subject": (subject or "")[:500] or None})
        row.updated_at = datetime.utcnow()
        if commit or owned:
            session.commit()
        else:
            session.flush()
        return row.id
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"email_log finalize failed for {log_id}: {e}")
        try:
            session.rollback()
        except Exception:
            pass
        return None
    finally:
        if owned:
            try:
                session.close()
            except Exception:
                pass
