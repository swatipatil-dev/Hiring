"""add_rating_to_interviews

Revision ID: a1b2c3d4e5f6
Revises: 2a077fcbf6c4
Create Date: 2026-04-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'a1b2c3d4e5f6'
down_revision = '2a077fcbf6c4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('interviews', sa.Column('rating', sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column('interviews', 'rating')
