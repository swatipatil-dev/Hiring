"""Fullscreen enforcement during a proctored exam (proctoring/routes.py).

The exam runs fullscreen so a candidate cannot read another tab alongside the
questions. Fullscreen cannot actually *prevent* that — browsers guarantee Escape and
F11 always work and no page may suppress them — so the integrity story is that leaving
is conspicuous and costly: the client hides the questions, and each exit is a strike
under the same policy that governs tab switches.

These pin the server half of that: the strike counter, the terminate threshold, and the
rule that a browser which simply cannot go fullscreen is recorded but never punished.

The endpoints are called directly rather than through the app, so no models load and no
database is touched: `db_session_id = None` makes `_persist_violation` a no-op, which is
the same path a session takes before its DB row exists.
"""
import asyncio
from types import SimpleNamespace

import pytest

from proctoring.routes import (
    FLAG_MODULE,
    FLAG_SEVERITY,
    FullscreenExitRequest,
    FullscreenUnavailableRequest,
    _sessions,
    record_fullscreen_exit,
    record_fullscreen_unavailable,
)

TERMINATE_STRIKES = 3


def _run(coro):
    """Drive one endpoint coroutine to completion.

    A fresh loop per call, because these endpoints suspend on nothing of their own — and
    it keeps this file consistent with a suite that is otherwise entirely synchronous.
    pytest-asyncio is in requirements-ci.txt but not in every dev environment.
    """
    return asyncio.run(coro)


@pytest.fixture()
def session():
    """A live proctoring session, with nothing that needs a model or a database."""
    sid = "fs-test-session"
    _sessions[sid] = {
        "pipeline": SimpleNamespace(cfg=SimpleNamespace(TERMINATE_STRIKES=TERMINATE_STRIKES)),
        "db_session_id": None,
        "strikes": {},
    }
    yield sid
    _sessions.pop(sid, None)


def _exit(sid, count):
    return _run(record_fullscreen_exit(sid, FullscreenExitRequest(count=count)))


# ── Strike policy ────────────────────────────────────────────────────────────

def test_the_first_exit_warns_rather_than_ending_the_test(session):
    """One slip is not cheating. The candidate gets told what it cost them."""
    r = _exit(session, 1)
    assert r["strike"] == 1
    assert r["max"] == TERMINATE_STRIKES
    assert r["warning"] is True
    assert r["terminate"] is False
    assert r["message"] is None


def test_the_fourth_exit_ends_the_test(session):
    """Three warnings, then out — the same threshold every other serious flag uses."""
    for n in range(1, TERMINATE_STRIKES + 1):
        r = _exit(session, n)
        assert r["terminate"] is False, f"strike {n} should still be a warning"

    final = _exit(session, TERMINATE_STRIKES + 1)
    assert final["terminate"] is True
    assert final["reason"] == "FULLSCREEN_EXIT"
    assert final["message"], "a terminated candidate must be told why"
    assert _sessions[session]["terminated"] is True


def test_exits_do_not_borrow_the_tab_switch_counter(session):
    """Separate flags on purpose. On some browsers leaving fullscreen also backgrounds
    the tab, and a shared counter would charge one act twice."""
    _sessions[session]["strikes"]["TAB_SWITCH"] = 2
    r = _exit(session, 1)
    assert r["strike"] == 1, "fullscreen exits start their own count"
    assert _sessions[session]["strikes"]["TAB_SWITCH"] == 2, "tab-switch count untouched"


def test_an_unknown_session_is_rejected(session):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _exit("no-such-session", 1)
    assert e.value.status_code == 404


# ── Browsers that cannot do it at all ────────────────────────────────────────

def test_an_unsupported_browser_is_recorded_but_never_punished(session):
    """iOS Safari has no element fullscreen. A candidate must not lose a job
    application over the phone they own, so this is a note on the report, not a strike."""
    r = _run(record_fullscreen_unavailable(
        session, FullscreenUnavailableRequest(reason="unsupported")))
    assert r["recorded"] is True
    assert _sessions[session]["strikes"] == {}, "must not count against them"
    assert not _sessions[session].get("terminated")


def test_unavailable_is_the_lightest_severity():
    """It has to reach HR's report — silence would look identical to a clean run — but
    it must not read as misconduct."""
    assert FLAG_SEVERITY["FULLSCREEN_UNAVAILABLE"] == "LOW"
    assert FLAG_MODULE["FULLSCREEN_UNAVAILABLE"] == "behavioral"


# ── Flag registration ────────────────────────────────────────────────────────

def test_exit_carries_the_same_weight_as_a_tab_switch():
    """Same act, different key. If these ever diverge, one route out of the exam
    becomes cheaper than the other."""
    assert FLAG_SEVERITY["FULLSCREEN_EXIT"] == FLAG_SEVERITY["TAB_SWITCH"]
    assert FLAG_MODULE["FULLSCREEN_EXIT"] == FLAG_MODULE["TAB_SWITCH"]
