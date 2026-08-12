"""entrypoint.sh must keep proctoring's one-worker/one-port guarantee in code.

Proctoring holds each session as a live MediaPipe handle in a process-local dict,
so a session's WebSocket has to reach the process that created it. More than one
worker breaks that (4004), and sharing the API's port breaks the whole service.

Both were once expressed as ``Environment=WEB_CONCURRENCY=1`` and
``Environment=PORT=8001`` in hireflow-proctor.service. That was not enough:
systemd applies ``EnvironmentFile=`` after ``Environment=``, so ``PORT=8000`` in
the shared .env won. Proctoring bound 8000, took the API's port, the API
crash-looped on "Connection in use", and the site kept serving from the proctor
process — while proctoring was unreachable, every face check sat on "Checking…"
and every report came back clean.

These are text assertions on purpose: they hold on Windows, and they check the
property that mattered (nothing overridable in the dispatch) rather than
re-testing gunicorn.
"""
import pathlib
import re

import pytest

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_SCRIPT = _BACKEND / "entrypoint.sh"


@pytest.fixture(scope="module")
def script():
    return _SCRIPT.read_text(encoding="utf-8")


def _branch(script: str, role: str) -> str:
    """The body of one `if [ "$ROLE" = "<role>" ]; then ... fi` block."""
    m = re.search(
        r'if \[ "\$ROLE" = "%s" \]; then\n(.*?)\nfi\n' % re.escape(role),
        script, re.DOTALL,
    )
    assert m, f"no branch for ROLE={role}"
    return m.group(1)


def _exec_line(body: str) -> str:
    """The exec command, with line continuations joined."""
    joined = re.sub(r"\\\n\s*", " ", body)
    m = re.search(r"^\s*exec (.+)$", joined, re.MULTILINE)
    assert m, "branch has no exec"
    return m.group(1)


def _web_exec(script: str) -> str:
    """The web role is the fall-through at the end, so it is the LAST exec."""
    joined = re.sub(r"\\\n\s*", " ", script)
    execs = re.findall(r"^\s*exec (.+)$", joined, re.MULTILINE)
    assert execs, "no exec in entrypoint.sh"
    return execs[-1]


# ── the two correctness constraints ──────────────────────────────────────────

def test_proctor_runs_exactly_one_worker(script):
    assert " -w 1 " in f" {_exec_line(_branch(script, 'proctor'))} "


def test_proctor_binds_8001(script):
    assert ":8001" in _exec_line(_branch(script, "proctor"))


def test_proctor_dispatch_reads_neither_port_nor_concurrency(script):
    """The point of the fix: no variable in the proctor's exec line."""
    cmd = _exec_line(_branch(script, "proctor"))
    for var in ("PORT", "WEB_CONCURRENCY"):
        assert var not in cmd, (
            f"{var} is back in the proctor dispatch — .env overrides the unit "
            "file, so this must not be configurable"
        )


def test_proctor_warns_when_web_concurrency_is_set(script):
    """Silently ignoring an operator's setting is its own trap."""
    body = _branch(script, "proctor")
    assert "WEB_CONCURRENCY" in body and "ignoring" in body


# ── the API keeps its own, configurable, settings ─────────────────────────────

def test_web_role_still_honours_port_and_concurrency(script):
    """Only proctoring is pinned; the API's worker count is a real tuning knob."""
    cmd = _web_exec(script)
    assert "${WEB_CONCURRENCY:-2}" in cmd
    assert "${PORT:-8000}" in cmd


def test_web_and_proctor_ports_differ(script):
    web = _web_exec(script)
    proctor = _exec_line(_branch(script, "proctor"))
    assert "8001" in proctor and "8001" not in web


# ── no role may fall through into the web role ───────────────────────────────

@pytest.mark.parametrize("role", ["worker", "proctor", "email-worker"])
def test_role_branch_cannot_fall_through(script, role):
    """If exec ever failed to replace the process, the next branch would run —
    starting a second API on 8000 and recreating the port collision."""
    body = _branch(script, role)
    assert re.search(r"^\s*exit 1", body, re.MULTILINE), (
        f"ROLE={role} does not terminate; execution could reach the web role"
    )
