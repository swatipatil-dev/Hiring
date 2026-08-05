"""Allow same email under different companies

Drops the global UNIQUE index on ``users.email`` so the same email address can
register under multiple companies (one candidate, many client portals). Replaces
it with:

* a non-unique ``ix_users_email`` for fast lookups, and
* a composite UNIQUE ``uq_users_company_email`` on ``(company_id, email)`` so a
  given email may exist at most once per company.

Revision ID: h8c9d0e1f2g3
Revises: g7b8c9d0e1f2
Create Date: 2026-05-21 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'h8c9d0e1f2g3'
down_revision: Union[str, None] = 'g7b8c9d0e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Drop the old globally-unique index on email.
    op.drop_index("ix_users_email", table_name="users")
    # Recreate it as a plain (non-unique) lookup index.
    op.create_index("ix_users_email", "users", ["email"], unique=False)
    # Enforce uniqueness per-company instead.
    op.create_index(
        "uq_users_company_email",
        "users",
        ["company_id", "email"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_users_company_email", table_name="users")
    op.drop_index("ix_users_email", table_name="users")
    op.create_index("ix_users_email", "users", ["email"], unique=True)
