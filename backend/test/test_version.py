"""
"Exactly which version is running in production?" must have one answer.

Before this there were three hardcoded copies of "2.0.0" in main.py and a
frontend/package.json that said "1.0.0", and the obvious place to look — the
`version` field of /health — was the MySQL server version, not the app's.
"""
import io
import os

import pytest

import main


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(main.__file__)))
VERSION_FILE = os.path.join(REPO_ROOT, "VERSION")


def test_version_file_exists_and_is_a_semver():
    """The release workflow writes this file; everything else reads it."""
    assert os.path.exists(VERSION_FILE), f"no VERSION file at {VERSION_FILE}"
    raw = io.open(VERSION_FILE, encoding="utf-8").read().strip()
    parts = raw.split(".")
    assert len(parts) == 3 and all(p.isdigit() for p in parts), \
        f"VERSION should be X.Y.Z with no leading v, got {raw!r}"


def test_app_version_matches_the_file():
    assert main.APP_VERSION == io.open(VERSION_FILE, encoding="utf-8").read().strip()


def test_health_reports_the_app_version(client):
    resp = client.get("/health")
    assert resp.status_code in (200, 503)
    assert resp.json()["app_version"] == main.APP_VERSION


def test_health_still_reports_the_mysql_version_under_its_old_name(client):
    """The regression that matters.

    /health has returned MySQL's version under the key `version` since it was
    written. The app's release number was given a separate key precisely so this
    one keeps its meaning — taking it over would silently change what every
    existing reader gets.
    """
    body = client.get("/health").json()
    assert "version" in body, "the MySQL version key was removed"
    assert body["version"] != main.APP_VERSION or main.APP_VERSION == "unknown", \
        "`version` now looks like the app version — it should still be MySQL's"


@pytest.mark.parametrize("path", ["/", "/api/v1/info"])
def test_no_endpoint_carries_its_own_copy_of_the_version(client, path):
    """Each of these used to hardcode "2.0.0" independently."""
    assert client.get(path).json()["version"] == main.APP_VERSION


def test_main_py_has_no_hardcoded_version_left():
    src = io.open(main.__file__, encoding="utf-8").read()
    # The literal the three endpoints used to carry. If it reappears, one of them
    # has drifted away from the file again.
    assert '"2.0.0"' not in src


def test_a_missing_version_file_does_not_stop_the_app(monkeypatch):
    """A version string is not worth failing a boot over.

    Patched rather than deleted — removing the real file would break every other
    test in the session, and the point is the fallback, not the filesystem.
    """
    def explode(*a, **kw):
        raise OSError("no such file")

    monkeypatch.setattr("builtins.open", explode)
    assert main._read_version() == "unknown"


def test_an_empty_version_file_reads_as_unknown(tmp_path, monkeypatch):
    """An empty file is a broken release, not version "" — say so."""
    real_open = io.open
    empty = tmp_path / "VERSION"
    empty.write_text("   \n", encoding="utf-8")
    monkeypatch.setattr(
        "builtins.open",
        lambda p, *a, **kw: real_open(empty, *a, **kw),
    )
    assert main._read_version() == "unknown"


def test_frontend_package_json_agrees_with_the_version_file():
    """Both are written by the release workflow; drift means it half-ran."""
    import json
    pkg = os.path.join(REPO_ROOT, "frontend", "package.json")
    version = io.open(VERSION_FILE, encoding="utf-8").read().strip()
    assert json.load(io.open(pkg, encoding="utf-8"))["version"] == version
