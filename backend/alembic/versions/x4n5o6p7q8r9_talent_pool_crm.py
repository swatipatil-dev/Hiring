"""Sourcing CRM fields for the HR Talent Pool

The talent pool became a place HR works in, not just a ranked list to read. That needs
state the ATS does not hold:

  stage           HR's own triage lane — new | ongoing | archived — moved by hand.
                  Deliberately NOT the derived `outcome` (hired / rejected / in
                  process), which is factual ATS history. Someone rejected for one
                  role can still sit in `ongoing` here because they are worth keeping
                  warm, and that distinction is the whole point of a talent pool.
  notes           free-text notes and interview feedback
  last_contact_at when HR last spoke to them, for the "days since contact" metric
  source_channel  where they came from — LinkedIn, referral, job board. Distinct from
                  `source`, which records how the row entered the system.

Plus `resume_pool_attachments`, so a profile can carry several documents (CV,
portfolio, a photographed resume) while `resume_pool.resume_path` stays the single
scored resume.

Idempotent per object.

Revision ID: x4n5o6p7q8r9
Revises: w3m4n5o6p7q8
Create Date: 2026-07-31 00:00:00.000000
"""
from typing import Optional, Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'x4n5o6p7q8r9'
down_revision: Union[str, None] = 'w3m4n5o6p7q8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

POOL = "resume_pool"
ATTACH = "resume_pool_attachments"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _inspector().get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    return index in {i["name"] for i in _inspector().get_indexes(table)}


def _referenced_collation() -> Optional[str]:
    """Collation of `companies.id`, which the new table's FKs must match.

    Returns None on anything unexpected (a non-MySQL backend, a missing table) so the
    table is created with the database default — the previous behaviour.
    """
    try:
        return op.get_bind().execute(sa.text(
            "SELECT COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'companies' "
            "AND COLUMN_NAME = 'id'"
        )).scalar()
    except Exception:
        return None


def _drop_fk_on(table: str, column: str) -> None:
    """MySQL refuses to drop a column while a foreign key still covers it, and these
    constraint names are server-generated, so look them up rather than guess."""
    for fk in _inspector().get_foreign_keys(table):
        if fk.get("name") and fk.get("constrained_columns") == [column]:
            op.drop_constraint(fk["name"], table, type_="foreignkey")


def upgrade() -> None:
    if not _has_table(POOL):
        return

    if not _has_column(POOL, "stage"):
        op.add_column(POOL, sa.Column("stage", sa.String(20), nullable=True,
                                      server_default="new"))
    if not _has_column(POOL, "notes"):
        op.add_column(POOL, sa.Column("notes", sa.Text, nullable=True))
    if not _has_column(POOL, "last_contact_at"):
        op.add_column(POOL, sa.Column("last_contact_at", sa.DateTime, nullable=True))
    if not _has_column(POOL, "source_channel"):
        op.add_column(POOL, sa.Column("source_channel", sa.String(100), nullable=True))

    # Everyone already in the pool starts in the New lane.
    op.execute(f"UPDATE {POOL} SET stage = 'new' WHERE stage IS NULL")

    # Rows HR already promoted to the ATS are demonstrably being worked, so they belong
    # in Ongoing rather than sitting in New for someone to re-triage.
    op.execute(f"UPDATE {POOL} SET stage = 'ongoing' WHERE status = 'moved'")

    for name, cols in (
        ("ix_resume_pool_stage", ["stage"]),
        ("ix_resume_pool_last_contact_at", ["last_contact_at"]),
        ("ix_resume_pool_source_channel", ["source_channel"]),
    ):
        if not _has_index(POOL, name):
            op.create_index(name, POOL, cols)

    if not _has_table(ATTACH):
        # Create with the SAME collation as the columns we point foreign keys at.
        # A new table otherwise inherits the DATABASE default, and if that differs
        # from the existing tables' collation MySQL rejects every FK with
        # "Referencing column ... and referenced column ... are incompatible" (3780).
        # That is environment-dependent — it passes wherever the database default
        # happens to match and fails everywhere else — so it must not be left to luck.
        table_kwargs = {}
        collation = _referenced_collation()
        if collation:
            table_kwargs = {"mysql_charset": collation.split("_")[0],
                            "mysql_collate": collation}

        op.create_table(
            ATTACH,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("entry_id", sa.String(36), sa.ForeignKey("resume_pool.id"), nullable=False),
            sa.Column("filename", sa.String(500), nullable=True),
            sa.Column("file_path", sa.String(500), nullable=True),
            sa.Column("content_type", sa.String(100), nullable=True),
            sa.Column("size_bytes", sa.Integer, nullable=True),
            sa.Column("is_scoreable", sa.Boolean, nullable=True, server_default=sa.text("1")),
            sa.Column("uploaded_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("uploaded_at", sa.DateTime, nullable=True),
            **table_kwargs,
        )
        op.create_index("ix_resume_pool_attachments_company_id", ATTACH, ["company_id"])
        op.create_index("ix_resume_pool_attachments_entry_id", ATTACH, ["entry_id"])


def downgrade() -> None:
    if _has_table(ATTACH):
        op.drop_table(ATTACH)

    if not _has_table(POOL):
        return

    for name in ("ix_resume_pool_stage", "ix_resume_pool_last_contact_at",
                 "ix_resume_pool_source_channel"):
        if _has_index(POOL, name):
            op.drop_index(name, table_name=POOL)

    for column in ("source_channel", "last_contact_at", "notes", "stage"):
        if _has_column(POOL, column):
            _drop_fk_on(POOL, column)
            op.drop_column(POOL, column)
