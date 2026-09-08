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


# ── Deploy manifest ────────────────────────────────────────────────────────────
#
# .release.json records what the pipeline actually deployed. It is absent on every
# developer machine, so "not recorded" is the ordinary path through this code and the
# one most likely to break if someone assumes the file is there.

MANIFEST = {
    "version": "v2.4.0",
    "commit": "abc123def456abc123def456abc123def456abcd",
    "frontend_version": "2.4.0",
    "frontend_published": True,
    "deployed_at": "2026-09-08T10:00:00Z",
    "deployed_by": "someone",
    "run_url": "https://example.invalid/run/1",
    "state": "deployed",
}


@pytest.fixture
def manifest(monkeypatch, tmp_path):
    """Point _read_release at a manifest we control."""
    def _write(payload, raw=None):
        f = tmp_path / ".release.json"
        f.write_text(raw if raw is not None else __import__("json").dumps(payload),
                     encoding="utf-8")
        real_open = io.open
        monkeypatch.setattr(
            "builtins.open",
            lambda p, *a, **kw: real_open(f, *a, **kw) if str(p).endswith(".release.json")
            else real_open(p, *a, **kw),
        )
        return f
    return _write


def test_version_endpoint_works_with_no_manifest(client):
    """The local-dev and fresh-install case. This one must never 500."""
    resp = client.get("/api/v1/version")
    assert resp.status_code == 200
    body = resp.json()
    assert body["app_version"] == main.APP_VERSION
    assert body["recorded"] is False
    assert body["release"] is None
    assert body["manifest"] is None
    # Nothing was compared, so the answer is "unknown", not "they match".
    assert body["frontend_matches"] is None


def test_version_endpoint_reports_the_manifest(manifest):
    manifest(MANIFEST)
    summary = main._release_summary()
    assert summary["recorded"] is True
    assert summary["release"] == "v2.4.0"
    assert summary["commit"].startswith("abc123")
    assert summary["deployed_by"] == "someone"
    assert summary["state"] == "deployed"


def test_frontend_skew_is_flagged(manifest):
    """A rollback restores an older bundle, so the frontend can sit behind the API.

    This is the only automatic check in the system for that — it otherwise surfaces as
    a user looking at an old screen against a new API.
    """
    manifest({**MANIFEST, "frontend_version": "2.3.0"})
    assert main._release_summary()["frontend_matches"] is False


def test_matching_frontend_is_not_flagged(manifest):
    manifest({**MANIFEST, "frontend_version": main.APP_VERSION})
    assert main._release_summary()["frontend_matches"] is True


def test_a_corrupt_manifest_reads_as_not_recorded(manifest):
    """Truncated mid-write, or hand-edited badly. Not a reason to stop serving."""
    manifest(None, raw='{"version": "v2.4.0", "commit":')
    assert main._read_release() == {}
    assert main._release_summary()["recorded"] is False


def test_a_manifest_that_is_not_an_object_is_rejected(manifest):
    """Valid JSON, wrong shape — a list would parse and then break every .get()."""
    manifest(None, raw='["v2.4.0"]')
    assert main._read_release() == {}


def test_health_carries_the_release_block(client):
    body = client.get("/health").json()
    assert "release" in body
    assert body["release"]["app_version"] == main.APP_VERSION
    # And the guarantees from the previous commit still hold.
    assert body["app_version"] == main.APP_VERSION
    assert "version" in body
