"""Move HR mail templates out of the browser and into the database

HR's mail templates lived in localStorage under `hf_mail_templates`, which made them
per-browser rather than per-company: a second HR user saw an empty list, and clearing
site data destroyed them. This adds the table they should always have been in.

  mail_templates  company-scoped subject/body templates, unique per (company, name).

`company_id` is nullable to match every other tenant-scoped table here, but rows are
always written with a company: the tenant read filter compares company_id to the
current company, which excludes NULL, so a NULL-tenant "global" template would be
invisible to the very query that needs it. Seeding is per-company instead.

Idempotent per object.

Revision ID: z6p7q8r9s0t1
Revises: y5o6p7q8r9s0
Create Date: 2026-08-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'z6p7q8r9s0t1'
down_revision: Union[str, None] = 'y5o6p7q8r9s0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "mail_templates"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {i["name"] for i in _inspector().get_indexes(table)}


def upgrade() -> None:
    # A fresh database is built by create_all() then stamped, so this table may
    # already exist by the time the migration runs. See docs/DEPLOYMENT_NOTES.md.
    if not _has_table(TABLE):
        op.create_table(
            TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36),
                      sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("created_by", sa.String(36),
                      sa.ForeignKey("users.id"), nullable=True),
            sa.Column("stage", sa.String(50), server_default="custom"),
            sa.Column("name", sa.String(255), nullable=False),
            sa.Column("subject", sa.String(500), nullable=True),
            sa.Column("body", sa.Text, nullable=True),
            sa.Column("is_builtin", sa.Boolean, server_default=sa.text("0")),
            sa.Column("is_active", sa.Boolean, server_default=sa.text("1")),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("updated_at", sa.DateTime, nullable=True),
            sa.UniqueConstraint("company_id", "name", name="uq_mail_tpl_company_name"),
        )

    if not _has_index(TABLE, "ix_mail_templates_company_id"):
        op.create_index("ix_mail_templates_company_id", TABLE, ["company_id"])


def downgrade() -> None:
    if _has_index(TABLE, "ix_mail_templates_company_id"):
        op.drop_index("ix_mail_templates_company_id", table_name=TABLE)
    if _has_table(TABLE):
        op.drop_table(TABLE)
