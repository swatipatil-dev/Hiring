"""add_company_id_to_profile_tables

Closes the last cross-tenant data-leak vector for the profile tables.

Background
----------
HRProfile and InterviewerProfile are 1:1 with User and were left unscoped on
the rationale that "scope flows from User.company_id via the FK".  That holds
only when callers JOIN users.  ``GET /interviewer/hr-list`` at
routes/enhanced_interviewer_routes.py executed ``db.query(HRProfile).all()``
with no join — so every company's HRs spilled into the test-publish dropdown.

Fix
---
Give both profile tables their own ``company_id`` column so the SQLAlchemy
before_compile auto-filter (src/tenancy/events.py) catches direct queries.
Backfill from each profile row's user.company_id.

For each table (hr_profiles, interviewer_profiles):
  1. Add nullable ``company_id String(36)`` + index + FK to companies.id.
  2. Backfill UPDATE … JOIN users (idempotent — only rows where the column
     is still NULL).

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-19 13:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f6a7b8c9d0e1'
down_revision: Union[str, None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_PROFILE_TABLES = ["hr_profiles", "interviewer_profiles"]


def upgrade() -> None:
    # 1. Schema.
    for table in _PROFILE_TABLES:
        op.add_column(table, sa.Column("company_id", sa.String(length=36), nullable=True))
        op.create_index(f"ix_{table}_company_id", table, ["company_id"], unique=False)
        op.create_foreign_key(
            f"fk_{table}_company_id",
            table,
            "companies",
            ["company_id"],
            ["id"],
        )

    # 2. Backfill from the owning user. Idempotent.
    op.execute("""
        UPDATE hr_profiles h
        JOIN users u ON h.user_id = u.id
        SET h.company_id = u.company_id
        WHERE h.company_id IS NULL AND u.company_id IS NOT NULL
    """)

    op.execute("""
        UPDATE interviewer_profiles ip
        JOIN users u ON ip.user_id = u.id
        SET ip.company_id = u.company_id
        WHERE ip.company_id IS NULL AND u.company_id IS NOT NULL
    """)


def downgrade() -> None:
    for table in reversed(_PROFILE_TABLES):
        op.drop_constraint(f"fk_{table}_company_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_company_id", table_name=table)
        op.drop_column(table, "company_id")
