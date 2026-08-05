"""company_id on remaining orphan tables

Closes every remaining cross-tenant leak surface. The audit found 11 tables
without ``company_id`` that hold per-company data and are reachable via raw
SQL or future ORM list-queries:

* interview_questions          — fed by raw SQL in mcq_api.py and
                                 evaluation_routes; was completely unscoped.
* candidate_question_history   — sibling of interview_questions.
* question_set_assignments     — sibling of interview_questions.
* candidate_profile_ext        — candidate self-profile.
* candidate_work_experience    — child of profile_ext.
* candidate_education          — child of profile_ext.
* candidate_skills             — child of profile_ext.
* candidate_certifications     — child of profile_ext.
* candidate_projects           — child of profile_ext.
* proctoring_violations        — child of proctoring_sessions.
* proctoring_transcripts       — child of proctoring_sessions.

For each table:
  1. Add nullable ``company_id String(36)`` + index + FK to companies.id.
  2. Backfill from each row's parent (Interview, Candidate user,
     CandidateProfileExt, ProctoringSession) — idempotent.

Revision ID: g7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-05-19 14:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7b8c9d0e1f2'
down_revision: Union[str, None] = 'f6a7b8c9d0e1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLES = [
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


def upgrade() -> None:
    for table in _TABLES:
        op.add_column(table, sa.Column("company_id", sa.String(length=36), nullable=True))
        op.create_index(f"ix_{table}_company_id", table, ["company_id"], unique=False)
        op.create_foreign_key(
            f"fk_{table}_company_id",
            table,
            "companies",
            ["company_id"],
            ["id"],
        )

    # Backfill — each child inherits from its parent's company_id.

    op.execute("""
        UPDATE interview_questions q
        JOIN interviews i ON q.interview_id = i.id
        SET q.company_id = i.company_id
        WHERE q.company_id IS NULL AND i.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE candidate_question_history h
        JOIN interviews i ON h.interview_id = i.id
        SET h.company_id = i.company_id
        WHERE h.company_id IS NULL AND i.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE question_set_assignments qsa
        JOIN interviews i ON qsa.interview_id = i.id
        SET qsa.company_id = i.company_id
        WHERE qsa.company_id IS NULL AND i.company_id IS NOT NULL
    """)

    # candidate_profile_ext: scope from the owning user's company_id.
    op.execute("""
        UPDATE candidate_profile_ext p
        JOIN users u ON p.user_id = u.id
        SET p.company_id = u.company_id
        WHERE p.company_id IS NULL AND u.company_id IS NOT NULL
    """)

    # Profile-ext children: scope from their profile's company_id (set above).
    for child_table in (
        "candidate_work_experience",
        "candidate_education",
        "candidate_skills",
        "candidate_certifications",
        "candidate_projects",
    ):
        op.execute(f"""
            UPDATE {child_table} c
            JOIN candidate_profile_ext p ON c.profile_id = p.id
            SET c.company_id = p.company_id
            WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
        """)

    # Proctoring children: scope from session's company_id (set in
    # e5f6a7b8c9d0_add_company_id_to_orphan_tenant_tables).
    op.execute("""
        UPDATE proctoring_violations v
        JOIN proctoring_sessions s ON v.session_id = s.id
        SET v.company_id = s.company_id
        WHERE v.company_id IS NULL AND s.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE proctoring_transcripts t
        JOIN proctoring_sessions s ON t.session_id = s.id
        SET t.company_id = s.company_id
        WHERE t.company_id IS NULL AND s.company_id IS NOT NULL
    """)


def downgrade() -> None:
    for table in reversed(_TABLES):
        op.drop_constraint(f"fk_{table}_company_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")
