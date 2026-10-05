"""RQ must never be handed a decoding Redis client.

RQ stores job arguments and results as **pickle**. A client created with
``decode_responses=True`` tries to UTF-8 decode those bytes on read and dies with

    'utf-8' codec can't decode byte 0x9c in position 1: invalid start byte

Enqueueing still appears to work (writing bytes is fine), so the fault only
surfaces when a result is read back — i.e. in production, on the candidate's
first "Run Code". Hence these tests assert on the *connection* each RQ object is
built with, not on any queue behaviour.

Both queue modules keep two clients: the decoding one for our own string keys
(pub/sub JSON, rate-limit counters, ``llen`` probes) and the raw one for RQ.
"""
import sys
import types

import pytest

from src.judge import queue as jq
from src.mailq import queue as mq


def _decode_flag(client):
    return client.connection_pool.connection_kwargs.get("decode_responses")


# ── the two clients must actually differ ─────────────────────────────────────

@pytest.mark.parametrize("module", [jq, mq], ids=["judge", "mailq"])
def test_raw_client_does_not_decode(module):
    raw = module.get_redis_raw()
    assert raw is not None, "redis-py should be installed in the test env"
    assert _decode_flag(raw) is False


@pytest.mark.parametrize("module", [jq, mq], ids=["judge", "mailq"])
def test_string_client_still_decodes(module):
    """The JSON/counter paths rely on str, so this one must keep decoding."""
    assert _decode_flag(module.get_redis()) is True


@pytest.mark.parametrize("module", [jq, mq], ids=["judge", "mailq"])
def test_clients_are_distinct_objects(module):
    assert module.get_redis() is not module.get_redis_raw()


# ── every RQ call site must use the raw client ───────────────────────────────

class _Recorder:
    """Stands in for rq.Queue / rq.job.Job, recording the connections used."""

    def __init__(self):
        self.connections = []

    def install(self, monkeypatch, job_finishes=True):
        rec = self

        class FakeQueue:
            def __init__(self, name, connection=None, **kw):
                rec.connections.append(connection)

            def enqueue(self, func_path, *a, **kw):
                return FakeJob()

            def enqueue_in(self, delta, func_path, *a, **kw):
                return FakeJob()

        class FakeJob:
            id = "job-1"
            exc_info = None
            is_finished = job_finishes
            is_failed = False

            def return_value(self):
                return "ok"

            def cancel(self):
                pass

        class FakeJobCls:
            @staticmethod
            def fetch(job_id, connection=None, **kw):
                rec.connections.append(connection)
                return FakeJob()

        monkeypatch.setitem(sys.modules, "rq", types.SimpleNamespace(Queue=FakeQueue))
        monkeypatch.setitem(sys.modules, "rq.job", types.SimpleNamespace(Job=FakeJobCls))


def test_run_in_worker_uses_raw_client(monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(jq, "redis_available", lambda: True)
    monkeypatch.setattr(jq, "_sync_forced", lambda: False)

    assert jq.run_in_worker("src.judge.worker.run_once_job", "python", "pass") == "ok"

    # One for Queue(), one for the Job.fetch() poll.
    assert len(rec.connections) >= 2
    assert all(_decode_flag(c) is False for c in rec.connections)


def test_enqueue_submission_uses_raw_client(monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(jq, "redis_available", lambda: True)
    monkeypatch.setattr(jq, "_sync_forced", lambda: False)

    assert jq.enqueue_submission("sub-1")["mode"] == "async"
    assert rec.connections and all(_decode_flag(c) is False for c in rec.connections)


def test_enqueue_email_uses_raw_client(monkeypatch):
    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(mq, "redis_available", lambda: True)
    monkeypatch.setattr(mq, "_sync_forced", lambda: False)

    assert mq.enqueue_email({"to": "a@b.c", "subject": "s", "body": "b"})["mode"] == "async"
    assert rec.connections and all(_decode_flag(c) is False for c in rec.connections)


def test_email_outbox_publish_uses_raw_client(monkeypatch):
    """The outbox publishes delivery jobs too — easy one to miss."""
    from src.email_api import outbox

    rec = _Recorder()
    rec.install(monkeypatch)
    monkeypatch.setattr(mq, "redis_available", lambda: True)

    outbox._enqueue("msg-1", "co-1")

    assert rec.connections and all(_decode_flag(c) is False for c in rec.connections)
