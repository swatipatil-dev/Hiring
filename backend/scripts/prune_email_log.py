"""Drop email_log rows older than the retention window.

The delivery log grows by one row per outbound message and nothing else removes
them, so a busy tenant accumulates rows indefinitely. This trims them.

Run from the backend directory:

    python scripts/prune_email_log.py            # delete rows older than the window
    python scripts/prune_email_log.py --dry-run  # report what would go
    python scripts/prune_email_log.py --days 30  # override the window

Env: EMAIL_LOG_RETENTION_DAYS (default 90).

Deliberately not wired to a systemd timer by default — the default install
should not need a new unit. If you want it automated, copy the pattern in
deploy/cpanel/hireflow-regrade.timer.

Deletes in batches so a large first run does not hold one long transaction or a
lock that blocks live writes.
"""
import argparse
import os
import sys

# Importable as a script run from backend/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from src.config.database_config_mysql import db_manager  # noqa: E402

DEFAULT_DAYS = int(os.getenv("EMAIL_LOG_RETENTION_DAYS", "90"))
BATCH = 1000


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS,
                    help=f"retention window in days (default {DEFAULT_DAYS})")
    ap.add_argument("--dry-run", action="store_true",
                    help="report the row count without deleting")
    args = ap.parse_args()

    if args.days < 1:
        print("--days must be at least 1", file=sys.stderr)
        return 2

    cutoff_sql = "created_at < (NOW() - INTERVAL :days DAY)"

    with db_manager.engine.begin() as conn:
        if not conn.dialect.has_table(conn, "email_log"):
            print("email_log does not exist yet — nothing to prune.")
            return 0
        total = conn.execute(
            text(f"SELECT COUNT(*) FROM email_log WHERE {cutoff_sql}"),
            {"days": args.days},
        ).scalar() or 0

    print(f"{total} email_log row(s) older than {args.days} day(s).")
    if args.dry_run or not total:
        return 0

    removed = 0
    while True:
        # One transaction per batch: a single DELETE of a very large backlog
        # holds locks long enough to stall the worker's own writes.
        with db_manager.engine.begin() as conn:
            n = conn.execute(
                text(f"DELETE FROM email_log WHERE {cutoff_sql} LIMIT :batch"),
                {"days": args.days, "batch": BATCH},
            ).rowcount
        removed += n
        if n:
            print(f"  deleted {removed}/{total}")
        if n < BATCH:
            break

    print(f"Done — removed {removed} row(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
