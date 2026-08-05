"""add_test_round_to_interviews

Two-round test support: a per-candidate test belongs to round 1 (MCQ) or round 2
(coding). ``round_type`` is "mcq"/"coding"; NULL = combined single-round test.

Idempotent: only adds a column if it isn't already present (this revision was applied
to some DBs before the migration file existed).

Revision ID: o5e6f7g8h9i0
Revises: n4d5e6f7g8h9
Create Date: 2026-06-03 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'o5e6f7g8h9i0'
down_revision = 'n4d5e6f7g8h9'
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("interviews", "test_round"):
        op.add_column("interviews", sa.Column("test_round", sa.Integer(), nullable=True, server_default="1"))
    if not _has_column("interviews", "round_type"):
        op.add_column("interviews", sa.Column("round_type", sa.String(length=20), nullable=True))


def downgrade() -> None:
    if _has_column("interviews", "round_type"):
        op.drop_column("interviews", "round_type")
    if _has_column("interviews", "test_round"):
        op.drop_column("interviews", "test_round")
