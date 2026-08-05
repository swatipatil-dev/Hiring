"""add_is_scheduled_interview_to_interviews

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-05-07 00:00:00.000000

Add a proper boolean discriminator column so scheduled meeting records can be
distinguished from MCQ test records without relying on notes-field heuristics.
Existing rows with "Mode:" in notes are back-filled to is_scheduled_interview=1.
"""
from alembic import op
import sqlalchemy as sa

revision: str = 'd4e5f6a7b8c9'
down_revision = 'c3d4e5f6a7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'interviews',
        sa.Column('is_scheduled_interview', sa.Boolean(), nullable=False,
                  server_default='0')
    )
    # Back-fill existing meeting records that were created with the notes heuristic
    op.execute(
        "UPDATE interviews SET is_scheduled_interview = 1 WHERE notes LIKE 'Mode:%'"
    )


def downgrade() -> None:
    op.drop_column('interviews', 'is_scheduled_interview')
