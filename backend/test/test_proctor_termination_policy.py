"""Which proctoring signals may end a test (proctoring/routes.py::_evaluate_strikes).

Policy: a test ends automatically only on strong evidence that someone else is involved —
a second face or a phone in view (after three warnings each), or a face that keeps not
matching the reference for IDENTITY_MISMATCH_STRIKE_SEC. Every other signal — leaving the
frame, pose-model phone gestures, gaze, head turns, background audio, tab switches,
leaving fullscreen — is warned about and recorded for a human reviewer, and never ends
the test, however often it happens.

Driven directly with a fake socket and a synthetic frame result: no models, no database.
"""
import asyncio
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("cv2", reason="proctoring extras not installed")

from proctoring import routes  # noqa: E402

STRIKES = 3
MISMATCH_SEC = 300.0


class FakeSocket:
    def __init__(self):
        self.sent = []

    async def send_json(self, msg):
        self.sent.append(msg)

    def events(self, name):
        return [m for m in self.sent if m.get("event") == name]


def _entry():
    return {
        "pipeline": SimpleNamespace(cfg=SimpleNamespace(
            TERMINATE_STRIKES=STRIKES, IDENTITY_MISMATCH_STRIKE_SEC=MISMATCH_SEC,
            TERMINATION_GRACE_SEC=0.0,
        )),
        "session_started_at": time.time() - 60,
        "db_session_id": None,
        "strikes": {}, "active_serious": set(), "terminated": False,
        "mismatch_since": None,
    }


def _frame(*flags, face=True):
    return SimpleNamespace(flags=list(flags), face_result={"face_detected": face})


def _run_episodes(flag, episodes, **frame_kw):
    """Show `flag` for `episodes` separate episodes (flag on, then off)."""
    entry, ws = _entry(), FakeSocket()
    for _ in range(episodes):
        asyncio.run(routes._evaluate_strikes("s", entry, _frame(flag, **frame_kw), ws))
        asyncio.run(routes._evaluate_strikes("s", entry, _frame(**frame_kw), ws))
    return entry, ws


@pytest.mark.parametrize("flag", ["MULTIPLE_FACES", "PHONE_DETECTED"])
def test_second_face_or_phone_ends_the_test_after_three_warnings(flag):
    entry, ws = _run_episodes(flag, STRIKES)
    assert not entry["terminated"], "three episodes are three warnings"
    assert len(ws.events("warning")) >= STRIKES

    entry, ws = _run_episodes(flag, STRIKES + 1)
    assert entry["terminated"]
    assert ws.events("terminate")[0]["reason"] == flag


@pytest.mark.parametrize("flag,frame_kw", [
    ("NO_FACE", {"face": False}),
    ("CANDIDATE_LEFT", {}),
    ("PHONE_GESTURE", {}),
    ("SUSPICIOUS_GAZE", {}),
    ("HEAD_TURNED", {}),
    ("MULTIPLE_VOICES", {}),
    ("BACKGROUND_NOISE", {}),
    ("PERSON_DETECTED", {}),
])
def test_other_signals_warn_but_never_end_the_test(flag, frame_kw, monkeypatch):
    # Let every frame's warning through (they're throttled to ~1/sec in production).
    monkeypatch.setattr(time, "time", _ticker())
    entry, ws = _run_episodes(flag, 25, **frame_kw)
    assert not entry["terminated"], f"{flag} must never end a test"
    assert not ws.events("terminate")
    warned = [m for m in ws.events("warning") if flag in m.get("flags", [])]
    assert warned, f"{flag} should still warn the candidate"
    assert all(m.get("severity") == "minor" for m in warned), "no numbered strike warning"


def test_a_sustained_identity_mismatch_still_counts(monkeypatch):
    clock = _ticker(step=MISMATCH_SEC + 1)
    monkeypatch.setattr(time, "time", clock)
    entry, ws = _entry(), FakeSocket()
    entry["session_started_at"] = 0
    for _ in range(2 * (STRIKES + 1) + 1):
        asyncio.run(routes._evaluate_strikes("s", entry, _frame("IDENTITY_MISMATCH"), ws))
    assert entry["terminated"]
    assert ws.events("terminate")[0]["reason"] == "IDENTITY_MISMATCH"


def test_termination_writes_no_verdict(monkeypatch):
    """Terminated is a status for a reviewer, not a conclusion: the session row gets
    status="terminated" and its decision is left alone (it used to be set to CHEATING)."""
    import contextlib
    row = SimpleNamespace(status="active", ended_at=None, decision=None)

    class FakeDB:
        def query(self, *_):
            return self

        def filter(self, *_):
            return self

        def first(self):
            return row

        def commit(self):
            pass

    monkeypatch.setattr(routes, "_db_session", contextlib.contextmanager(lambda: (yield FakeDB())))
    monkeypatch.setattr(routes, "_persist_violation", lambda *a, **k: None)
    entry = _entry()
    entry["db_session_id"] = "db-1"

    assert routes._mark_session_terminated(entry, "PHONE_DETECTED") is True
    assert row.status == "terminated"
    assert row.ended_at is not None
    assert row.decision is None


def _ticker(step=2.0):
    t = {"now": 1_000_000.0}

    def now():
        t["now"] += step
        return t["now"]
    return now
