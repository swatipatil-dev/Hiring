"""Idempotently apply the column changes from the three develop-branch tenancy
migrations to a DB that was previously stamped at abf4d61600ac without those
migrations having physically run (i.e. the DB was originally created via
Base.metadata.create_all() against a pre-tenancy model).

Safe to re-run. For each (table, column) it checks information_schema first and
only adds + indexes + FKs the column if missing. Backfill SQL is already
idempotent (only touches NULL rows).

After this runs the live DB matches the ORM and the alembic head
(abf4d61600ac) is correct.
"""
from __future__ import annotations
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager


# (table, [list of children for FK/index naming convenience])
# The three migrations all add: company_id VARCHAR(36), index ix_<table>_company_id,
# foreign key fk_<table>_company_id -> companies.id.
ORPHAN_TABLES = [
    "ats_results",
    "candidate_stage_history",
    "interview_feedback",
    "support_messages",
    "proctoring_sessions",
]

PROFILE_TABLES = ["hr_profiles", "interviewer_profiles"]

REMAINING_TABLES = [
    "interview_questions",
    "candidate_question_history",
    "question_set_assignments",
    "candidate_profile_ext",
    "candidate_work_experience",
    "candidate_education",
    "candidate_skills",
    "candidate_certifications",
    "candidate_projects",
    "proctoring_violations",
    "proctoring_transcripts",
]

COMPANY_DOMAIN_COLUMNS = [
    ("domain_verified",     "BOOLEAN NOT NULL DEFAULT 0"),
    ("domain_pending",      "VARCHAR(255) NULL"),
    ("domain_verify_token", "VARCHAR(64)  NULL"),
]


def column_exists(conn, table: str, column: str) -> bool:
    q = text("""
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = DATABASE() AND table_name = :t AND column_name = :c
        LIMIT 1
    """)
    return conn.execute(q, {"t": table, "c": column}).fetchone() is not None


def table_exists(conn, table: str) -> bool:
    q = text("""
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = DATABASE() AND table_name = :t LIMIT 1
    """)
    return conn.execute(q, {"t": table}).fetchone() is not None


def constraint_exists(conn, table: str, constraint: str) -> bool:
    q = text("""
        SELECT 1 FROM information_schema.table_constraints
        WHERE table_schema = DATABASE() AND table_name = :t AND constraint_name = :c
        LIMIT 1
    """)
    return conn.execute(q, {"t": table, "c": constraint}).fetchone() is not None


def index_exists(conn, table: str, index_name: str) -> bool:
    q = text("""
        SELECT 1 FROM information_schema.statistics
        WHERE table_schema = DATABASE() AND table_name = :t AND index_name = :i
        LIMIT 1
    """)
    return conn.execute(q, {"t": table, "i": index_name}).fetchone() is not None


def ensure_company_id(conn, table: str) -> str:
    """Idempotently add company_id + index + FK to the given table.
    Returns a short status string for logging.
    """
    if not table_exists(conn, table):
        return f"[skip] {table}: table does not exist"

    actions = []
    if not column_exists(conn, table, "company_id"):
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN company_id VARCHAR(36) NULL"))
        actions.append("add column")

    idx = f"ix_{table}_company_id"
    if not index_exists(conn, table, idx):
        conn.execute(text(f"CREATE INDEX {idx} ON {table} (company_id)"))
        actions.append("add index")

    fk = f"fk_{table}_company_id"
    if not constraint_exists(conn, table, fk):
        conn.execute(text(
            f"ALTER TABLE {table} ADD CONSTRAINT {fk} "
            "FOREIGN KEY (company_id) REFERENCES companies(id)"
        ))
        actions.append("add fk")

    return f"[ok] {table}: " + (", ".join(actions) if actions else "already current")


