"""add_job_hr_collaborators

A job has one owning HR (`job_postings.hr_id`). This table adds co-HRs: other HRs with
the same access to the same job, so two HRs can work one opening without copying it.

`uq_job_hr_collaborator` keeps an HR from being added to the same job twice.

Idempotent: only creates the table if it isn't already present.

Revision ID: h4x5y6z7a8b9
Revises: g3w4x5y6z7a8
Create Date: 2026-09-25 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'h4x5y6z7a8b9'
down_revision = 'g3w4x5y6z7a8'
branch_labels = None
depends_on = None

TABLE = "job_hr_collaborators"


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
        sa.Column("job_id", sa.String(36), sa.ForeignKey("job_postings.id"), nullable=False),
        sa.Column("hr_id", sa.String(36), sa.ForeignKey("hr_profiles.id"), nullable=False),
        sa.Column("added_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("job_id", "hr_id", name="uq_job_hr_collaborator"),
    )
    op.create_index(f"ix_{TABLE}_company_id", TABLE, ["company_id"])
    op.create_index(f"ix_{TABLE}_job_id", TABLE, ["job_id"])
    op.create_index(f"ix_{TABLE}_hr_id", TABLE, ["hr_id"])


def downgrade() -> None:
    if _has_table(TABLE):
        op.drop_table(TABLE)
