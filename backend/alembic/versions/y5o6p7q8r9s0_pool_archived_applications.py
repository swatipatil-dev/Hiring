"""Preserve applications whose job posting was hard-deleted

Closing or hard-deleting a job used to strand its candidates. Hard delete is the worse
case: it removes every `Candidate` row for the job and then the job itself, and for an
HR-created candidate (no portal account — everyone promoted via Move to ATS) that row is
the only copy of their name, email, phone, parsed skills and resume pointer.

The pool now takes a copy before the delete runs. Most of it fits existing columns, but
"which job did they apply for" does not: `PoolApplication` is read live from `candidates`
joined to `job_postings` on every request, specifically so it cannot go stale, and a hard
delete destroys both sides of that join at once.

  archived_applications  JSON list shaped like PoolApplication — {candidate_id, job_id,
                         job_title, stage, applied_at, ats_score} — for applications whose
                         job posting no longer exists.

Denormalised text on purpose. A foreign key would either be NULLed alongside best_job_id
or refuse the delete outright with MySQL 1451, and the job title is exactly what has to
outlive the job.

Idempotent per object.

Revision ID: y5o6p7q8r9s0
Revises: x4n5o6p7q8r9
Create Date: 2026-08-13 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'y5o6p7q8r9s0'
down_revision: Union[str, None] = 'x4n5o6p7q8r9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POOL = "resume_pool"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    if not _has_table(POOL):
        return

    if not _has_column(POOL, "archived_applications"):
        op.add_column(POOL, sa.Column("archived_applications", sa.Text, nullable=True))


def downgrade() -> None:
    if not _has_table(POOL):
        return

    # No foreign key on this column, so no constraint to drop first.
    if _has_column(POOL, "archived_applications"):
        op.drop_column(POOL, "archived_applications")
