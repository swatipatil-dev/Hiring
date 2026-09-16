"""add_job_deletion_archives

Permanently deleting a job hard-deletes the tests written for it. Those templates carry
their author's name and have no candidate to ride along with, so the colleague who wrote a
job's test was erased with no surviving record anywhere.

This table is that record: one row per deleted job, with a JSON snapshot of its tests and
their authors. Deliberately no FK to `job_postings` or `interviews` — those rows are gone,
which is the reason this table exists.

Idempotent: only creates the table if it isn't already present.

Revision ID: f2v3w4x5y6z7
Revises: e1u2v3w4x5y6
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'f2v3w4x5y6z7'
down_revision = 'e1u2v3w4x5y6'
branch_labels = None
depends_on = None

TABLE = "job_deletion_archives"


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if _has_table(TABLE):
        return
    op.create_table(
        TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("job_id", sa.String(36), nullable=False),
        sa.Column("job_title", sa.String(255), nullable=False),
        sa.Column("department_name", sa.String(255), nullable=True),
        sa.Column("deleted_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("deleted_by_email", sa.String(255), nullable=True),
        sa.Column("candidates_archived", sa.Integer(), server_default="0"),
        sa.Column("interviews_archived", sa.Integer(), server_default="0"),
        sa.Column("tests", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(f"ix_{TABLE}_company_id", TABLE, ["company_id"])
    op.create_index(f"ix_{TABLE}_job_id", TABLE, ["job_id"])
    op.create_index(f"ix_{TABLE}_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
