"""merge communication answers with develop head

Revision ID: aaa4b350b0b5
Revises: b1c2d3e4f5a6, o5e6f7g8h9i0
Create Date: 2026-06-08 18:23:05.835191

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'aaa4b350b0b5'
down_revision: Union[str, None] = ('b1c2d3e4f5a6', 'o5e6f7g8h9i0')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
