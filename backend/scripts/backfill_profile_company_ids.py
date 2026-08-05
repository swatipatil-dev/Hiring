"""Idempotent backfill: populate company_id on hr_profiles and
interviewer_profiles after revision ``f6a7b8c9d0e1`` added the column.

The Alembic upgrade runs the same UPDATEs once; this script lets ops re-run
safely after data drift (e.g. profiles created by an older code path that
didn't set company_id explicitly).

Usage::

    python scripts/backfill_profile_company_ids.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config.database_config_mysql import db_manager


BACKFILLS = [
    ("hr_profiles", """
        UPDATE hr_profiles h
        JOIN users u ON h.user_id = u.id
        SET h.company_id = u.company_id
        WHERE h.company_id IS NULL AND u.company_id IS NOT NULL
    """),
    ("interviewer_profiles", """
        UPDATE interviewer_profiles ip
        JOIN users u ON ip.user_id = u.id
        SET ip.company_id = u.company_id
        WHERE ip.company_id IS NULL AND u.company_id IS NOT NULL
    """),
]


def main() -> None:
    with db_manager.engine.begin() as conn:
        for table, sql in BACKFILLS:
            before = conn.execute(text(
                f"SELECT COUNT(*) FROM {table} WHERE company_id IS NULL"
            )).scalar()
            result = conn.execute(text(sql))
            after = conn.execute(text(
                f"SELECT COUNT(*) FROM {table} WHERE company_id IS NULL"
            )).scalar()
            print(f"{table:25s}  null_before={before:>6}  updated={result.rowcount:>6}  null_after={after:>6}")


if __name__ == "__main__":
    main()
