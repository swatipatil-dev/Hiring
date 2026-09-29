"""The email worker only runs its own send job, only from JSON, and shares fairly.

The worker holds the key that decrypts every company's mailbox password. Stock RQ runs
any importable function a job names and unpickles its arguments, so anyone who could
write to its Redis could run code there. These pin the same two defences the judge has
(test_rq_safety.py): an allow-list, and JSON instead of pickle.

They also pin the rate limiter's fairness rules: OTPs keep headroom bulk mail can't
use, and on a mailbox the platform shares between companies each company has its own
sub-limit.
"""
import os
import pathlib
import pickle
import re

import pytest
import redis
from rq.serializers import JSONSerializer

from src.mailq import queue as mq
from src.mailq import ratelimit, safety
from src.mailq.safety import ForbiddenJob, SafeEmailJob

_CONN = redis.Redis(host="127.0.0.1", port=1)     # never contacted


def _job(func_path, *args, **kwargs):
    return SafeEmailJob.create(func_path, args=args, kwargs=kwargs, connection=_CONN,
                               serializer=JSONSerializer)


def _payload(**kw):
    base = {"to": "a@b.c", "subject": "s", "body": "b", "reply_to": None,
            "branding": {"company_name": "Acme", "color": "#123456"}, "is_otp": False,
            "cc": ["x@y.z"], "sender": {"host": "smtp.x", "port": 587, "user": "u@x",
                                        "from_email": "u@x", "password_enc": "gAAA"},
            "hourly_limit": 50, "log_id": "log-1", "company_id": "co-1",
            "step_key": "offer_letter"}
    base.update(kw)
    return base


# ── allow-list and serializer ────────────────────────────────────────────────

def test_a_job_naming_any_other_function_is_refused(monkeypatch):
    ran = []
    monkeypatch.setattr(os, "system", lambda cmd: ran.append(cmd) or 0)
    with pytest.raises(ForbiddenJob):
        _job("os.system", "touch /tmp/owned")._execute()
    assert ran == []


def test_the_send_job_runs(monkeypatch):
    import src.mailq.worker as worker
    monkeypatch.setattr(worker, "send_email_job", lambda payload, attempt=0: {"sent": True})
    assert _job(safety.SEND_JOB, _payload())._execute() == {"sent": True}


def test_the_payload_travels_as_json_and_survives_intact():
    payload = _payload()
    job = _job(safety.SEND_JOB, payload, 2)
    func, instance, args, kwargs = JSONSerializer.loads(job.data)
    assert func == safety.SEND_JOB and instance is None
    assert args == [payload, 2]


class _Detonator:
    fired = []

    def __reduce__(self):
        return (_Detonator.fired.append, ("unpickled!",))


def test_a_forged_pickle_payload_is_never_unpickled():
    job = _job(safety.SEND_JOB, _payload())
    job.data = pickle.dumps((safety.SEND_JOB, None, (_Detonator(),), {}))
    with pytest.raises(Exception):
        job._execute()
    assert _Detonator.fired == []


def test_enqueue_and_reschedule_use_the_json_serializer(monkeypatch):
    """Both ways a job reaches the queue must agree with the worker's serializer."""
    import sys
    import types
    import src.mailq.worker as worker

    seen = []

    class FakeQueue:
        def __init__(self, name, connection=None, serializer=None, **kw):
            seen.append(serializer)

        def enqueue(self, func_path, *a, **kw):
            seen.append(func_path)
            return types.SimpleNamespace(id="job-1")

        def enqueue_in(self, delta, func_path, *a, **kw):
            seen.append(func_path)
            return types.SimpleNamespace(id="job-2")

    monkeypatch.setitem(sys.modules, "rq", types.SimpleNamespace(Queue=FakeQueue))
    monkeypatch.setattr(mq, "redis_available", lambda: True)
    monkeypatch.setattr(mq, "_sync_forced", lambda: False)

    assert mq.enqueue_email(_payload())["mode"] == "async"
    worker._reschedule(_payload(), attempt=1, delay=5, reason="test")
    assert seen == [JSONSerializer, safety.SEND_JOB, JSONSerializer, safety.SEND_JOB]


def test_entrypoint_starts_the_email_worker_locked_down():
    script = (pathlib.Path(__file__).resolve().parent.parent / "entrypoint.sh").read_text(
        encoding="utf-8")
    body = re.search(r'if \[ "\$ROLE" = "email-worker" \]; then\n(.*?)\nfi\n',
                     script, re.DOTALL).group(1)
    assert "--job-class src.mailq.safety.SafeEmailJob" in body
    assert "--serializer rq.serializers.JSONSerializer" in body
    assert "resolve_redis_url" in body


# ── its own Redis database ───────────────────────────────────────────────────

