"""Add test_cases JSON column to interview_questions

Stores HackerRank-style test cases on AI-generated coding questions:
``[{"input", "expected_output", "is_sample", "weight"}]``. Idempotent (the app
also bootstraps schema via create_all, but create_all does not add columns to an
existing table, so this migration is the authoritative add).

Revision ID: m3c4d5e6f7g8
Revises: l2b3c4d5e6f7
Create Date: 2026-05-27 00:45:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'm3c4d5e6f7g8'
down_revision: Union[str, None] = 'l2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_column("interview_questions", "test_cases"):
        op.add_column("interview_questions", sa.Column("test_cases", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("interview_questions", "test_cases"):
        op.drop_column("interview_questions", "test_cases")
