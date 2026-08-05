"""Add permanent-address + current_address_line columns to candidate_profile_ext.

Idempotent: skips columns that already exist. Safe to re-run.
Run:  python scripts/add_permanent_address_columns.py
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager

TABLE = "candidate_profile_ext"

# column_name -> DDL type
NEW_COLUMNS = {
    "current_address_line":      "VARCHAR(255) NULL",
    "permanent_address_line":    "VARCHAR(255) NULL",
    "permanent_city":            "VARCHAR(100) NULL",
    "permanent_state":           "VARCHAR(100) NULL",
    "permanent_country":         "VARCHAR(100) NULL",
    "permanent_pincode":         "VARCHAR(20) NULL",
    "permanent_same_as_current": "TINYINT(1) NOT NULL DEFAULT 0",
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
