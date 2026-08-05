"""Add language column to interview_questions

Stores the target programming language (judge key: python/java/cpp/javascript/sql)
for AI-generated coding questions, chosen by HR at generation time. NULL for
non-coding and legacy coding questions (treated as python at grade time).

Idempotent (create_all does not add columns to an existing table, so this
migration is the authoritative add — same pattern as the test_cases add).

Revision ID: t0j1k2l3m4n5
Revises: s9i0j1k2l3m4
Create Date: 2026-07-21 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 't0j1k2l3m4n5'
down_revision: Union[str, None] = 's9i0j1k2l3m4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_column("interview_questions", "language"):
        op.add_column("interview_questions", sa.Column("language", sa.String(20), nullable=True))


def downgrade() -> None:
    if _has_column("interview_questions", "language"):
        op.drop_column("interview_questions", "language")
