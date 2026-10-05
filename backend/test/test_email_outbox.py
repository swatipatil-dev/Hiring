"""The outbox: publish only after commit, never lose committed work, never send inline
except auth mail while Redis is down."""
from datetime import datetime, timedelta

import pytest

from src.email_api import constants as C
from src.email_api import outbox
from src.email_api.adapters import base as A
from src.models.models import EmailAttempt, EmailOutbox
from test.email_factories import (FakeAdapter, make_company, make_smtp_sender, make_route,
                                  queue_message)


@pytest.fixture()
def co(db):
    c = make_company(db, "Client")
    s, _ = make_smtp_sender(db, c.id)
    make_route(db, c.id, C.DEFAULT_ROUTE, s.id)
    return c


@pytest.fixture()
def adapter(monkeypatch):
    fake = FakeAdapter()
    monkeypatch.setattr(A, "get_adapter", lambda conn, is_test=False: fake)
    return fake


@pytest.fixture()
def redis(monkeypatch):
    """Toggle Redis up/down and capture what would have been enqueued."""
    state = {"up": False, "jobs": [], "fail": False}
    monkeypatch.setattr("src.mailq.queue.redis_available", lambda: state["up"])

    def enqueue(mid, tid):
        if state["fail"]:
            raise ConnectionError("redis went away")
        state["jobs"].append({"message_id": mid, "tenant_id": tid})
    monkeypatch.setattr(outbox, "_enqueue", enqueue)
    monkeypatch.delenv("EMAIL_SYNC", raising=False)
    return state


def _box(db, msg):
    db.expire_all()
    return db.query(EmailOutbox).filter_by(message_id=msg.id).one()


def test_publish_after_commit_carries_ids_only(db, co, redis, adapter):
    redis["up"] = True
    msg = queue_message(db, co.id).message
    assert redis["jobs"] == []          # nothing before commit
    db.commit()
    assert redis["jobs"] == [{"message_id": msg.id, "tenant_id": co.id}]
    box = _box(db, msg)
    assert box.state == "published" and box.publish_count == 1 and box.lease_expires_at
    assert adapter.calls == []          # the worker sends, not the request


def test_savepoint_release_does_not_publish(db, co, redis):
    """SQLAlchemy fires after_commit for SAVEPOINTs; publishing there would hand
    the worker a message its transaction has not committed yet."""
    redis["up"] = True
    queue_message(db, co.id)
    sp = db.begin_nested()
    sp.commit()
    queue_message(db, co.id, subject="second")   # its own savepoint inside submit
    assert redis["jobs"] == []
    db.commit()
    assert len(redis["jobs"]) == 2


def test_rollback_publishes_nothing(db, co, redis):
    redis["up"] = True
    queue_message(db, co.id)
    db.rollback()
    db.commit()
    assert redis["jobs"] == []


def test_redis_down_leaves_work_pending(db, co, redis, adapter):
    msg = queue_message(db, co.id).message
    db.commit()
    assert redis["jobs"] == [] and adapter.calls == []
    assert _box(db, msg).state == "pending"


def test_redis_down_delivers_auth_mail_inline(db, co, redis, adapter):
    otp = queue_message(db, co.id, subject="Code", priority=C.PRIORITY_AUTH, is_sensitive=True).message
    normal = queue_message(db, co.id).message
    db.commit()
    db.expire_all()
    assert otp.status == C.ACCEPTED and len(adapter.calls) == 1
    assert db.query(EmailAttempt).filter_by(message_id=otp.id).count() == 1
    assert normal.status == C.QUEUED


def test_deferred_auth_mail_waits_for_kick(db, co, redis, adapter):
    otp = queue_message(db, co.id, subject="Code", priority=C.PRIORITY_AUTH,
                        defer_inline=True).message
    db.commit()
    assert adapter.calls == []
    outbox.kick([otp.id], co.id)
    db.expire_all()
    assert otp.status == C.ACCEPTED


def test_email_sync_delivers_everything_inline(db, co, redis, adapter, monkeypatch):
    monkeypatch.setenv("EMAIL_SYNC", "1")
    msg = queue_message(db, co.id).message
    db.commit()
    db.expire_all()
    assert msg.status == C.ACCEPTED and redis["jobs"] == []


def test_enqueue_failure_returns_row_to_pending(db, co, redis):
    redis["up"], redis["fail"] = True, True
    msg = queue_message(db, co.id).message
    db.commit()
    box = _box(db, msg)
    assert box.state == "pending" and box.last_publish_error
    assert box.available_at > datetime.utcnow()


@pytest.mark.parametrize("skip_locked", [True, False])
def test_dispatcher_claims_due_and_expired_rows(db, co, redis, skip_locked):
    due = queue_message(db, co.id, subject="due").message
    future = queue_message(db, co.id, subject="future").message
    lost = queue_message(db, co.id, subject="lost").message
    fresh = queue_message(db, co.id, subject="fresh").message
    now = datetime.utcnow()
    rows = {m.id: db.query(EmailOutbox).filter_by(message_id=m.id).one()
            for m in (due, future, lost, fresh)}
    rows[due.id].available_at = now - timedelta(seconds=1)
    rows[future.id].available_at = now + timedelta(hours=1)
    rows[lost.id].state, rows[lost.id].lease_expires_at = "published", now - timedelta(seconds=1)
    rows[fresh.id].state, rows[fresh.id].lease_expires_at = "published", now + timedelta(minutes=5)
    db.flush()
    session = outbox.db_mod.open_session()
    try:
        claimed = outbox.claim_due(session, skip_locked=skip_locked)
    finally:
        session.close()
    claimed_ids = {mid for mid, _ in claimed}
    assert due.id in claimed_ids and lost.id in claimed_ids
    assert future.id not in claimed_ids and fresh.id not in claimed_ids
    assert all(tid == co.id for _, tid in claimed)
    # A second dispatcher finds nothing left to claim.
    session = outbox.db_mod.open_session()
    try:
        again = {mid for mid, _ in outbox.claim_due(session, skip_locked=skip_locked)}
    finally:
        session.close()
    assert not (again & claimed_ids)


def test_run_once_publishes_backlog_when_redis_returns(db, co, redis):
    msg = queue_message(db, co.id).message
    db.commit()
    assert outbox.run_once() == 0                      # still down
    assert _box(db, msg).state == "pending"
    redis["up"] = True
    box = _box(db, msg)
    box.available_at = datetime.utcnow() - timedelta(seconds=1)
    db.flush()
    assert outbox.run_once() == 1
    assert redis["jobs"] == [{"message_id": msg.id, "tenant_id": co.id}]


@pytest.mark.parametrize("version,expected", [
    ("8.0.36", True), ("10.5.22-MariaDB", False), ("10.6.16-MariaDB-log", True),
    ("5.7.44", False), ("", False),
])
def test_skip_locked_detection(version, expected):
    assert outbox._version_has_skip_locked(version) is expected
