"""Fullscreen enforcement during a proctored exam (proctoring/routes.py).

The exam runs fullscreen so a candidate cannot read another tab alongside the
questions. Fullscreen cannot actually *prevent* that — browsers guarantee Escape and
F11 always work and no page may suppress them — so the integrity story is that leaving
is conspicuous: the client hides the questions, and each exit is recorded and warned
about, the same as a tab switch. Exits never end the test; a person reviews them.

These pin the server half of that: the per-flag count, the rule that exits never
terminate, and the rule that a browser which simply cannot go fullscreen is recorded but
never punished.

The endpoints are called directly rather than through the app, so no models load and no
database is touched: `db_session_id = None` makes `_persist_violation` a no-op, which is
the same path a session takes before its DB row exists.
"""
import asyncio
from types import SimpleNamespace

import pytest

# proctoring/routes.py imports cv2 at module scope, and the CI image installs
# requirements-ci.txt, which deliberately carries no ML extras — so importing this module
# there raises ModuleNotFoundError and takes the whole collection down with it. Skip
# rather than fail, the same guard test_proctor_model_sharing.py uses.
#
# The cost is real and worth naming: these tests do not run in CI. Every cv2 use in
# routes.py is inside a function, so a lazy import there would make the whole proctoring
# HTTP layer CI-testable — but that is a change to a live service, not something to do
# in passing to turn one check green.
pytest.importorskip("cv2", reason="proctoring extras not installed")

from proctoring.routes import (   # noqa: E402  (must follow the guard above)
    FLAG_MODULE,
    FLAG_SEVERITY,
    FullscreenExitRequest,
    FullscreenUnavailableRequest,
    _sessions,
    record_fullscreen_exit,
    record_fullscreen_unavailable,
)

TERMINATE_STRIKES = 3

# Every per-session endpoint checks that the caller started the session.
OWNER = SimpleNamespace(id="fs-owner", role="candidate", company_id="co")


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
        "owner_user_id": OWNER.id,
    }
    yield sid
    _sessions.pop(sid, None)


def _exit(sid, count):
    return _run(record_fullscreen_exit(sid, FullscreenExitRequest(count=count), current_user=OWNER))


# ── Warning-only policy ──────────────────────────────────────────────────────

def test_an_exit_is_recorded_and_warned_about(session):
    r = _exit(session, 1)
    assert r["occurrence"] == 1
    assert r["warning"] is True
    assert r["terminate"] is False


def test_exits_never_end_the_test(session):
    """Leaving fullscreen is evidence for a reviewer, not grounds to stop the test —
    however many times it happens."""
    for n in range(1, 11):
        r = _exit(session, n)
        assert r["terminate"] is False, f"exit {n} must not end the test"
        assert r["occurrence"] == n
    assert not _sessions[session].get("terminated")


def test_exits_do_not_borrow_the_tab_switch_counter(session):
    """Separate flags on purpose. On some browsers leaving fullscreen also backgrounds
    the tab, and a shared counter would charge one act twice."""
    _sessions[session]["strikes"]["TAB_SWITCH"] = 2
    r = _exit(session, 1)
    assert r["occurrence"] == 1, "fullscreen exits start their own count"
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
        session, FullscreenUnavailableRequest(reason="unsupported"), current_user=OWNER))
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
