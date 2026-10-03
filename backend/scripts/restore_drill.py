"""Restore a backup into a scratch database and prove it is complete.

A backup nobody has restored is a hope, not a backup. This takes one bundle written by
scripts/backup.py, restores it somewhere harmless, and checks it:

  1. the bundle's outer checksum, then every inner checksum (SHA256SUMS)
  2. every table's row count matches the manifest recorded at backup time
  3. alembic_version in the restore equals the code's migration head
  4. scripts/verify_tenant_isolation.py passes against the restored database
  5. a sample of file keys the database references exist among the restored files
     (a key missing from the restore but listed in files.list = the backup is broken;
     missing from both = the reference was already dangling in production)

It NEVER touches the live database: it refuses a target equal to DATABASE_URL's database,
or any name in RESTORE_PROTECTED_DBS, or one without "drill"/"restore" in its name.

    python scripts/restore_drill.py /opt/hireflow/backups/hireflow-20261001T023000Z.tar.gpg
    python scripts/restore_drill.py BUNDLE --target-db acct_hireflow_drill --report drill.json

Decrypting needs the private key (public-key backups) or BACKUP_PASSPHRASE_FILE. Run the
monthly checklist in docs/BACKUP_AND_RESTORE.md. Exit status 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
import backup  # noqa: E402  (scripts/ has no __init__.py)

BACKEND = backup.BACKEND

# (table, column) pairs holding storage keys. Columns absent from an older schema are
# skipped. Logos may be stored as "/logos/..." or a full URL; URLs are not files here.
FILE_COLUMNS = [
    ("candidates", "resume_path"),
    ("candidates", "photo_path"),
    ("candidate_profile_ext", "resume_path"),
    ("candidate_profile_ext", "profile_photo_path"),
    ("candidate_profile_ext", "cover_letter_path"),
    ("resume_pool", "resume_path"),
    ("resume_pool_attachments", "file_path"),
    ("communication_answers", "audio_path"),
    ("proctoring_violations", "evidence_path"),
    ("companies", "logo_url"),
    ("departments", "logo_url"),
]


class DrillError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[drill] {msg}", flush=True)


# ── safety ───────────────────────────────────────────────────────────────────

def check_target_is_safe(target: str, live: Dict[str, object],
                         protected: Sequence[str] = ()) -> None:
    """Refuse any target that could be a real database."""
    t = (target or "").strip()
    if not t:
        raise DrillError("no target database")
    if t.lower() == str(live["database"]).lower():
        raise DrillError(f"refusing to restore into {t!r}: that is the live database "
                         f"(DATABASE_URL)")
    if t.lower() in {p.strip().lower() for p in protected if p.strip()}:
        raise DrillError(f"refusing to restore into {t!r}: listed in RESTORE_PROTECTED_DBS")
    if "drill" not in t.lower() and "restore" not in t.lower():
        raise DrillError(f"refusing to restore into {t!r}: a scratch database name must "
                         f"contain 'drill' or 'restore', so a typo can't name a real one")


def check_files_dir_is_safe(files_dir: Path, live_data_dir: Path) -> None:
    """Refuse a --files-dir that could overwrite real uploads.

    Extraction writes over whatever is there, so the drill must not be pointed at the
    live data directory (or anything inside it, or any folder that contains it), and
    must not mix a restore into a folder that already holds files.
    """
    d, live_dir = files_dir.resolve(), live_data_dir.resolve()
    if d == live_dir or live_dir in d.parents or d in live_dir.parents:
        raise DrillError(f"refusing to restore files into {d}: it overlaps the live data "
                         f"directory {live_dir} (HIREFLOW_DATA_DIR)")
    if d.exists() and any(d.iterdir()):
        raise DrillError(f"refusing to restore files into {d}: the folder is not empty")


def default_target(live: Dict[str, object]) -> str:
    # Keeps the cPanel account prefix (acct_hireflow -> acct_hireflow_drill), which cPanel
    # requires for any database the account user may touch.
    return f"{live['database']}_drill"


# ── unpack ───────────────────────────────────────────────────────────────────

def verify_outer_checksum(bundle: Path) -> bool:
    sums = bundle.with_name(bundle.name + ".sha256")
    if not sums.is_file():
        log(f"note: no {sums.name} next to the bundle — skipping the outer checksum")
        return True
    expected = sums.read_text(encoding="utf-8").split()[0]
    return backup.sha256_of(bundle) == expected


def decrypt(bundle: Path, out: Path) -> None:
    gpg = os.getenv("BACKUP_GPG", "gpg")
    cmd = [gpg, "--batch", "--yes", "--output", str(out)]
    passfile = os.getenv("BACKUP_PASSPHRASE_FILE", "").strip()
    if passfile:
        cmd += ["--pinentry-mode", "loopback", "--passphrase-file", passfile]
    backup._run(cmd + ["--decrypt", str(bundle)], capture_output=True)


def unpack(bundle: Path, work: Path) -> Path:
    """Decrypt (if needed) and extract; return the folder holding manifest.json."""
    plain = bundle
    if bundle.name.endswith(".gpg"):
        plain = work / bundle.name[:-4]
        decrypt(bundle, plain)
    with tarfile.open(plain, "r") as tar:
        _safe_extract(tar, work / "bundle")
    found = list((work / "bundle").glob("*/manifest.json"))
    if len(found) != 1:
        raise DrillError("bundle does not contain exactly one manifest.json")
    return found[0].parent


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract refusing absolute paths, '..' and links — a bundle is input like any other."""
    dest = dest.resolve()
    for m in tar.getmembers():
        target = (dest / m.name).resolve()
        if not (target == dest or str(target).startswith(str(dest) + os.sep)):
            raise DrillError(f"bundle member escapes the extract dir: {m.name}")
        if m.issym() or m.islnk() or m.isdev():
            raise DrillError(f"bundle member is a link/device: {m.name}")
    tar.extractall(str(dest))


