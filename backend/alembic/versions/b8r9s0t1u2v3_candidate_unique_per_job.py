"""One candidate row per person per job

A person could be entered twice for the same opening. Five code paths create a
``Candidate`` — the two apply endpoints, the enhanced apply endpoint, HR's Move to ATS,
and the signup placeholder — and only some of them checked first, so whichever one lacked
a check could write a second row for someone already in the pipeline.

App-level checks cannot close this on their own: two requests racing each other both read
"not present" before either writes. The database is the only place the guarantee can
actually hold, and ``candidates`` is the one table whose data is clean enough to take it.

On the NULL rows, which this deliberately does NOT cover
--------------------------------------------------------
``candidates.job_id`` is nullable, and registration writes a placeholder row with
``job_id = NULL`` for every user (src/auth.py). MySQL treats NULLs as distinct in a unique
index, so those placeholders are not constrained by this — the same property
``uq_resume_pool_company_source_user`` relies on deliberately.

That is the right outcome rather than a gap to paper over: one placeholder per person is
already guaranteed upstream by ``uq_users_company_email``. Nobody should read this
constraint as covering every candidate row.

On the pre-flight
-----------------
Gated on a duplicate check that logs and skips rather than failing the upgrade. The
development database was clean when this was written, but production is a different
dataset, and blocking a deploy over historical rows is worse than running without the
guard until someone de-duplicates. Same reasoning, and same shape, as
``w3m4n5o6p7q8_talent_pool_indexes.py``.

Idempotent per object.

Revision ID: b8r9s0t1u2v3
Revises: a7q8r9s0t1u2
Create Date: 2026-08-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8r9s0t1u2v3'
down_revision: Union[str, None] = 'a7q8r9s0t1u2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "candidates"
UQ = "uq_candidates_company_job_email"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_unique(table: str, name: str) -> bool:
    insp = _inspector()
    names = {c["name"] for c in insp.get_unique_constraints(table)}
    # MySQL surfaces a unique constraint as a unique index too, driver depending.
    names |= {i["name"] for i in insp.get_indexes(table) if i.get("unique")}
    return name in names


def _duplicate_groups(conn) -> int:
    """How many (company, job, email) groups already hold more than one row.

    `job_id IS NOT NULL` because the signup placeholders are outside this constraint —
    counting them here would report a conflict the index would never actually raise.
    """
    return conn.execute(sa.text(f"""
        SELECT COUNT(*) FROM (
            SELECT company_id, job_id, email
            FROM {TABLE}
            WHERE job_id IS NOT NULL
            GROUP BY company_id, job_id, email
            HAVING COUNT(*) > 1
        ) AS dupes
    """)).scalar() or 0


def upgrade() -> None:
    conn = op.get_bind()
    if not _inspector().has_table(TABLE):
        return
    if _has_unique(TABLE, UQ):
        return

    dupes = _duplicate_groups(conn)
    if dupes:
        print(
            f"[{revision}] Skipping {UQ}: {dupes} (company_id, job_id, email) group(s) "
            f"already hold more than one candidate. De-duplicate `candidates` and re-run "
            f"this migration to add the guard."
        )
        return

    op.create_unique_constraint(UQ, TABLE, ["company_id", "job_id", "email"])


def downgrade() -> None:
    if _inspector().has_table(TABLE) and _has_unique(TABLE, UQ):
        op.drop_constraint(UQ, TABLE, type_="unique")
