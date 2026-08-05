"""Idempotent backfill for the tables added in revision ``g7b8c9d0e1f2``.

Mirrors the migration's UPDATE statements so ops can re-run safely after
data drift (e.g. rows inserted by an older code path before the auto-stamp
was wired up).

Usage::

    python scripts/backfill_full_company_ids.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config.database_config_mysql import db_manager


BACKFILLS = [
    ("interview_questions", """
        UPDATE interview_questions q
        JOIN interviews i ON q.interview_id = i.id
        SET q.company_id = i.company_id
        WHERE q.company_id IS NULL AND i.company_id IS NOT NULL
    """),
    ("candidate_question_history", """
        UPDATE candidate_question_history h
        JOIN interviews i ON h.interview_id = i.id
        SET h.company_id = i.company_id
        WHERE h.company_id IS NULL AND i.company_id IS NOT NULL
    """),
    ("question_set_assignments", """
        UPDATE question_set_assignments qsa
        JOIN interviews i ON qsa.interview_id = i.id
        SET qsa.company_id = i.company_id
        WHERE qsa.company_id IS NULL AND i.company_id IS NOT NULL
    """),
    ("candidate_profile_ext", """
        UPDATE candidate_profile_ext p
        JOIN users u ON p.user_id = u.id
        SET p.company_id = u.company_id
        WHERE p.company_id IS NULL AND u.company_id IS NOT NULL
    """),
    ("candidate_work_experience", """
        UPDATE candidate_work_experience c
        JOIN candidate_profile_ext p ON c.profile_id = p.id
        SET c.company_id = p.company_id
        WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
    """),
    ("candidate_education", """
        UPDATE candidate_education c
        JOIN candidate_profile_ext p ON c.profile_id = p.id
        SET c.company_id = p.company_id
        WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
    """),
    ("candidate_skills", """
        UPDATE candidate_skills c
        JOIN candidate_profile_ext p ON c.profile_id = p.id
        SET c.company_id = p.company_id
        WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
    """),
    ("candidate_certifications", """
        UPDATE candidate_certifications c
        JOIN candidate_profile_ext p ON c.profile_id = p.id
        SET c.company_id = p.company_id
        WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
    """),
    ("candidate_projects", """
        UPDATE candidate_projects c
        JOIN candidate_profile_ext p ON c.profile_id = p.id
        SET c.company_id = p.company_id
        WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
    """),
    ("proctoring_violations", """
        UPDATE proctoring_violations v
        JOIN proctoring_sessions s ON v.session_id = s.id
        SET v.company_id = s.company_id
        WHERE v.company_id IS NULL AND s.company_id IS NOT NULL
    """),
    ("proctoring_transcripts", """
        UPDATE proctoring_transcripts t
        JOIN proctoring_sessions s ON t.session_id = s.id
        SET t.company_id = s.company_id
        WHERE t.company_id IS NULL AND s.company_id IS NOT NULL
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
            print(f"{table:30s}  null_before={before:>6}  updated={result.rowcount:>6}  null_after={after:>6}")


if __name__ == "__main__":
    main()
