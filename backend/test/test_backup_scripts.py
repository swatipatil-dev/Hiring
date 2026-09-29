"""The backup and restore-drill scripts' own logic, without MySQL or gpg.

The end-to-end drill (mysqldump -> gpg -> restore -> checks) is run by hand against a
scratch database — see docs/BACKUP_AND_RESTORE.md. These pin the decisions that must not
regress quietly: never restoring over the live database, which bundles retention keeps,
and what counts as a broken backup versus a reference that was already dangling.
"""
import importlib.util
import io
import sys
import tarfile
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))   # restore_drill imports backup as a sibling


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


backup = _load("backup")
drill = _load("restore_drill")

LIVE = {"host": "h", "port": 3306, "user": "u", "password": "p", "database": "acct_hireflow"}


# ── never the live database ──────────────────────────────────────────────────

@pytest.mark.parametrize("target", ["acct_hireflow", "ACCT_HIREFLOW", " acct_hireflow "])
def test_drill_refuses_the_live_database(target):
    with pytest.raises(drill.DrillError, match="live database"):
        drill.check_target_is_safe(target, LIVE)


def test_drill_refuses_a_protected_database():
    with pytest.raises(drill.DrillError, match="RESTORE_PROTECTED_DBS"):
        drill.check_target_is_safe("acct_restore_prod", LIVE, ["acct_restore_prod"])


def test_drill_refuses_a_name_that_is_not_obviously_scratch():
    with pytest.raises(drill.DrillError, match="drill"):
        drill.check_target_is_safe("acct_hireflow2", LIVE)


def test_default_target_keeps_the_cpanel_prefix_and_is_safe():
    target = drill.default_target(LIVE)
    assert target == "acct_hireflow_drill"
    drill.check_target_is_safe(target, LIVE)


# ── retention ────────────────────────────────────────────────────────────────

def _names(stamps):
    return [f"hireflow-{s}.tar.gpg" for s in stamps]


def test_retention_keeps_seven_daily_and_four_weekly():
    # 40 consecutive nightly bundles ending Wed 2026-09-30.
    import datetime as dt
    end = dt.datetime(2026, 9, 30, 2, 30)
    stamps = [(end - dt.timedelta(days=i)).strftime("%Y%m%dT%H%M%SZ") for i in range(40)]
    keep = backup.bundles_to_keep(_names(stamps))
    daily = set(_names(stamps[:7]))
    assert daily <= keep
    # Daily covers Sep 24-30 (ISO weeks 39 and part of 38). Weekly then adds the newest of
    # the 4 weeks before: Sun Sep 20, 13, 6 and Aug 30.
    weekly = keep - daily
    assert weekly == set(_names(["20260920T023000Z", "20260913T023000Z",
                                 "20260906T023000Z", "20260830T023000Z"]))
    assert _names([stamps[-1]])[0] not in keep


def test_retention_ignores_files_that_are_not_bundles():
    names = ["LAST_SUCCESS", "notes.txt", "hireflow-oops.tar.gpg"] + _names(["20260101T000000Z"])
    assert backup.bundles_to_keep(names) == {"hireflow-20260101T000000Z.tar.gpg"}


def test_apply_retention_deletes_bundle_and_its_checksum(tmp_path):
    import datetime as dt
    end = dt.datetime(2026, 9, 30)
    for i in range(60):
        n = f"hireflow-{(end - dt.timedelta(days=i)):%Y%m%dT%H%M%SZ}.tar.gpg"
        (tmp_path / n).write_text("x")
        (tmp_path / f"{n}.sha256").write_text("x")
    (tmp_path / "LAST_SUCCESS").write_text("x")
    removed = backup.apply_retention(tmp_path)
    left = sorted(p.name for p in tmp_path.iterdir())
    assert len(removed) == 60 - len([n for n in left if n.endswith(".gpg")])
    assert "LAST_SUCCESS" in left
    for n in removed:
        assert not (tmp_path / n).exists() and not (tmp_path / f"{n}.sha256").exists()


# ── what the drill calls a failure ───────────────────────────────────────────

