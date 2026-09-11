"""Record what happened to every outbound email

Delivery left no trace in the database. The send helpers report failure by
return value rather than by raising, and most call sites discarded that return —
so a failed offer letter produced no row, no log line, and an API response
saying it had been sent. Retry state lived only in Redis and vanished on flush,
which meant "did candidate X get their offer letter?" could not be answered at
all.

  email_log  one row per outbound message: recipient, sending mailbox, pipeline
             step, status, error, attempt count.

``company_id`` is nullable to match every other tenant-scoped table here, but
rows are always written with a company. The RQ worker has no tenant bound, so
``before_flush`` will not stamp it — the worker sets it explicitly from the job
payload. A NULL would make the row permanently invisible to the HR listing
endpoint, which is the trap documented in docs/DASHBOARD_DATA_PITFALLS.md.

Indexes are chosen for the two real queries: "everything for this recipient"
(to_email) and the retention sweep (created_at).

Idempotent per object.

Revision ID: d0t1u2v3w4x5
Revises: c9s0t1u2v3w4
Create Date: 2026-09-10 00:00:00.000000
"""
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd0t1u2v3w4x5'
down_revision: Union[str, None] = 'c9s0t1u2v3w4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "email_log"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {i["name"] for i in _inspector().get_indexes(table)}


def _referenced_collation() -> Optional[str]:
    """Collation of `companies.id`, which this table's foreign key must match.

    Returns None on anything unexpected (a non-MySQL backend, a missing table) so
    the table is created with the database default.
    """
    try:
        return op.get_bind().execute(sa.text(
            "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'companies' "
            "AND COLUMN_NAME = 'id'"
        )).scalar()
    except Exception:
        return None


def upgrade() -> None:
    # A fresh database is built by create_all() then stamped, so this table may
    # already exist by the time the migration runs. See docs/DEPLOYMENT_NOTES.md.
    if not _has_table(TABLE):
        # Match the collation of the column the foreign key points at, rather than
        # naming one. A new table otherwise takes the DATABASE default, and where
        # that differs from companies.id MySQL rejects the FK outright with
        # "Referencing column ... are incompatible" (3780). Hardcoding a collation
        # has the same failure with extra steps: it works wherever the default
        # happens to agree and breaks everywhere else. Same approach as
        # x4n5o6p7q8r9_talent_pool_crm.py, which hit this first.
        table_kwargs = {"mysql_engine": "InnoDB"}
        collation = _referenced_collation()
        if collation:
            table_kwargs["mysql_charset"] = collation.split("_")[0]
            table_kwargs["mysql_collate"] = collation

        op.create_table(
            TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36),
                      sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("step_key", sa.String(50), nullable=True),
            sa.Column("to_email", sa.String(255), nullable=False),
            sa.Column("subject", sa.String(500), nullable=True),
            sa.Column("sender_user", sa.String(255), nullable=True),
            sa.Column("status", sa.String(20), nullable=False,
                      server_default="queued"),
            sa.Column("error_type", sa.String(20), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("attempt", sa.Integer(), server_default="0"),
            sa.Column("reference_type", sa.String(50), nullable=True),
            sa.Column("reference_id", sa.String(36), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            **table_kwargs,
        )

    if not _has_index(TABLE, "ix_email_log_company_id"):
        op.create_index("ix_email_log_company_id", TABLE, ["company_id"])
    if not _has_index(TABLE, "ix_email_log_to_email"):
        op.create_index("ix_email_log_to_email", TABLE, ["to_email"])
    if not _has_index(TABLE, "ix_email_log_created_at"):
        op.create_index("ix_email_log_created_at", TABLE, ["created_at"])


def downgrade() -> None:
    # Drop the table outright rather than the indexes first: MySQL refuses to
    # drop ix_email_log_company_id while the foreign key on that column needs it
    # ("Cannot drop index ...: needed in a foreign key constraint"). Dropping the
    # table takes the indexes and the constraint with it.
    if _has_table(TABLE):
        op.drop_table(TABLE)
