"""Add preferred_skills to job_postings

Until now a job had one skills column, so every nice-to-have was stored as a
hard requirement. The distinction already existed upstream — `candidate_requests`
and `jd_templates` both carry `required_skills` and `preferred_skills` — and was
flattened into one list when HR approved a request into a job.

NULL is meaningful and must stay that way: it marks a job whose HR has never
separated the two, and the scorer treats those exactly as before. An explicit
'[]' means someone has looked at the split and left the preferred list empty.
So this column is never backfilled.

Renumbered on merge into dont_know: it was written as g3w4x5y6z7a8, an id already
taken there by add_job_transfer_history, and two revisions sharing an id break
`alembic upgrade`. j6z7a8b9c0d1 adds the same column idempotently, so on a database
that already ran it this revision does nothing.

Idempotent (create_all does not add columns to an existing table, so this
migration is the authoritative add — same pattern as the language add).

Revision ID: l0a1b2c3d4e5
Revises: k8b2c3d4e5f6
Create Date: 2026-09-23 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'l0a1b2c3d4e5'
down_revision: Union[str, None] = 'k8b2c3d4e5f6'
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