def backfill_orphans(conn):
    """Backfill company_id from parent rows. Idempotent — only touches NULLs."""
    sqls = [
        # from migration e5f6a7b8c9d0
        ("ats_results <- candidates", """
            UPDATE ats_results a JOIN candidates c ON a.candidate_id = c.id
            SET a.company_id = c.company_id
            WHERE a.company_id IS NULL AND c.company_id IS NOT NULL
        """),
        ("candidate_stage_history <- candidates", """
            UPDATE candidate_stage_history h JOIN candidates c ON h.candidate_id = c.id
            SET h.company_id = c.company_id
            WHERE h.company_id IS NULL AND c.company_id IS NOT NULL
        """),
        ("interview_feedback <- interviews", """
            UPDATE interview_feedback f JOIN interviews i ON f.interview_id = i.id
            SET f.company_id = i.company_id
            WHERE f.company_id IS NULL AND i.company_id IS NOT NULL
        """),
        # support_messages may not exist on this DB — guarded by ensure_company_id() skip above
        ("proctoring_sessions <- interviews", """
            UPDATE proctoring_sessions p JOIN interviews i ON p.test_id = i.id
            SET p.company_id = i.company_id
            WHERE p.company_id IS NULL AND i.company_id IS NOT NULL
        """),
        # from migration f6a7b8c9d0e1
        ("hr_profiles <- users", """
            UPDATE hr_profiles h JOIN users u ON h.user_id = u.id
            SET h.company_id = u.company_id
            WHERE h.company_id IS NULL AND u.company_id IS NOT NULL
        """),
        ("interviewer_profiles <- users", """
            UPDATE interviewer_profiles ip JOIN users u ON ip.user_id = u.id
            SET ip.company_id = u.company_id
            WHERE ip.company_id IS NULL AND u.company_id IS NOT NULL
        """),
        # from migration g7b8c9d0e1f2
        ("interview_questions <- interviews", """
            UPDATE interview_questions q JOIN interviews i ON q.interview_id = i.id
            SET q.company_id = i.company_id
            WHERE q.company_id IS NULL AND i.company_id IS NOT NULL
        """),
        ("candidate_question_history <- interviews", """
            UPDATE candidate_question_history h JOIN interviews i ON h.interview_id = i.id
            SET h.company_id = i.company_id
            WHERE h.company_id IS NULL AND i.company_id IS NOT NULL
        """),
        ("question_set_assignments <- interviews", """
            UPDATE question_set_assignments qsa JOIN interviews i ON qsa.interview_id = i.id
            SET qsa.company_id = i.company_id
            WHERE qsa.company_id IS NULL AND i.company_id IS NOT NULL
        """),
        ("candidate_profile_ext <- users", """
            UPDATE candidate_profile_ext p JOIN users u ON p.user_id = u.id
            SET p.company_id = u.company_id
            WHERE p.company_id IS NULL AND u.company_id IS NOT NULL
        """),
    ]
    for child in (
        "candidate_work_experience", "candidate_education", "candidate_skills",
        "candidate_certifications", "candidate_projects",
    ):
        sqls.append((f"{child} <- candidate_profile_ext", f"""
            UPDATE {child} c JOIN candidate_profile_ext p ON c.profile_id = p.id
            SET c.company_id = p.company_id
            WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
        """))
    sqls += [
        ("proctoring_violations <- proctoring_sessions", """
            UPDATE proctoring_violations v JOIN proctoring_sessions s ON v.session_id = s.id
            SET v.company_id = s.company_id
            WHERE v.company_id IS NULL AND s.company_id IS NOT NULL
        """),
        ("proctoring_transcripts <- proctoring_sessions", """
            UPDATE proctoring_transcripts t JOIN proctoring_sessions s ON t.session_id = s.id
            SET t.company_id = s.company_id
            WHERE t.company_id IS NULL AND s.company_id IS NOT NULL
        """),
    ]

    for label, sql in sqls:
        # Skip if either side of the JOIN doesn't exist on this DB.
        first_table = label.split()[0]
        if not table_exists(conn, first_table):
            print(f"  [skip backfill] {label}: {first_table} does not exist")
            continue
        result = conn.execute(text(sql))
        print(f"  [ok backfill] {label}: {result.rowcount} rows")


def ensure_company_domain_columns(conn):
    if not table_exists(conn, "companies"):
        print("[skip] companies table missing")
        return
    for col, ddl in COMPANY_DOMAIN_COLUMNS:
        if not column_exists(conn, "companies", col):
            conn.execute(text(f"ALTER TABLE companies ADD COLUMN {col} {ddl}"))
            print(f"[ok] companies.{col}: added")
        else:
            print(f"[ok] companies.{col}: already present")


def main():
    engine = db_manager.engine
    with engine.begin() as conn:
        print("=== companies domain-verification columns ===")
        ensure_company_domain_columns(conn)

        print("\n=== company_id on orphan tenant tables ===")
        for t in ORPHAN_TABLES:
            print(" ", ensure_company_id(conn, t))

        print("\n=== company_id on profile tables ===")
        for t in PROFILE_TABLES:
            print(" ", ensure_company_id(conn, t))

        print("\n=== company_id on remaining orphan tables ===")
        for t in REMAINING_TABLES:
            print(" ", ensure_company_id(conn, t))

        print("\n=== backfill company_id from parents ===")
        backfill_orphans(conn)

    print("\nDone. Re-run is safe.")


if __name__ == "__main__":
    main()
