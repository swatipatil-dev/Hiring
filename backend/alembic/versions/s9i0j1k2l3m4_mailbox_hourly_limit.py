"""add company_mailboxes.hourly_limit

Per-mailbox hourly send cap for the rate-limited email queue. NULL means "use the
EMAIL_HOURLY_LIMIT env default". Idempotent.

Revision ID: s9i0j1k2l3m4
Revises: r8h9i0j1k2l3
Create Date: 2026-07-02 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 's9i0j1k2l3m4'
down_revision = 'r8h9i0j1k2l3'
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    insp = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("company_mailboxes", "hourly_limit"):
        op.add_column("company_mailboxes", sa.Column("hourly_limit", sa.Integer(), nullable=True))


def downgrade() -> None:
    if _has_column("company_mailboxes", "hourly_limit"):
        op.drop_column("company_mailboxes", "hourly_limit")
