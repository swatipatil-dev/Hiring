"""Nightly backup: the database and every uploaded file, as one encrypted bundle.

Run by deploy/cpanel/hireflow-backup.timer; see docs/BACKUP_AND_RESTORE.md for the
setup, the restore procedure and the monthly drill (scripts/restore_drill.py).

One bundle per run, ``hireflow-<UTC stamp>.tar.gpg``, holding:

  db.sql.gz       mysqldump --single-transaction of the DATABASE_URL database
  files.tar.gz    the upload directories under HIREFLOW_DATA_DIR
  files.list      every file key in files.tar.gz (the drill checks DB references against it)
  manifest.json   exact per-table row counts, alembic version, DB name, file count
  SHA256SUMS      checksums of the four files above

plus ``<bundle>.sha256`` next to it, so a corrupted copy is caught before decrypting.

Nothing about the schema name is hardcoded: it comes from DATABASE_URL, because cPanel
prefixes every database with the account name (docs/DEPLOYMENT_NOTES.md).

Encryption is required. BACKUP_GPG_RECIPIENT encrypts to a public key — preferred, since
then the server cannot decrypt its own backups and a stolen backup disk is useless.
BACKUP_PASSPHRASE_FILE encrypts symmetrically (AES256) instead. ``--no-encrypt`` exists for
a local restore drill only and says so loudly.

Retention: the newest 7 bundles, plus the newest bundle of each of the 4 ISO weeks before
them (about five weeks of history).
BACKUP_OFFSITE copies each bundle off the box (rclone:<remote>:<path>, gs://..., s3://...);
if it is configured and the copy fails, the run fails — a backup that only exists on the
server it protects is not a backup.

Exit status: 0 on success, 1 on any failure (systemd then shows the unit as failed).
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

# Upload directories under HIREFLOW_DATA_DIR (src/services/storage.py keys, and
# proctoring's evidence_dir in proctoring/config.py). Override with BACKUP_FILE_DIRS.
DEFAULT_FILE_DIRS = ("resumes", "uploads", "cover_letters", "logos", "evidence_frames")

BUNDLE_RE = re.compile(r"^hireflow-(\d{8}T\d{6}Z)\.tar(\.gpg)?$")
KEEP_DAILY = 7
KEEP_WEEKLY = 4


class BackupError(RuntimeError):
    pass


def log(msg: str) -> None:
    print(f"[backup] {msg}", flush=True)


# ── configuration ────────────────────────────────────────────────────────────

def load_environment() -> None:
    """Read backend/.env the same tolerant way the app does (src/env_boot.py)."""
    try:
        from src.env_boot import load_env
        load_env(dotenv_path=str(BACKEND / ".env"))
    except Exception:
        pass


def db_params(url: Optional[str] = None) -> Dict[str, object]:
    """Connection parameters from DATABASE_URL (URL-decoded, so passwords with @ work)."""
    from sqlalchemy.engine import make_url
    url = url or os.getenv("DATABASE_URL")
    if not url:
        raise BackupError("DATABASE_URL is not set")
    u = make_url(url)
    if not u.database:
        raise BackupError("DATABASE_URL names no database")
    return {"host": u.host or "localhost", "port": int(u.port or 3306),
            "user": u.username or "", "password": u.password or "",
            "database": u.database}


def data_dir() -> Path:
    return Path(os.getenv("HIREFLOW_DATA_DIR") or BACKEND).resolve()


def file_dirs() -> List[str]:
    raw = os.getenv("BACKUP_FILE_DIRS")
    return raw.split() if raw else list(DEFAULT_FILE_DIRS)


@contextlib.contextmanager
def mysql_defaults_file(params: Dict[str, object]):
    """A 0600 option file carrying the credentials, so the password never appears in
    the process list (``ps`` shows every argument of every process on a shared box)."""
    fd, path = tempfile.mkstemp(prefix="hireflow-my-", suffix=".cnf")
    try:
        with os.fdopen(fd, "w") as f:
            f.write("[client]\n")
            f.write(f"host={params['host']}\nport={params['port']}\n")
            f.write(f"user={params['user']}\n")
            # TCP like the app (pymysql never uses the socket), so the grant that lets the
            # API in lets the dump in; "localhost" would otherwise mean the unix socket.
            f.write("protocol=tcp\n")
            # Quoted: option files treat # as a comment and strip unquoted spaces.
            pw = str(params["password"]).replace("\\", "\\\\").replace('"', '\\"')
            f.write(f'password="{pw}"\n')
        os.chmod(path, 0o600)
        yield path
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)


def _run(cmd: Sequence[str], **kw) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(list(cmd), check=True, **kw)
    except FileNotFoundError:
        raise BackupError(f"{cmd[0]} not found — install it or set its path in the environment")
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="replace").strip() if isinstance(e.stderr, bytes) else (e.stderr or "")
        raise BackupError(f"{Path(cmd[0]).name} failed (exit {e.returncode}): {err[-2000:]}")


# ── database ─────────────────────────────────────────────────────────────────

def _engine(params: Dict[str, object]):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL
    return create_engine(URL.create(
        "mysql+pymysql", username=params["user"], password=params["password"],
        host=params["host"], port=params["port"], database=params["database"]))


def table_row_counts(params: Dict[str, object]) -> Dict[str, int]:
    """Exact COUNT(*) per base table, read in one consistent snapshot.

    information_schema.TABLE_ROWS is an InnoDB estimate that can be off by 40%, so it is
    no use for proving a restore is complete.
    """
    from sqlalchemy import text
    eng = _engine(params)
    try:
        with eng.connect() as conn:
            conn.exec_driver_sql("SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ")
            conn.exec_driver_sql("START TRANSACTION WITH CONSISTENT SNAPSHOT")
            tables = [r[0] for r in conn.execute(text(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' "
                "ORDER BY table_name"))]
            counts = {t: int(conn.exec_driver_sql(f"SELECT COUNT(*) FROM `{t}`").scalar())
                      for t in tables}
            conn.exec_driver_sql("COMMIT")
            return counts
    finally:
        eng.dispose()


def alembic_version(params: Dict[str, object]) -> List[str]:
    from sqlalchemy import text
    eng = _engine(params)
    try:
        with eng.connect() as conn:
            try:
                return sorted(r[0] for r in conn.execute(text("SELECT version_num FROM alembic_version")))
            except Exception:
                return []
    finally:
        eng.dispose()


def _dump_supports(mysqldump: str, option: str) -> bool:
    try:
        out = subprocess.run([mysqldump, "--help"], capture_output=True, text=True).stdout
    except OSError:
        return False
    return option in out


def dump_database(params: Dict[str, object], out_path: Path) -> None:
    """mysqldump the database to a gzip file, streaming (the dump never sits in RAM)."""
    mysqldump = os.getenv("MYSQLDUMP", "mysqldump")
    args = ["--single-transaction", "--quick", "--routines", "--triggers",
            "--hex-blob", "--default-character-set=utf8mb4",
            # cPanel database users have no PROCESS privilege; without this MySQL 8's
            # mysqldump aborts trying to read tablespace metadata.
            "--no-tablespaces"]
    # MySQL's mysqldump only (MariaDB's rejects it): keep GTID state out of the dump so it
    # restores into any server, including the drill database on the same host.
    if _dump_supports(mysqldump, "--set-gtid-purged"):
        args.append("--set-gtid-purged=OFF")
    with mysql_defaults_file(params) as cnf:
        cmd = [mysqldump, f"--defaults-extra-file={cnf}", *args, str(params["database"])]
        tail = b""
        # stderr goes to a file, not a pipe: a chatty stderr would fill the pipe buffer
        # and deadlock mysqldump while we only read stdout.
        with gzip.open(out_path, "wb") as gz, tempfile.TemporaryFile() as errf:
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf)
            except FileNotFoundError:
                raise BackupError(f"{mysqldump} not found — install it or set MYSQLDUMP")
            try:
                for chunk in iter(lambda: proc.stdout.read(1 << 20), b""):
                    gz.write(chunk)
                    tail = (tail + chunk)[-4096:]
            finally:
                proc.stdout.close()
            if proc.wait() != 0:
                errf.seek(0)
                err = errf.read().decode(errors="replace")
                raise BackupError(f"mysqldump failed (exit {proc.returncode}): {err[-2000:]}")
    # A dump cut off mid-stream can still exit 0 (e.g. a killed connection on some
    # versions); the trailer is the only proof it finished.
    if b"-- Dump completed" not in tail:
        raise BackupError("mysqldump output has no completion trailer — dump is truncated")


# ── files ────────────────────────────────────────────────────────────────────

def archive_files(root: Path, dirs: Iterable[str], out_path: Path, list_path: Path) -> Dict[str, int]:
    """tar.gz the upload directories with paths relative to root (= storage keys)."""
    count = size = 0
    keys: List[str] = []
    with tarfile.open(out_path, "w:gz") as tar:
        for d in dirs:
            base = root / d
            if not base.is_dir():
                log(f"note: {base} does not exist — nothing to back up there")
                continue
            for path in sorted(base.rglob("*")):
                if path.is_symlink() or not path.is_file():
                    continue
                key = path.relative_to(root).as_posix()
                tar.add(str(path), arcname=key, recursive=False)
                keys.append(key)
                count += 1
                size += path.stat().st_size
    list_path.write_text("\n".join(keys) + ("\n" if keys else ""), encoding="utf-8")
    return {"file_count": count, "file_bytes": size}


# ── integrity and encryption ─────────────────────────────────────────────────

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sha256sums(folder: Path, names: Iterable[str]) -> None:
    lines = [f"{sha256_of(folder / n)}  {n}" for n in names]
    (folder / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")


def verify_sha256sums(folder: Path) -> List[str]:
    """Return the names whose checksum does not match (empty list = all good)."""
    bad = []
    for line in (folder / "SHA256SUMS").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split(None, 1)
        name = name.strip().lstrip("*")
        target = folder / name
        if not target.is_file() or sha256_of(target) != digest:
            bad.append(name)
    return bad


def encrypt(src: Path, dst: Path) -> str:
    gpg = os.getenv("BACKUP_GPG", "gpg")
    recipient = os.getenv("BACKUP_GPG_RECIPIENT", "").strip()
    passfile = os.getenv("BACKUP_PASSPHRASE_FILE", "").strip()
    base = [gpg, "--batch", "--yes", "--output", str(dst)]
    if recipient:
        # --trust-model always: the key was imported deliberately for this purpose; there is
        # no web of trust on a service account and no human to answer the prompt.
        _run(base + ["--trust-model", "always", "--encrypt", "--recipient", recipient, str(src)],
             capture_output=True)
        return f"gpg public key {recipient}"
    if passfile:
        if not Path(passfile).is_file():
            raise BackupError(f"BACKUP_PASSPHRASE_FILE {passfile} does not exist")
        _run(base + ["--pinentry-mode", "loopback", "--passphrase-file", passfile,
                     "--symmetric", "--cipher-algo", "AES256", str(src)], capture_output=True)
        return "gpg symmetric AES256"
    raise BackupError("no encryption configured: set BACKUP_GPG_RECIPIENT or "
                      "BACKUP_PASSPHRASE_FILE (see docs/BACKUP_AND_RESTORE.md)")


# ── retention and off-site ───────────────────────────────────────────────────

def bundles_to_keep(names: Iterable[str], daily: int = KEEP_DAILY,
                    weekly: int = KEEP_WEEKLY) -> set:
    """Which bundle names survive retention: the newest `daily`, plus the newest bundle of
    each of the `weekly` ISO weeks before those — so 7 + 4 reaches back about five weeks.
    Names that aren't bundles are never touched."""
    dated = []
    for n in names:
        m = BUNDLE_RE.match(n)
        if m:
            dated.append((dt.datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ"), n))
    dated.sort(reverse=True)
    keep = {n for _, n in dated[:daily]}
    covered = {stamp.isocalendar()[:2] for stamp, _ in dated[:daily]}
    added = 0
    for stamp, n in dated[daily:]:
        if added >= weekly:
            break
        week = stamp.isocalendar()[:2]
        if week in covered:
            continue
        covered.add(week)
        keep.add(n)
        added += 1
    return keep


def apply_retention(folder: Path) -> List[str]:
    names = [p.name for p in folder.iterdir() if BUNDLE_RE.match(p.name)]
    keep = bundles_to_keep(names)
    removed = []
    for n in names:
        if n not in keep:
            for p in (folder / n, folder / f"{n}.sha256"):
                with contextlib.suppress(FileNotFoundError):
                    p.unlink()
            removed.append(n)
    return sorted(removed)


def offsite_command(target: str, local: Path) -> List[str]:
    """The command that copies one local file to BACKUP_OFFSITE."""
    if target.startswith("rclone:"):
        return [os.getenv("RCLONE", "rclone"), "copy", str(local), target[len("rclone:"):]]
    if target.startswith("gs://"):
        return [os.getenv("GSUTIL", "gsutil"), "cp", str(local), target.rstrip("/") + "/"]
    if target.startswith("s3://"):
        return [os.getenv("AWS_CLI", "aws"), "s3", "cp", str(local), target.rstrip("/") + "/"]
    raise BackupError(f"BACKUP_OFFSITE {target!r} is not rclone:<remote>:<path>, gs:// or s3://")


def copy_offsite(files: Sequence[Path]) -> Optional[str]:
    target = os.getenv("BACKUP_OFFSITE", "").strip()
    if not target:
        log("WARNING: BACKUP_OFFSITE is not set — this backup exists only on this server")
        return None
    for f in files:
        _run(offsite_command(target, f), capture_output=True)
    return target


# ── main ─────────────────────────────────────────────────────────────────────

def run_backup(out_dir: Path, encrypt_bundle: bool = True) -> Path:
    params = db_params()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir.mkdir(parents=True, exist_ok=True)
    bundle_name = f"hireflow-{stamp}.tar" + (".gpg" if encrypt_bundle else "")
    root = data_dir()

    with tempfile.TemporaryDirectory(prefix="hireflow-backup-", dir=str(out_dir)) as tmp:
        stage = Path(tmp) / f"hireflow-{stamp}"
        stage.mkdir()

        log(f"counting rows in {params['database']}")
        counts = table_row_counts(params)
        log(f"dumping {params['database']} ({len(counts)} tables)")
        dump_database(params, stage / "db.sql.gz")
        log(f"archiving files under {root}")
        stats = archive_files(root, file_dirs(), stage / "files.tar.gz", stage / "files.list")

        manifest = {
            "format": 1,
            "created_at": stamp,
            "database": params["database"],
            "alembic_version": alembic_version(params),
            "tables": counts,
            "data_dir": str(root),
            "file_dirs": file_dirs(),
            "storage_backend": os.getenv("STORAGE_BACKEND", "local"),
            **stats,
        }
        (stage / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        write_sha256sums(stage, ["db.sql.gz", "files.tar.gz", "files.list", "manifest.json"])

        plain = Path(tmp) / f"hireflow-{stamp}.tar"
        with tarfile.open(plain, "w") as tar:
            tar.add(str(stage), arcname=stage.name)

        final = out_dir / bundle_name
        if encrypt_bundle:
            how = encrypt(plain, final)
        else:
            shutil.move(str(plain), final)
            how = "NONE"
        os.chmod(final, 0o600)

    sums = out_dir / f"{bundle_name}.sha256"
    sums.write_text(f"{sha256_of(final)}  {bundle_name}\n", encoding="utf-8")
    log(f"wrote {final} ({final.stat().st_size} bytes, encryption: {how}, "
        f"{sum(counts.values())} rows, {stats['file_count']} files)")

    where = copy_offsite([final, sums])
    if where:
        log(f"copied off-site to {where}")

    removed = apply_retention(out_dir)
    if removed:
        log(f"retention removed {len(removed)} old bundle(s): {', '.join(removed)}")
    (out_dir / "LAST_SUCCESS").write_text(stamp + "\n", encoding="utf-8")
    return final


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="backup directory (default: $BACKUP_DIR)")
    ap.add_argument("--no-encrypt", action="store_true",
                    help="DEV/DRILL ONLY: write an unencrypted bundle")
    args = ap.parse_args(argv)
    load_environment()
    out = args.out or os.getenv("BACKUP_DIR")
    if not out:
        log("FAILED: no backup directory — pass --out or set BACKUP_DIR")
        return 1
    if args.no_encrypt:
        log("WARNING: --no-encrypt — this bundle holds every candidate's personal data in "
            "clear. Use only for a local drill and delete it afterwards.")
    try:
        run_backup(Path(out), encrypt_bundle=not args.no_encrypt)
    except BackupError as e:
        log(f"FAILED: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