@pytest.mark.parametrize("env,expected", [
    ({"REDIS_URL": "redis://:pw@127.0.0.1:6379/0"}, "redis://:pw@127.0.0.1:6379/1"),
    ({"REDIS_URL": "redis://:pw@127.0.0.1:6379/0", "EMAIL_REDIS_DB": "0"},
     "redis://:pw@127.0.0.1:6379/0"),
    ({"REDIS_URL": "redis://h:6379", "EMAIL_REDIS_DB": "4"}, "redis://h:6379/4"),
    ({"REDIS_URL": "redis://h:6379/0", "EMAIL_REDIS_URL": "redis://other:6380/7"},
     "redis://other:6380/7"),
    ({"JUDGE_REDIS_URL": "redis://j:6379/0"}, "redis://j:6379/1"),
    ({"REDIS_URL": "unix:///tmp/redis.sock"}, "unix:///tmp/redis.sock"),
])
def test_email_queue_gets_its_own_redis_database(monkeypatch, env, expected):
    for var in ("REDIS_URL", "JUDGE_REDIS_URL", "EMAIL_REDIS_URL", "EMAIL_REDIS_DB"):
        monkeypatch.delenv(var, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    assert mq.resolve_redis_url() == expected


# ── fairness ─────────────────────────────────────────────────────────────────

class FakeRedis:
    def __init__(self):
        self.store = {}

    def incr(self, k):
        self.store[k] = self.store.get(k, 0) + 1
        return self.store[k]

    def decr(self, k):
        self.store[k] = self.store.get(k, 0) - 1
        return self.store[k]

    def expire(self, k, ttl):
        return True


@pytest.fixture
def fake(monkeypatch):
    r = FakeRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: r)
    monkeypatch.setattr(ratelimit, "PRIORITY_RESERVE_PCT", 20)
    monkeypatch.setattr(ratelimit, "TENANT_HOURLY_LIMIT", 0)
    for var in ("SMTP_USER", "INNOMAX_HR_USER", "INNOMAX_OFFICIAL_USER"):
        monkeypatch.delenv(var, raising=False)
    return r


def test_bulk_mail_leaves_headroom_for_otps(fake):
    account = "own@acme.com"   # a company's own mailbox: no sub-limit, only headroom
    sent = sum(ratelimit.reserve_send(account, 10, "co-1") is not None for _ in range(20))
    assert sent == 8, "bulk mail must stop 20% short of the cap"
    assert ratelimit.reserve_send(account, 10, "co-1", priority=True) is not None
    assert ratelimit.reserve_send(account, 10, "co-1", priority=True) is not None
    assert ratelimit.reserve_send(account, 10, "co-1", priority=True) is None


def test_one_company_cannot_use_up_a_shared_mailbox(fake, monkeypatch):
    monkeypatch.setenv("INNOMAX_HR_USER", "hr@innomax.com")
    for account in (ratelimit.SHARED_ACCOUNT, "HR@innomax.com"):
        a = sum(ratelimit.reserve_send(account, 10, "co-a") is not None for _ in range(20))
        b = sum(ratelimit.reserve_send(account, 10, "co-b") is not None for _ in range(20))
        assert a == 5, "company A is held to half the shared cap"
        assert b == 3, "company B still gets what bulk headroom remains"


def test_a_companys_own_mailbox_has_no_sub_limit(fake):
    assert not ratelimit.is_shared_account("own@acme.com")
    sent = sum(ratelimit.reserve_send("own@acme.com", 10, "co-a") is not None
               for _ in range(20))
    assert sent == 8


def test_a_deferral_gives_back_the_company_slot(fake):
    """If the account is full, the company bucket claimed first must be released."""
    account = ratelimit.SHARED_ACCOUNT
    key = ratelimit._window_key(account)
    fake.store[key] = 8   # account already at the bulk ceiling
    assert ratelimit.reserve_send(account, 10, "co-a") is None
    bucket = ratelimit._window_key(ratelimit._tenant_bucket(account, "co-a"))
    assert fake.store.get(bucket, 0) == 0


def test_release_send_frees_every_claimed_bucket(fake):
    account = ratelimit.SHARED_ACCOUNT
    claimed = ratelimit.reserve_send(account, 10, "co-a")
    assert len(claimed) == 2
    ratelimit.release_send(claimed)
    assert all(v == 0 for v in fake.store.values())


def test_worker_marks_otps_as_priority(monkeypatch):
    import src.mailq.worker as worker
    calls = []
    monkeypatch.setattr(ratelimit, "reserve_send",
                        lambda account, limit, company_id=None, priority=False:
                        calls.append((account, company_id, priority)) or None)
    monkeypatch.setattr(worker, "_reschedule", lambda *a, **k: None)
    monkeypatch.setattr(worker, "_log", lambda *a, **k: None)
    worker.send_email_job(_payload(is_otp=True, sender=None, company_id="co-9"))
    assert calls == [(ratelimit.SHARED_ACCOUNT, "co-9", True)]
