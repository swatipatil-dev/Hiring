"""company_mailboxes + pipeline_step_configs.mailbox_id

Admin-defined named sender mailboxes. HR selects one per pipeline step via
``pipeline_step_configs.mailbox_id`` instead of typing raw SMTP credentials, so
the per-step smtp_* columns are dropped.

Idempotent: guards on table/column existence.

Revision ID: r8h9i0j1k2l3
Revises: q7g8h9i0j1k2
Create Date: 2026-06-30 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'r8h9i0j1k2l3'
down_revision = 'q7g8h9i0j1k2'
branch_labels = None
depends_on = None

_STEP_SMTP_COLS = ["smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from_email"]


def _has_table(table: str) -> bool:
    return table in sa.inspect(op.get_bind()).get_table_names()


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_table("company_mailboxes"):
        op.create_table(
            "company_mailboxes",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("company_id", sa.String(length=36), nullable=False),
            sa.Column("name", sa.String(length=100), nullable=False),
            sa.Column("smtp_host", sa.String(length=255), nullable=True),
            sa.Column("smtp_port", sa.Integer(), nullable=True),
            sa.Column("smtp_user", sa.String(length=255), nullable=True),
            sa.Column("smtp_pass", sa.String(length=255), nullable=True),
            sa.Column("smtp_from_email", sa.String(length=255), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("company_id", "name", name="uq_mailbox_company_name"),
        )
        op.create_index("ix_company_mailboxes_company_id", "company_mailboxes", ["company_id"])

    if _has_table("pipeline_step_configs"):
        if not _has_column("pipeline_step_configs", "mailbox_id"):
            op.add_column("pipeline_step_configs",
                          sa.Column("mailbox_id", sa.String(length=36), nullable=True))
            op.create_foreign_key(
                "fk_pipeline_step_mailbox", "pipeline_step_configs", "company_mailboxes",
                ["mailbox_id"], ["id"],
            )
        for col in _STEP_SMTP_COLS:
            if _has_column("pipeline_step_configs", col):
                op.drop_column("pipeline_step_configs", col)


def downgrade() -> None:
    if _has_table("pipeline_step_configs"):
        for col, type_ in [
            ("smtp_host", sa.String(length=255)), ("smtp_port", sa.Integer()),
            ("smtp_user", sa.String(length=255)), ("smtp_pass", sa.String(length=255)),
            ("smtp_from_email", sa.String(length=255)),
        ]:
            if not _has_column("pipeline_step_configs", col):
                op.add_column("pipeline_step_configs", sa.Column(col, type_, nullable=True))
        if _has_column("pipeline_step_configs", "mailbox_id"):
            try:
                op.drop_constraint("fk_pipeline_step_mailbox", "pipeline_step_configs",
                                   type_="foreignkey")
            except Exception:
                pass
            op.drop_column("pipeline_step_configs", "mailbox_id")

    if _has_table("company_mailboxes"):
        op.drop_index("ix_company_mailboxes_company_id", table_name="company_mailboxes")
        op.drop_table("company_mailboxes")
