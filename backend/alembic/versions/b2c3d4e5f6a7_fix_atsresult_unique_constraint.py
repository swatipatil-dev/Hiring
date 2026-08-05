"""fix_atsresult_unique_constraint

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-07 00:00:00.000000

Replace the single-column unique constraint on ats_results.candidate_id with a
composite unique constraint on (candidate_id, job_id) so a candidate can apply
to multiple jobs without triggering an IntegrityError on the second ATSResult INSERT.
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'b2c3d4e5f6a7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the old single-column unique index on candidate_id
    # (MySQL names it based on column; use try/except for portability)
    try:
        op.drop_index('candidate_id', table_name='ats_results')
    except Exception:
        pass
    try:
        op.drop_constraint('candidate_id', 'ats_results', type_='unique')
    except Exception:
        pass

    # Create the composite unique constraint
    op.create_unique_constraint(
        'uq_atsresult_candidate_job',
        'ats_results',
        ['candidate_id', 'job_id']
    )


def downgrade() -> None:
    op.drop_constraint('uq_atsresult_candidate_job', 'ats_results', type_='unique')
    op.create_index('candidate_id', 'ats_results', ['candidate_id'], unique=True)
