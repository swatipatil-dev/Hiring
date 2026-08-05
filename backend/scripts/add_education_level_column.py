"""Add education_level column to candidate_education.

Idempotent: skips the column if it already exists. Safe to re-run.
Run:  python scripts/add_education_level_column.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager

TABLE = "candidate_education"

# column_name -> DDL type
NEW_COLUMNS = {
    "education_level": "VARCHAR(50) NULL",
}


def main():
    engine = db_manager.engine
    with engine.begin() as conn:
        existing = {
            row[0] for row in conn.execute(text(
                "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
            ), {"t": TABLE})
        }
        if not existing:
            print(f"Table '{TABLE}' not found — nothing to do.")
            return
        for col, ddl in NEW_COLUMNS.items():
            if col in existing:
                print(f"  = {col} already exists, skipping")
                continue
            conn.execute(text(f"ALTER TABLE {TABLE} ADD COLUMN {col} {ddl}"))
            print(f"  + added {col}")
    print("Done.")


if __name__ == "__main__":
    main()