# ── restore ──────────────────────────────────────────────────────────────────

def _server_engine(params: Dict[str, object], database: Optional[str]):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    return create_engine(URL.create(
        "mysql+pymysql", username=params["user"], password=params["password"],
        host=params["host"], port=params["port"], database=database))


def prepare_target(params: Dict[str, object], target: str) -> None:
    """Make `target` exist and be empty. Only ever called after check_target_is_safe.

    cPanel users usually cannot CREATE DATABASE (it is created in the cPanel UI), so an
    existing database is emptied table by table rather than dropped.
    """
    eng = _server_engine(params, None)
    try:
        with eng.connect() as conn:
            conn.exec_driver_sql(f"CREATE DATABASE IF NOT EXISTS `{target}` "
                                 f"CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    except Exception as e:
        log(f"note: could not CREATE DATABASE {target} ({type(e).__name__}); "
            f"assuming it was created in cPanel")
    finally:
        eng.dispose()
    eng = _server_engine(params, target)
    try:
        with eng.connect() as conn:
            conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 0")
            rows = conn.exec_driver_sql(
                "SELECT table_name, table_type FROM information_schema.tables "
                "WHERE table_schema = DATABASE()").fetchall()
            for name, kind in rows:
                what = "VIEW" if kind == "VIEW" else "TABLE"
                conn.exec_driver_sql(f"DROP {what} IF EXISTS `{name}`")
            conn.exec_driver_sql("SET FOREIGN_KEY_CHECKS = 1")
    finally:
        eng.dispose()


def load_dump(params: Dict[str, object], target: str, dump_gz: Path) -> None:
    mysql = os.getenv("MYSQL", "mysql")
    target_params = dict(params, database=target)
    with backup.mysql_defaults_file(target_params) as cnf, tempfile.TemporaryFile() as errf:
        cmd = [mysql, f"--defaults-extra-file={cnf}", "--default-character-set=utf8mb4", target]
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                                    stderr=errf)
        except FileNotFoundError:
            raise DrillError(f"{mysql} not found — install it or set MYSQL")
        try:
            with gzip.open(dump_gz, "rb") as gz:
                shutil.copyfileobj(gz, proc.stdin, length=1 << 20)
        finally:
            proc.stdin.close()
        if proc.wait() != 0:
            errf.seek(0)
            raise DrillError(f"mysql restore failed (exit {proc.returncode}): "
                             f"{errf.read().decode(errors='replace')[-2000:]}")


