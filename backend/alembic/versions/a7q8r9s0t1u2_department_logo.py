"""Store department logos on the server instead of in localStorage

Department logos were held as base64 data URLs in each admin's browser
(`hireflow_dept_logos`), so they were invisible to every other admin and lost with
the browser's site data.

  departments.logo_url  storage key for the uploaded logo, e.g. /logos/<uuid>.png

Named logo_url rather than logo_path to match companies.logo_url and, more
importantly, so scripts/verify_storage_keys.py detects it: that checker allow-lists
storage-key columns by name and walks Base.metadata, so this column is covered by
the missing-file check with no change to the script.

Idempotent per object.

Revision ID: a7q8r9s0t1u2
Revises: z6p7q8r9s0t1
Create Date: 2026-08-18 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7q8r9s0t1u2'
down_revision: Union[str, None] = 'z6p7q8r9s0t1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "departments"
COLUMN = "logo_url"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _inspector().get_columns(table)}


def upgrade() -> None:
    # A fresh database is built by create_all() then stamped, so the column may
    # already be present. See docs/DEPLOYMENT_NOTES.md.
    if not _has_table(TABLE):
        return
    if not _has_column(TABLE, COLUMN):
        op.add_column(TABLE, sa.Column(COLUMN, sa.String(500), nullable=True))


def downgrade() -> None:
    if _has_table(TABLE) and _has_column(TABLE, COLUMN):
        op.drop_column(TABLE, COLUMN)
