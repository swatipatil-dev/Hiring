"""Record where each parsed resume-profile field came from

The pool's identity fields (full_name, email, phone, college, year_of_passing,
job_role, highest_degree) are produced by `parse_resume_profile`, a deliberately
conservative regex parser that returns None rather than guess. Gemini now fills the
fields it leaves blank — see src/services/llm_resume_extractor.py.

An inferred value and a parsed one are not equally trustworthy, and once both sit in
the same VARCHAR they are indistinguishable. This column keeps them apart:

  profile_source  JSON object mapping field name -> "regex" | "llm", e.g.
                  {"full_name": "regex", "college": "llm"}

It drives the "AI-filled — please verify" badge HR sees before promoting a pool entry
into a real Candidate, and makes the LLM's actual contribution measurable rather than
a matter of impression.

NULL on every row written before this existed; readers treat absent as "regex".
Text rather than a native JSON column to match this table's existing convention
(extracted_skills, match_results and archived_applications are all Text holding JSON).

Idempotent per object.

Revision ID: c9s0t1u2v3w4
Revises: b8r9s0t1u2v3
Create Date: 2026-08-31 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9s0t1u2v3w4'
down_revision: Union[str, None] = 'b8r9s0t1u2v3'
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

    if not _has_column(POOL, "profile_source"):
        op.add_column(POOL, sa.Column("profile_source", sa.Text, nullable=True))


def downgrade() -> None:
    if not _has_table(POOL):
        return

    # No foreign key on this column, so no constraint to drop first.
    if _has_column(POOL, "profile_source"):
        op.drop_column(POOL, "profile_source")
