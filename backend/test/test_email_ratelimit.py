"""Unit tests for the email queue's per-sender hourly rate limiter.

Uses a tiny in-memory fake Redis (monkeypatched into the module) so no real
Redis is needed.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.mailq import ratelimit


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


def test_effective_limit_defaults():
    assert ratelimit.effective_limit(None) == ratelimit.DEFAULT_HOURLY_LIMIT
    assert ratelimit.effective_limit(0) == ratelimit.DEFAULT_HOURLY_LIMIT
    assert ratelimit.effective_limit("nan") == ratelimit.DEFAULT_HOURLY_LIMIT
    assert ratelimit.effective_limit(7) == 7


def test_reserve_allows_up_to_limit_then_defers(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)
    acct = "offers@x.com"
    assert ratelimit.reserve(acct, 3) is True
    assert ratelimit.reserve(acct, 3) is True
    assert ratelimit.reserve(acct, 3) is True
    # 4th in the same window is refused...
    assert ratelimit.reserve(acct, 3) is False
    # ...and the refused attempt did not consume a slot (counter stays at 3).
    key = ratelimit._window_key(acct)
    assert fake.store[key] == 3


def test_release_frees_a_slot(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(ratelimit, "get_redis", lambda: fake)
    acct = "hr@x.com"
    for _ in range(2):
        assert ratelimit.reserve(acct, 2) is True
    assert ratelimit.reserve(acct, 2) is False   # full
    ratelimit.release(acct)                        # give one back
    assert ratelimit.reserve(acct, 2) is True      # now there's room again


def test_no_redis_is_fail_open(monkeypatch):
    monkeypatch.setattr(ratelimit, "get_redis", lambda: None)
    # Without Redis there is no throttle — every send is allowed.
    for _ in range(50):
        assert ratelimit.reserve("any@x.com", 1) is True


def test_seconds_to_next_window_in_range():
    s = ratelimit.seconds_to_next_window()
    assert 1 <= s <= 3600
