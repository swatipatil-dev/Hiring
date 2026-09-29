"""question_tenancy

The question-bank tables become TenantScoped, so the tenant filter scopes them like
every other company-private table:

- interview_questions, question_set_assignments, candidate_question_history already
  have `company_id` in the database (g7b8c9d0e1f2) but the ORM never declared it, so
  nothing kept it filled. They are re-backfilled here.
- communication_answers (a candidate's recorded voice answers) gains `company_id`.

Every row takes its company from its interview. A row whose interview has no company
stays NULL and becomes invisible to every tenant, which is the point: run
scripts/backfill_orphan_company_ids.py first so interviews themselves are homed.

Idempotent: the column, index and foreign key are each added only when missing, and the
backfills only touch NULL rows.

Revision ID: k8b2c3d4e5f6
Revises: k7a1b2c3d4e5
Create Date: 2026-09-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'k8b2c3d4e5f6'
down_revision: Union[str, None] = 'k7a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    "interview_questions",
    "question_set_assignments",
    "candidate_question_history",
    "communication_answers",
]


def _insp():
    return sa.inspect(op.get_bind())


def _has_column(table, column):
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table, name):
    return name in {i["name"] for i in _insp().get_indexes(table)}


def _has_fk(table, name):
    return name in {fk.get("name") for fk in _insp().get_foreign_keys(table)}


def upgrade() -> None:
    for table in TABLES:
        if not _insp().has_table(table):
            continue
        if not _has_column(table, "company_id"):
            op.add_column(table, sa.Column("company_id", sa.String(36), nullable=True))
        if not _has_index(table, f"ix_{table}_company_id"):
            op.create_index(f"ix_{table}_company_id", table, ["company_id"])
        if not _has_fk(table, f"fk_{table}_company_id"):
            # Existing rows may point at a deleted company; a failed FK must not stop
            # the backfill, which is the part that matters for isolation.
            try:
                op.create_foreign_key(
                    f"fk_{table}_company_id", table, "companies", ["company_id"], ["id"],
                )
            except Exception as exc:  # pragma: no cover - depends on the live data
                print(f"[k8b2c3d4e5f6] skipped FK on {table}.company_id: {exc}")

        # Each row inherits its interview's company.
        op.execute(f"""
            UPDATE {table} t
            JOIN interviews i ON t.interview_id = i.id
            SET t.company_id = i.company_id
            WHERE t.company_id IS NULL AND i.company_id IS NOT NULL
        """)


def downgrade() -> None:
    # Only communication_answers gained the column here; the other three had it before.
    table = "communication_answers"
    if _insp().has_table(table) and _has_column(table, "company_id"):
        if _has_fk(table, f"fk_{table}_company_id"):
            op.drop_constraint(f"fk_{table}_company_id", table, type_="foreignkey")
        if _has_index(table, f"ix_{table}_company_id"):
            op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")
