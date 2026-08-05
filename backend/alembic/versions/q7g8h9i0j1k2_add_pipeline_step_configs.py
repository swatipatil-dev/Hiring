"""add_pipeline_step_configs

Per-company, per-step email notification config. Lets HR add extra recipients
(typed addresses + roles) and an optional custom sender mailbox for each
hiring-pipeline email step. A missing row preserves the previous behaviour
(primary recipient only, company/Innomax sender).

Idempotent: only creates the table if it isn't already present.

Revision ID: q7g8h9i0j1k2
Revises: 4903cb526808
Create Date: 2026-06-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'q7g8h9i0j1k2'
down_revision = '4903cb526808'
branch_labels = None
depends_on = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return table in insp.get_table_names()


def upgrade() -> None:
    if _has_table("pipeline_step_configs"):
        return
    op.create_table(
        "pipeline_step_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("company_id", sa.String(length=36), nullable=False),
        sa.Column("step_key", sa.String(length=50), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("extra_emails", sa.Text(), nullable=True),
        sa.Column("notify_roles", sa.Text(), nullable=True),
        sa.Column("as_cc", sa.Boolean(), nullable=True, server_default=sa.true()),
        sa.Column("smtp_host", sa.String(length=255), nullable=True),
        sa.Column("smtp_port", sa.Integer(), nullable=True),
        sa.Column("smtp_user", sa.String(length=255), nullable=True),
        sa.Column("smtp_pass", sa.String(length=255), nullable=True),
        sa.Column("smtp_from_email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "step_key", name="uq_pipeline_step_company"),
    )
    op.create_index(
        "ix_pipeline_step_configs_company_id",
        "pipeline_step_configs",
        ["company_id"],
    )


def downgrade() -> None:
    if _has_table("pipeline_step_configs"):
        op.drop_index("ix_pipeline_step_configs_company_id", table_name="pipeline_step_configs")
        op.drop_table("pipeline_step_configs")
