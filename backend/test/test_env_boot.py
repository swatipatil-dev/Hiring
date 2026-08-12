"""Loading .env must never be fatal.

In the cPanel deployment ``backend/.env`` is mode 0600 owned by the API user
(it holds SECRET_KEY, the DB password and the Gemini key), while the judge worker
runs as a different user — ``hfjudge``, which has the Docker access the API
deliberately lacks. systemd reads the file as root and injects it via
``EnvironmentFile=``, so by the time the worker's Python starts every variable is
already in ``os.environ``.

An unguarded ``load_dotenv()`` in that process raised
``PermissionError: '/opt/hireflow/backend/.env'`` on import and killed the worker,
which candidates saw as "Judge error". See src/env_boot.py.
"""
import pathlib
import re

import pytest

from src import env_boot


@pytest.fixture(autouse=True)
def _fresh_loader():
    """load_env() memoises success; reset it so each test starts clean."""
    original = env_boot._loaded
    env_boot._loaded = False
    yield
    env_boot._loaded = original


def test_unreadable_env_is_not_fatal(monkeypatch):
    def denied(*a, **k):
        raise PermissionError(13, "Permission denied", "/opt/hireflow/backend/.env")

    monkeypatch.setattr("dotenv.load_dotenv", denied)
    assert env_boot.load_env() is False


def test_other_os_errors_are_not_fatal(monkeypatch):
    """A directory in place of .env, a dead NFS mount — same tolerance."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: (_ for _ in ()).throw(
        IsADirectoryError(21, "Is a directory")))
    assert env_boot.load_env() is False


def test_successful_load_reports_true_and_memoises(monkeypatch):
    calls = []
    monkeypatch.setattr("dotenv.load_dotenv",
                        lambda *a, **k: calls.append(1) or True)
    assert env_boot.load_env() is True
    assert env_boot.load_env() is True
    assert len(calls) == 1, "should not re-read .env on every call"


def test_failure_is_retried_not_cached(monkeypatch):
    """A denied read must not poison a later process that CAN read the file."""
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: (_ for _ in ()).throw(
        PermissionError(13, "Permission denied")))
    assert env_boot.load_env() is False
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: True)
    assert env_boot.load_env() is True


# ── nothing in the worker's import chain may bypass this ─────────────────────

_BACKEND = pathlib.Path(__file__).resolve().parent.parent
_DIRECT_CALL = re.compile(r"^\s*load_dotenv\s*\(", re.MULTILINE)

# Standalone scripts, always run by the user that owns .env — not imported by the
# worker, so a hard failure there is honest rather than mysterious.
_EXEMPT = {"alembic/env.py", "create_departments.py"}


def _application_modules():
    for sub in ("src", "routes", "proctoring"):
        yield from (_BACKEND / sub).rglob("*.py")
    yield from _BACKEND.glob("*.py")


def test_no_application_module_calls_load_dotenv_directly():
    offenders = []
    for path in _application_modules():
        rel = path.relative_to(_BACKEND).as_posix()
        if rel in _EXEMPT or rel == "src/env_boot.py":
            continue
        if _DIRECT_CALL.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(rel)

    assert not offenders, (
        "use src.env_boot.load_env() instead — a bare load_dotenv() crashes the "
        f"judge worker, which cannot read the 0600 .env: {offenders}"
    )
