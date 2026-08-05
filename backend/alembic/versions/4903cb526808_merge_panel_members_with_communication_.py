"""merge panel-members with communication head

Revision ID: 4903cb526808
Revises: aaa4b350b0b5, p6f7g8h9i0j1
Create Date: 2026-06-08 18:37:40.884131

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4903cb526808'
down_revision: Union[str, None] = ('aaa4b350b0b5', 'p6f7g8h9i0j1')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
