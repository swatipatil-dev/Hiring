"""Add preferred_skills to job_postings

Until now a job had one skills column, so every nice-to-have was stored as a
hard requirement. The distinction already existed upstream — `candidate_requests`
and `jd_templates` both carry `required_skills` and `preferred_skills` — and was
flattened into one list when HR approved a request into a job.

NULL is meaningful and must stay that way: it marks a job whose HR has never
separated the two, and the scorer treats those exactly as before. An explicit
'[]' means someone has looked at the split and left the preferred list empty.
So this column is never backfilled.

Idempotent (create_all does not add columns to an existing table, so this
migration is the authoritative add — same pattern as the language add).

Revision ID: g3w4x5y6z7a8
Revises: f2v3w4x5y6z7
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'g3w4x5y6z7a8'
down_revision: Union[str, None] = 'f2v3w4x5y6z7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = [c["name"] for c in sa.inspect(bind).get_columns(table)]
    return column in cols


def upgrade() -> None:
    if not _has_column("job_postings", "preferred_skills"):
        op.add_column("job_postings", sa.Column("preferred_skills", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("job_postings", "preferred_skills"):
        op.drop_column("job_postings", "preferred_skills")
