"""normalize_interview_status_case

``interviews.status`` is written uppercase almost everywhere ("SCHEDULED", "COMPLETED",
"DRAFT", "READY") — but two writers emitted lowercase ("completed"), and three readers
compared lowercase. Both casings therefore exist in live data, and every uppercase
comparison silently skipped the lowercase rows: interview history, dashboard counts, and
the round-2 gate all under-reported.

The code is fixed to compare on UPPER(); this normalises what is already stored so the
rows agree with the writers again. Idempotent — re-running changes nothing, because the
WHERE clause only matches rows that are not already uppercase.

Deliberately not a CHECK constraint or an ENUM column: the app writes a handful of values
that vary by flow, and pinning them in the schema would turn a future status addition into
a migration. UPPER() on both sides is the invariant that matters.

No downgrade: re-lowercasing would restore the bug, and there is no record of which rows
were originally lowercase.

Revision ID: e1u2v3w4x5y6
Revises: d0t1u2v3w4x5
Create Date: 2026-09-16 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'e1u2v3w4x5y6'
down_revision = 'd0t1u2v3w4x5'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "UPDATE interviews SET status = UPPER(status) "
            "WHERE status IS NOT NULL AND status <> UPPER(status)"
        )
    )


def downgrade() -> None:
    # Intentionally a no-op: the lowercase values were the defect.
    pass
