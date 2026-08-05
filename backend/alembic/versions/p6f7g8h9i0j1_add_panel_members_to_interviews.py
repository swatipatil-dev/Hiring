"""add_panel_members_to_interviews

Panel interview support: an interview can carry multiple interviewers. The roster
is stored as a JSON list of {"id","name","email"} in ``panel_members``. The
existing ``interviewer_name``/``interviewer_email`` continue to hold the panel
lead (panel[0]) so single-interviewer reads keep working unchanged.

Idempotent: only adds the column if it isn't already present.

Revision ID: p6f7g8h9i0j1
Revises: o5e6f7g8h9i0
Create Date: 2026-06-08 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa

revision: str = 'p6f7g8h9i0j1'
down_revision = 'o5e6f7g8h9i0'
branch_labels = None
depends_on = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade() -> None:
    if not _has_column("interviews", "panel_members"):
        op.add_column("interviews", sa.Column("panel_members", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("interviews", "panel_members"):
        op.drop_column("interviews", "panel_members")
