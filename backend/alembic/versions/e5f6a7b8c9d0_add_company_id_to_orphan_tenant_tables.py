"""add_company_id_to_orphan_tenant_tables

Closes cross-tenant data leakage gaps for tables that hold per-company rows
but were never given a direct ``company_id`` FK in the original tenancy work:

* ats_results
* candidate_stage_history
* interview_feedback
* support_messages
* proctoring_sessions

For each table the migration:
  1. Adds a nullable ``company_id String(36)`` column with an FK to companies.id
     and an index for the auto-filter's WHERE predicate.
  2. Backfills rows by joining to the parent table that already carries
     ``company_id`` (Candidate / Interview / SupportRequest). Idempotent — only
     touches rows whose company_id is still NULL.

Skipped intentionally:
  * candidate_work_experience / education / skills / certifications / projects
    — they cascade-delete with their parent profile and are never queried
    across tenants in the current codebase.
  * proctoring_violations / proctoring_transcripts — child of
    proctoring_sessions, cascade-deleted, never queried across tenants.
  * hr_profiles / interviewer_profiles — 1:1 with User, scope flows from
    User.company_id via the FK relationship.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-05-19 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, None] = 'd4e5f6a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_ORPHAN_TABLES = [
    "ats_results",
    "candidate_stage_history",
    "interview_feedback",
    "support_messages",
    "proctoring_sessions",
]


def upgrade() -> None:
    # 1. Schema: add nullable company_id + FK + index on each orphan table.
    for table in _ORPHAN_TABLES:
        op.add_column(table, sa.Column("company_id", sa.String(length=36), nullable=True))
        op.create_index(f"ix_{table}_company_id", table, ["company_id"], unique=False)
        op.create_foreign_key(
            f"fk_{table}_company_id",
            table,
            "companies",
            ["company_id"],
            ["id"],
        )

    # 2. Backfill from the parent row's company_id. All idempotent — only
    # updates rows where the new column is still NULL, so safe to re-run.
    op.execute("""
        UPDATE ats_results a
        JOIN candidates c ON a.candidate_id = c.id
        SET a.company_id = c.company_id
        WHERE a.company_id IS NULL AND c.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE candidate_stage_history h
        JOIN candidates c ON h.candidate_id = c.id
        SET h.company_id = c.company_id
        WHERE h.company_id IS NULL AND c.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE interview_feedback f
        JOIN interviews i ON f.interview_id = i.id
        SET f.company_id = i.company_id
        WHERE f.company_id IS NULL AND i.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE support_messages m
        JOIN support_requests s ON m.request_id = s.id
        SET m.company_id = s.company_id
        WHERE m.company_id IS NULL AND s.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE proctoring_sessions p
        JOIN interviews i ON p.test_id = i.id
        SET p.company_id = i.company_id
        WHERE p.company_id IS NULL AND i.company_id IS NOT NULL
    """)


def downgrade() -> None:
    for table in reversed(_ORPHAN_TABLES):
        op.drop_constraint(f"fk_{table}_company_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")
