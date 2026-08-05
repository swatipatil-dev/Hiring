"""fix_proctoring_session_test_id_type

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-05-07 00:00:00.000000

Change proctoring_sessions.test_id from VARCHAR(36) to INT with a proper
foreign key to interviews.id, matching the Integer primary key of Interview.
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'c3d4e5f6a7b8'
down_revision = 'b2c3d4e5f6a7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop FK if one existed (unlikely, but safe)
    try:
        op.drop_constraint('fk_proctoring_sessions_test_id', 'proctoring_sessions', type_='foreignkey')
    except Exception:
        pass

    # Alter column type: VARCHAR(36) → INT
    op.alter_column(
        'proctoring_sessions', 'test_id',
        existing_type=sa.String(36),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using='test_id::integer'   # no-op on MySQL but harmless
    )

    # Add the FK constraint
    op.create_foreign_key(
        'fk_proctoring_sessions_test_id',
        'proctoring_sessions', 'interviews',
        ['test_id'], ['id']
    )


def downgrade() -> None:
    op.drop_constraint('fk_proctoring_sessions_test_id', 'proctoring_sessions', type_='foreignkey')
    op.alter_column(
        'proctoring_sessions', 'test_id',
        existing_type=sa.Integer(),
        type_=sa.String(36),
        existing_nullable=False
    )