def restore_files(files_tgz: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with tarfile.open(files_tgz, "r:gz") as tar:
        _safe_extract(tar, dest)


# ── checks ───────────────────────────────────────────────────────────────────

def compare_counts(expected: Dict[str, int], actual: Dict[str, int],
                   tolerance_pct: float = 0.0) -> List[str]:
    """Human-readable problems; empty list = every table matches."""
    problems = []
    for table, want in sorted(expected.items()):
        if table not in actual:
            problems.append(f"{table}: missing from the restore (expected {want} rows)")
            continue
        got = actual[table]
        allowed = int(want * tolerance_pct / 100)
        if abs(got - want) > allowed:
            problems.append(f"{table}: {got} rows restored, manifest says {want}")
    for table in sorted(set(actual) - set(expected)):
        problems.append(f"{table}: in the restore but not in the manifest")
    return problems


def code_heads() -> List[str]:
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return sorted(ScriptDirectory.from_config(cfg).get_heads())


def run_tenant_check(params: Dict[str, object], target: str) -> Tuple[int, str]:
    from sqlalchemy.engine import URL
    url = URL.create("mysql+pymysql", username=params["user"], password=params["password"],
                     host=params["host"], port=params["port"], database=target)
    env = dict(os.environ, DATABASE_URL=url.render_as_string(hide_password=False))
    proc = subprocess.run([sys.executable, str(BACKEND / "scripts" / "verify_tenant_isolation.py")],
                          env=env, cwd=str(BACKEND), capture_output=True, text=True)
    return proc.returncode, (proc.stdout + proc.stderr)[-3000:]


def normalise_key(value: str) -> Optional[str]:
    v = (value or "").strip()
    if not v or v.startswith(("http://", "https://", "data:")):
        return None
    return v.replace("\\", "/").lstrip("/")


def sample_file_keys(params: Dict[str, object], target: str, per_column: int) -> List[str]:
    eng = _server_engine(params, target)
    keys: List[str] = []
    try:
        with eng.connect() as conn:
            present = {(t, c) for t, c in conn.exec_driver_sql(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE table_schema = DATABASE()").fetchall()}
            for table, column in FILE_COLUMNS:
                if (table, column) not in present:
                    continue
                rows = conn.exec_driver_sql(
                    f"SELECT `{column}` FROM `{table}` WHERE `{column}` IS NOT NULL "
                    f"AND `{column}` <> '' ORDER BY RAND() LIMIT {int(per_column)}").fetchall()
                keys += [k for k in (normalise_key(r[0]) for r in rows) if k]
    finally:
        eng.dispose()
    return keys


def check_file_keys(keys: Sequence[str], restored: Path, listed: set) -> Tuple[List[str], List[str]]:
    """(broken, dangling): broken = backed up but not restored; dangling = never existed."""
    broken, dangling = [], []
    for k in keys:
        if (restored / k).is_file():
            continue
        (broken if k in listed else dangling).append(k)
    return broken, dangling


# ── main ─────────────────────────────────────────────────────────────────────

def run_drill(bundle: Path, target: str, files_dir: Optional[Path], per_column: int,
              tolerance_pct: float, keep: bool) -> Dict[str, object]:
    live = backup.db_params()
    check_target_is_safe(target, live, os.getenv("RESTORE_PROTECTED_DBS", "").split(","))
    if files_dir is not None:
        check_files_dir_is_safe(files_dir, backup.data_dir())
    report: Dict[str, object] = {"bundle": str(bundle), "target_db": target, "checks": {}}
    checks = report["checks"]

    with tempfile.TemporaryDirectory(prefix="hireflow-drill-") as tmp:
        work = Path(tmp)
        log(f"verifying {bundle.name}")
        checks["outer_checksum"] = verify_outer_checksum(bundle)
        if not checks["outer_checksum"]:
            raise DrillError("bundle checksum does not match its .sha256 — corrupted copy")
        folder = unpack(bundle, work)
        bad = backup.verify_sha256sums(folder)
        checks["inner_checksums"] = not bad
        if bad:
            raise DrillError(f"inner checksum mismatch: {', '.join(bad)}")
        manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
        report["backup_created_at"] = manifest.get("created_at")

        log(f"restoring {manifest['database']} -> {target}")
        prepare_target(live, target)
        load_dump(live, target, folder / "db.sql.gz")
        dest = files_dir or (work / "files")
        restore_files(folder / "files.tar.gz", dest)

        counts = backup.table_row_counts(dict(live, database=target))
        problems = compare_counts(manifest["tables"], counts, tolerance_pct)
        checks["row_counts"] = {"ok": not problems, "tables": len(counts),
                                "rows": sum(counts.values()), "problems": problems}

        restored_version = backup.alembic_version(dict(live, database=target))
        heads = code_heads()
        checks["alembic"] = {"ok": restored_version == heads,
                             "restored": restored_version, "code_heads": heads}

        rc, out = run_tenant_check(live, target)
        checks["tenant_isolation"] = {"ok": rc == 0, "exit_code": rc, "output": out}

        listed = set((folder / "files.list").read_text(encoding="utf-8").splitlines())
        keys = sample_file_keys(live, target, per_column)
        broken, dangling = check_file_keys(keys, dest, listed)
        restored_count = sum(1 for p in dest.rglob("*") if p.is_file())
        checks["files"] = {"ok": not broken and restored_count == manifest.get("file_count", 0),
                           "restored": restored_count,
                           "manifest": manifest.get("file_count", 0),
                           "keys_sampled": len(keys), "broken": broken,
                           "dangling_in_production": dangling}

        if not keep:
            prepare_target(live, target)   # leave the scratch database empty

    report["passed"] = all(
        (v if isinstance(v, bool) else v.get("ok")) for v in checks.values())
    return report


def print_report(report: Dict[str, object]) -> None:
    for name, v in report["checks"].items():
        ok = v if isinstance(v, bool) else v.get("ok")
        detail = "" if isinstance(v, bool) else {k: val for k, val in v.items()
                                                  if k not in ("ok", "output")}
        log(f"{'PASS' if ok else 'FAIL'}  {name}  {detail if detail else ''}")
        if not ok and isinstance(v, dict) and v.get("output"):
            print(v["output"])
    log("RESULT: " + ("PASS" if report["passed"] else "FAIL"))


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("bundle", help="hireflow-<stamp>.tar.gpg (or .tar from a --no-encrypt drill)")
    ap.add_argument("--target-db", help="scratch database (default: <live db>_drill)")
    ap.add_argument("--files-dir", help="restore files here (default: a temp dir)")
    ap.add_argument("--sample", type=int, default=50, help="file keys sampled per column")
    ap.add_argument("--count-tolerance", type=float, default=0.0,
                    help="percent row-count drift tolerated per table (default: exact)")
    ap.add_argument("--keep", action="store_true", help="leave the restored tables in place")
    ap.add_argument("--report", help="write the full report as JSON here")
    args = ap.parse_args(argv)
    backup.load_environment()
    try:
        target = args.target_db or default_target(backup.db_params())
        report = run_drill(Path(args.bundle), target,
                           Path(args.files_dir) if args.files_dir else None,
                           args.sample, args.count_tolerance, args.keep)
    except (DrillError, backup.BackupError) as e:
        log(f"FAIL: {e}")
        return 1
    print_report(report)
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
