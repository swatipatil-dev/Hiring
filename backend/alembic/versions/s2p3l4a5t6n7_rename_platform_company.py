"""Rename the platform company from "Innomax IT Solutions" to "Innomax Skills"

The platform company (slug PLATFORM_COMPANY_SLUG, default "innomax") was created
under the code default name "Innomax IT Solutions" — which is also the name of a
client company. Its public careers page, /innomax/jobs, therefore looked like that
client's job board while listing the platform's own jobs.

Data only. Renames the row only while it still carries the old default, so a name
someone has since set on purpose is left alone. Downgrade restores the old default
under the same condition.

Revision ID: s2p3l4a5t6n7
Revises: r1w2f3l4o5w6
Create Date: 2026-10-03 00:00:00.000000
"""
import os
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 's2p3l4a5t6n7'
down_revision: Union[str, None] = 'r1w2f3l4o5w6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_NAME = "Innomax IT Solutions"
NEW_NAME = "Innomax Skills"


def _rename(old: str, new: str) -> None:
    slug = os.getenv("PLATFORM_COMPANY_SLUG", "innomax").strip().lower()
    op.get_bind().execute(
        sa.text("UPDATE companies SET name = :new "
                "WHERE LOWER(slug) = :slug AND name = :old"),
        {"new": new, "old": old, "slug": slug},
    )


def upgrade() -> None:
    _rename(OLD_NAME, NEW_NAME)


def downgrade() -> None:
    _rename(NEW_NAME, OLD_NAME)
