"""add_job_transfer_history

HR can hand a job over to another HR. Ownership is `job_postings.hr_id`, which only ever
holds the current owner, so without this table there is no record of who owned a job
before, who moved it, or why.

One row per transfer. `job_id` is deliberately not an FK to `job_postings`: the permanent
job delete removes children by explicit bulk delete, and an FK here would block it.

Idempotent: only creates the table if it isn't already present.

Revision ID: g3w4x5y6z7a8
Revises: f2v3w4x5y6z7
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'g3w4x5y6z7a8'
down_revision = 'f2v3w4x5y6z7'
branch_labels = None
depends_on = None

TABLE = "job_transfer_history"


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
        sa.Column("from_hr_id", sa.String(36), sa.ForeignKey("hr_profiles.id"), nullable=True),
        sa.Column("from_hr_name", sa.String(255), nullable=True),
        sa.Column("to_hr_id", sa.String(36), sa.ForeignKey("hr_profiles.id"), nullable=False),
        sa.Column("to_hr_name", sa.String(255), nullable=True),
        sa.Column("transferred_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("transferred_by_email", sa.String(255), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("offers_moved", sa.Integer(), server_default="0"),
        sa.Column("interviews_moved", sa.Integer(), server_default="0"),
        sa.Column("bulk", sa.Boolean(), server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )
    op.create_index(f"ix_{TABLE}_company_id", TABLE, ["company_id"])
    op.create_index(f"ix_{TABLE}_job_id", TABLE, ["job_id"])
    op.create_index(f"ix_{TABLE}_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
