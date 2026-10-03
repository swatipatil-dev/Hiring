"""A real backup -> restore round trip, against MySQL with the real mysqldump/mysql.

test_backup_scripts.py covers the pieces; this proves they fit: a bundle written by
scripts/backup.py restores through scripts/restore_drill.py with every row and every
file intact. It runs on scratch databases of its own — never the test database the
rest of the suite shares, and never anything named in DATABASE_URL.

Needs the MySQL client binaries (mysqldump, mysql). GitHub's Ubuntu runners ship them;
elsewhere set MYSQLDUMP / MYSQL. Without them the test skips, which CI treats as a
failure (scripts/ci_check_skips.py) — deliberately, since an untested restore is the
thing this file exists to prevent.
"""
import importlib.util
import os
import shutil
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa

BACKEND = Path(__file__).resolve().parent.parent
_WINDOWS_MYSQL = Path("C:/Program Files/MySQL/MySQL Server 8.0/bin")


sys.path.insert(0, str(BACKEND / "scripts"))   # restore_drill imports backup as a sibling


def _load(name):
    spec = importlib.util.spec_from_file_location(name, BACKEND / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _binary(env, name):
    found = os.getenv(env) or shutil.which(name)
    if not found and (_WINDOWS_MYSQL / f"{name}.exe").exists():
        found = str(_WINDOWS_MYSQL / f"{name}.exe")
    return found


def _server_url(database=""):
    host = os.getenv("DB_HOST", "127.0.0.1")
    password = os.getenv("DB_PASSWORD", "1234")
    return f"mysql+pymysql://root:{password}@{host}:3306/{database}"


@pytest.fixture()
def scratch_source(monkeypatch, tmp_path):
    """A small source database and data dir, wired up as if they were live."""
    mysqldump, mysql = _binary("MYSQLDUMP", "mysqldump"), _binary("MYSQL", "mysql")
    if not (mysqldump and mysql):
        pytest.skip("mysqldump/mysql client binaries not found (set MYSQLDUMP / MYSQL)")

    tag = uuid.uuid4().hex[:8]
    source, target = f"hireflow_rt_{tag}", f"hireflow_rt_{tag}_drill"
    server = sa.create_engine(_server_url())
    with server.begin() as c:
        c.execute(sa.text(f"CREATE DATABASE `{source}` CHARACTER SET utf8mb4"))

    eng = sa.create_engine(_server_url(source))
    with eng.begin() as c:
        c.execute(sa.text("CREATE TABLE companies (id VARCHAR(36) PRIMARY KEY, name VARCHAR(100))"))
        c.execute(sa.text(
            "CREATE TABLE candidates (id VARCHAR(36) PRIMARY KEY, company_id VARCHAR(36), "
            "first_name VARCHAR(100), resume_path VARCHAR(500), photo BLOB)"))
        c.execute(sa.text("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"))
        c.execute(sa.text("INSERT INTO companies VALUES ('c1', 'Acme'), ('c2', 'Globex')"))
        # Non-ASCII text and raw bytes: the two things a careless dump mangles.
        c.execute(sa.text("INSERT INTO candidates VALUES "
                          "('a', 'c1', 'Aadhya', 'resumes/a.pdf', :b1), "
                          "('b', 'c2', 'Zoë ✓', 'uploads/photos/b.jpg', :b2)"),
                  {"b1": bytes(range(256)), "b2": b"\x00\xff\x10"})

    drill = _load("restore_drill")
    with eng.begin() as c:
        c.execute(sa.text("INSERT INTO alembic_version VALUES (:v)"), {"v": drill.code_heads()[0]})
    eng.dispose()

    data = tmp_path / "data"
    (data / "resumes").mkdir(parents=True)
    (data / "uploads" / "photos").mkdir(parents=True)
    (data / "resumes" / "a.pdf").write_bytes(b"%PDF-1.4 resume a")
    (data / "uploads" / "photos" / "b.jpg").write_bytes(bytes(range(200)))

    monkeypatch.setenv("DATABASE_URL", _server_url(source))
    monkeypatch.setenv("HIREFLOW_DATA_DIR", str(data))
    monkeypatch.setenv("BACKUP_FILE_DIRS", "resumes uploads")
    monkeypatch.setenv("MYSQLDUMP", mysqldump)
    monkeypatch.setenv("MYSQL", mysql)
    monkeypatch.delenv("BACKUP_OFFSITE", raising=False)
    monkeypatch.delenv("RESTORE_PROTECTED_DBS", raising=False)
    try:
        yield {"source": source, "target": target, "data": data, "drill": drill, "tmp": tmp_path}
    finally:
        with server.begin() as c:
            c.execute(sa.text(f"DROP DATABASE IF EXISTS `{source}`"))
            c.execute(sa.text(f"DROP DATABASE IF EXISTS `{target}`"))
        server.dispose()


def test_backup_restores_every_row_and_file(scratch_source):
    backup = _load("backup")
    drill, s = scratch_source["drill"], scratch_source

    out = s["tmp"] / "out"
    assert backup.main(["--out", str(out), "--no-encrypt"]) == 0
    bundles = sorted(out.glob("hireflow-*.tar"))
    assert len(bundles) == 1

    restored_files = s["tmp"] / "restored"
    report = drill.run_drill(bundles[0], s["target"], restored_files, per_column=10,
                             tolerance_pct=0.0, keep=True)
    checks = report["checks"]
    assert checks["outer_checksum"] and checks["inner_checksums"]
    assert checks["row_counts"]["ok"], checks["row_counts"]
    assert checks["row_counts"]["rows"] == 5
    assert checks["alembic"]["ok"], checks["alembic"]
    assert checks["files"]["ok"] and checks["files"]["restored"] == 2, checks["files"]

    # Byte-for-byte, not just counts.
    eng = sa.create_engine(_server_url(s["target"]))
    with eng.connect() as c:
        rows = dict(c.execute(sa.text("SELECT id, photo FROM candidates")).all())
        names = {r[0] for r in c.execute(sa.text("SELECT first_name FROM candidates"))}
    eng.dispose()
    assert rows == {"a": bytes(range(256)), "b": b"\x00\xff\x10"}
    assert names == {"Aadhya", "Zoë ✓"}
    for key in ("resumes/a.pdf", "uploads/photos/b.jpg"):
        assert (restored_files / key).read_bytes() == (s["data"] / key).read_bytes()