def test_row_counts_must_match_exactly_by_default():
    assert drill.compare_counts({"a": 5, "b": 0}, {"a": 5, "b": 0}) == []
    problems = drill.compare_counts({"a": 5, "b": 2}, {"a": 4, "c": 1})
    assert any("a: 4 rows restored" in p for p in problems)
    assert any("b: missing" in p for p in problems)
    assert any("c: in the restore but not in the manifest" in p for p in problems)


def test_row_count_tolerance_is_opt_in():
    assert drill.compare_counts({"a": 1000}, {"a": 995}, tolerance_pct=1) == []
    assert drill.compare_counts({"a": 1000}, {"a": 980}, tolerance_pct=1)


def test_missing_file_is_broken_only_if_it_was_backed_up(tmp_path):
    (tmp_path / "resumes").mkdir()
    (tmp_path / "resumes" / "ok.pdf").write_bytes(b"x")
    broken, dangling = drill.check_file_keys(
        ["resumes/ok.pdf", "resumes/lost.pdf", "resumes/never.pdf"], tmp_path,
        listed={"resumes/ok.pdf", "resumes/lost.pdf"})
    assert broken == ["resumes/lost.pdf"]
    assert dangling == ["resumes/never.pdf"]


@pytest.mark.parametrize("value,expected", [
    ("/logos/a.png", "logos/a.png"),
    ("uploads\\photos\\x.jpg", "uploads/photos/x.jpg"),
    ("https://cdn.example.com/logo.png", None),
    ("", None),
])
def test_file_keys_are_normalised_like_storage_keys(value, expected):
    assert drill.normalise_key(value) == expected


def test_extract_rejects_path_traversal(tmp_path):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo("../../evil.txt")
        info.size = 1
        tar.addfile(info, io.BytesIO(b"x"))
    buf.seek(0)
    with tarfile.open(fileobj=buf, mode="r") as tar:
        with pytest.raises(drill.DrillError, match="escapes"):
            drill._safe_extract(tar, tmp_path / "out")
    assert not (tmp_path / "evil.txt").exists()


# ── integrity, config ────────────────────────────────────────────────────────

def test_sha256sums_catch_a_changed_file(tmp_path):
    (tmp_path / "db.sql.gz").write_bytes(b"dump")
    (tmp_path / "manifest.json").write_text("{}")
    backup.write_sha256sums(tmp_path, ["db.sql.gz", "manifest.json"])
    assert backup.verify_sha256sums(tmp_path) == []
    (tmp_path / "db.sql.gz").write_bytes(b"dumq")
    assert backup.verify_sha256sums(tmp_path) == ["db.sql.gz"]


def test_database_url_password_is_url_decoded():
    p = backup.db_params("mysql+pymysql://acct_user:p%40ss%23w0rd@db.host:3307/acct_hireflow")
    assert p == {"host": "db.host", "port": 3307, "user": "acct_user",
                 "password": "p@ss#w0rd", "database": "acct_hireflow"}


def test_credentials_go_in_a_private_file_not_the_command_line():
    with backup.mysql_defaults_file(dict(LIVE, password='we"ird#pw')) as path:
        text = Path(path).read_text()
        assert 'password="we\\"ird#pw"' in text
    assert not Path(path).exists()


@pytest.mark.parametrize("target,tool", [
    ("rclone:b2:hireflow-backups", "rclone"),
    ("gs://bucket/hireflow", "gsutil"),
    ("s3://bucket/hireflow", "aws"),
])
def test_offsite_targets(target, tool):
    assert backup.offsite_command(target, Path("x.tar.gpg"))[0] == tool


def test_unknown_offsite_target_fails_loudly():
    with pytest.raises(backup.BackupError):
        backup.offsite_command("ftp://nope", Path("x"))


def test_backup_refuses_to_run_unencrypted_without_the_flag(monkeypatch, tmp_path):
    monkeypatch.delenv("BACKUP_GPG_RECIPIENT", raising=False)
    monkeypatch.delenv("BACKUP_PASSPHRASE_FILE", raising=False)
    with pytest.raises(backup.BackupError, match="no encryption configured"):
        backup.encrypt(tmp_path / "in", tmp_path / "out")
