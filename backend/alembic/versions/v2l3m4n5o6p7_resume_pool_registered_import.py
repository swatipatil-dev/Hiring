"""Add provenance columns to resume_pool so registered candidates can be imported

The Resume Matcher only ever showed resumes HR uploaded through it. Everyone who
registered on the candidate portal lived in `candidates` with no path into that view.
These columns let the pool hold both populations and tell them apart:

  source              upload | registered
  source_user_id      the registered User — the import's idempotency key. content_hash
                      cannot serve that role because someone who registered without
                      uploading anything has no text to hash.
  source_candidate_id the Candidate the row was imported FROM. Deliberately separate
                      from moved_candidate_id, which means "a Candidate this row
                      PRODUCED" and guards the stored file from deletion.
  imported_at         last import/refresh, kept distinct from uploaded_at (which for
                      an imported row is when the person registered).

Idempotent per object, so a database where create_all already added the columns
upgrades cleanly.

Revision ID: v2l3m4n5o6p7
Revises: u1k2l3m4n5o6
Create Date: 2026-07-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'v2l3m4n5o6p7'
down_revision: Union[str, None] = 'u1k2l3m4n5o6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "resume_pool"


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _inspector().get_columns(table)}


def _has_index(table: str, index: str) -> bool:
    return index in {i["name"] for i in _inspector().get_indexes(table)}


def _has_unique(table: str, name: str) -> bool:
    insp = _inspector()
    names = {c["name"] for c in insp.get_unique_constraints(table)}
    # MySQL reports unique constraints as indexes too, depending on driver version.
    names |= {i["name"] for i in insp.get_indexes(table) if i.get("unique")}
    return name in names


def _drop_fk_on(table: str, column: str) -> None:
    """Drop whichever foreign key covers `column`.

    MySQL refuses to drop a column while a foreign key still references it, and the
    constraint names here are server-generated (resume_pool_ibfk_N) because the rest
    of this table's FKs are unnamed — so look the name up rather than guessing it.
    """
    for fk in _inspector().get_foreign_keys(table):
        if fk.get("name") and fk.get("constrained_columns") == [column]:
            op.drop_constraint(fk["name"], table, type_="foreignkey")


def upgrade() -> None:
    if not _has_table(TABLE):
        # Nothing to extend — u1k2l3m4n5o6 creates the table with these columns absent
        # only on databases that ran it, and a fresh DB gets them from create_all.
        return

    if not _has_column(TABLE, "source"):
        op.add_column(TABLE, sa.Column("source", sa.String(20), nullable=True,
                                       server_default="upload"))
    if not _has_column(TABLE, "source_user_id"):
        op.add_column(TABLE, sa.Column("source_user_id", sa.String(36),
                                       sa.ForeignKey("users.id"), nullable=True))
    if not _has_column(TABLE, "source_candidate_id"):
        op.add_column(TABLE, sa.Column("source_candidate_id", sa.String(36),
                                       sa.ForeignKey("candidates.id"), nullable=True))
    if not _has_column(TABLE, "imported_at"):
        op.add_column(TABLE, sa.Column("imported_at", sa.DateTime, nullable=True))

    # Every row that already exists got there by HR uploading a file.
    op.execute(f"UPDATE {TABLE} SET source = 'upload' WHERE source IS NULL")

    if not _has_index(TABLE, "ix_resume_pool_source"):
        op.create_index("ix_resume_pool_source", TABLE, ["source"])

    # Concurrency guard: two HRs clicking Import at once both pass the NOT EXISTS
    # check, and this turns the loser into an IntegrityError the import counts as
    # skipped instead of writing a duplicate person.
    if not _has_unique(TABLE, "uq_resume_pool_company_source_user"):
        op.create_unique_constraint(
            "uq_resume_pool_company_source_user", TABLE,
            ["company_id", "source_user_id"],
        )


def downgrade() -> None:
    if not _has_table(TABLE):
        return

    if _has_unique(TABLE, "uq_resume_pool_company_source_user"):
        op.drop_constraint("uq_resume_pool_company_source_user", TABLE, type_="unique")
    if _has_index(TABLE, "ix_resume_pool_source"):
        op.drop_index("ix_resume_pool_source", table_name=TABLE)

    # The FK has to go before the column it constrains, or MySQL errors mid-downgrade
    # and leaves the table half-migrated.
    for column in ("imported_at", "source_candidate_id", "source_user_id", "source"):
        if not _has_column(TABLE, column):
            continue
        _drop_fk_on(TABLE, column)
        op.drop_column(TABLE, column)
