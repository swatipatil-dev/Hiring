"""Indexes backing the HR Talent Pool

The Resume Matcher became a talent pool: HR browses everyone the company has seen,
including people who applied and people who were rejected. Two schema needs fall out
of that, both index-only — no new columns (``resume_pool.source`` simply gains a third
value, ``"applied"``, in its existing VARCHAR).

1. ``ix_candidates_email`` — the pool matches account-less candidates to a pool row by
   email, in a correlated EXISTS evaluated per scanned row. Without this index that is
   a full table scan per row. ``candidates.email`` is ``utf8mb4_unicode_ci``, which
   compares case-insensitively, so the query side uses a plain ``==`` and never
   ``LOWER()`` (which would discard the index).

2. ``uq_resume_pool_company_source_candidate`` — the concurrency guard for the orphan
   import pass, mirroring ``uq_resume_pool_company_source_user`` for people who have no
   user account. MySQL allows unlimited NULLs in a unique index, so rows that predate
   this (all HR uploads) are unaffected.

Idempotent per object. The unique constraint is gated on a duplicate pre-flight: if
existing data already violates it we log and skip rather than failing the upgrade,
because blocking a deploy over historical data is worse than running without the guard.

Revision ID: w3m4n5o6p7q8
Revises: v2l3m4n5o6p7
Create Date: 2026-07-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'w3m4n5o6p7q8'
down_revision: Union[str, None] = 'v2l3m4n5o6p7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POOL = "resume_pool"
CANDIDATES = "candidates"
UQ_SOURCE_CANDIDATE = "uq_resume_pool_company_source_candidate"
IX_CANDIDATE_EMAIL = "ix_candidates_email"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_index(table: str, index: str) -> bool:
    return index in {i["name"] for i in _inspector().get_indexes(table)}


def _has_unique(table: str, name: str) -> bool:
    insp = _inspector()
    names = {c["name"] for c in insp.get_unique_constraints(table)}
    # MySQL surfaces unique constraints as unique indexes too, driver depending.
    names |= {i["name"] for i in insp.get_indexes(table) if i.get("unique")}
    return name in names


def _duplicate_source_candidates(conn) -> int:
    return conn.execute(sa.text(f"""
        SELECT COUNT(*) FROM (
            SELECT company_id, source_candidate_id
            FROM {POOL}
            WHERE source_candidate_id IS NOT NULL
            GROUP BY company_id, source_candidate_id
            HAVING COUNT(*) > 1
        ) AS dupes
    """)).scalar() or 0


def upgrade() -> None:
    conn = op.get_bind()

    if _has_table(CANDIDATES) and not _has_index(CANDIDATES, IX_CANDIDATE_EMAIL):
        op.create_index(IX_CANDIDATE_EMAIL, CANDIDATES, ["email"])

    if not _has_table(POOL):
        return

    if not _has_unique(POOL, UQ_SOURCE_CANDIDATE):
        dupes = _duplicate_source_candidates(conn)
        if dupes:
            print(
                f"[{revision}] Skipping {UQ_SOURCE_CANDIDATE}: {dupes} "
                f"(company_id, source_candidate_id) group(s) already duplicated. "
                f"De-duplicate resume_pool and re-run this migration to add the guard."
            )
        else:
            op.create_unique_constraint(
                UQ_SOURCE_CANDIDATE, POOL, ["company_id", "source_candidate_id"],
            )


def downgrade() -> None:
    if _has_table(POOL) and _has_unique(POOL, UQ_SOURCE_CANDIDATE):
        op.drop_constraint(UQ_SOURCE_CANDIDATE, POOL, type_="unique")
    if _has_table(CANDIDATES) and _has_index(CANDIDATES, IX_CANDIDATE_EMAIL):
        op.drop_index(IX_CANDIDATE_EMAIL, table_name=CANDIDATES)
