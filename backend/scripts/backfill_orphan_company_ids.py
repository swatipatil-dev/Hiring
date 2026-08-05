"""Idempotent backfill: populate company_id on tables that gained the column
in revision ``e5f6a7b8c9d0_add_company_id_to_orphan_tenant_tables``.

The Alembic upgrade runs the same statements once. This standalone script lets
ops re-run safely after data drift (e.g. rows inserted by an older code path
that didn't set company_id explicitly).

Usage::

    python scripts/backfill_orphan_company_ids.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config.database_config_mysql import db_manager


BACKFILLS = [
    ("ats_results", """
        UPDATE ats_results a
        JOIN candidates c ON a.candidate_id = c.id
        SET a.company_id = c.company_id
        WHERE a.company_id IS NULL AND c.company_id IS NOT NULL
    """),
    ("candidate_stage_history", """
        UPDATE candidate_stage_history h
        JOIN candidates c ON h.candidate_id = c.id
        SET h.company_id = c.company_id
        WHERE h.company_id IS NULL AND c.company_id IS NOT NULL
    """),
    ("interview_feedback", """
        UPDATE interview_feedback f
        JOIN interviews i ON f.interview_id = i.id
        SET f.company_id = i.company_id
        WHERE f.company_id IS NULL AND i.company_id IS NOT NULL
    """),
    ("support_messages", """
        UPDATE support_messages m
        JOIN support_requests s ON m.request_id = s.id
        SET m.company_id = s.company_id
        WHERE m.company_id IS NULL AND s.company_id IS NOT NULL
    """),
    ("proctoring_sessions", """
        UPDATE proctoring_sessions p
        JOIN interviews i ON p.test_id = i.id
        SET p.company_id = i.company_id
        WHERE p.company_id IS NULL AND i.company_id IS NOT NULL
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
