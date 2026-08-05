"""merge job_category and onboarding heads

Revision ID: 34716348e109
Revises: abf4d61600ac, i9d0e1f2g3h4
Create Date: 2026-05-25 14:51:28.882202

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '34716348e109'
down_revision: Union[str, None] = ('abf4d61600ac', 'i9d0e1f2g3h4')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
