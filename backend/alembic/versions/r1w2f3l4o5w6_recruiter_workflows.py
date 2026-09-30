"""Recruiter workflows: sources, merges, withdrawal, stages, bulk actions, scorecards

  candidates               + source, source_detail, referrer_name, referrer_email
                           + merged_into_id (self FK), withdrawn_at, withdrawal_reason
  pipeline_stage_configs   per-company stage label / order / owner / deadline
  stage_reminder_log       one row per pending-action reminder sent
  bulk_operations          preview-then-confirm bulk actions, and their outcome
  candidate_merges         audit + undo record of every profile merge
  interview_scorecards     one independent scorecard per panellist per interview

Existing candidates keep source NULL ("not recorded") — there is no honest value to
backfill, and guessing "direct" would skew the source report.

Idempotent per object: a fresh database is built by create_all() then stamped, so
any of these may already exist when this runs.

Revision ID: r1w2f3l4o5w6
Revises: l0a1b2c3d4e5
Create Date: 2026-09-30 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from migration_helpers import referenced_collation


revision: str = 'r1w2f3l4o5w6'
down_revision: Union[str, None] = 'l0a1b2c3d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _inspector().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {i["name"] for i in _inspector().get_indexes(table)}


def _fks_on(table: str, column: str):
    """Names of the foreign keys on ``column``, whatever they are called: create_all()
    names them itself (candidates_ibfk_N), so checking for our own name would miss one
    and add a duplicate constraint."""
    return [fk["name"] for fk in _inspector().get_foreign_keys(table)
            if fk.get("constrained_columns") == [column] and fk.get("name")]


def _table_kwargs(*targets):
    kwargs = {"mysql_engine": "InnoDB"}
    collation = referenced_collation(op.get_bind(), *targets)
    if collation:
        kwargs["mysql_collate"] = collation
    return kwargs


def _index(table: str, name: str, cols) -> None:
    if not _has_index(table, name):
        op.create_index(name, table, cols)


CANDIDATE_COLUMNS = [
    ("source", lambda: sa.Column("source", sa.String(30), nullable=True)),
    ("source_detail", lambda: sa.Column("source_detail", sa.String(255), nullable=True)),
    ("referrer_name", lambda: sa.Column("referrer_name", sa.String(255), nullable=True)),
    ("referrer_email", lambda: sa.Column("referrer_email", sa.String(255), nullable=True)),
    ("merged_into_id", lambda: sa.Column("merged_into_id", sa.String(36), nullable=True)),
    ("withdrawn_at", lambda: sa.Column("withdrawn_at", sa.DateTime(), nullable=True)),
    ("withdrawal_reason", lambda: sa.Column("withdrawal_reason", sa.Text(), nullable=True)),
]


def upgrade() -> None:
    # ── candidates ──
    for name, make in CANDIDATE_COLUMNS:
        if not _has_column("candidates", name):
            op.add_column("candidates", make())
    _index("candidates", "ix_candidates_source", ["source"])
    _index("candidates", "ix_candidates_merged_into_id", ["merged_into_id"])
    if not _fks_on("candidates", "merged_into_id"):
        op.create_foreign_key("fk_candidates_merged_into_id", "candidates", "candidates",
                              ["merged_into_id"], ["id"])

    ref = (("companies", "id"), ("users", "id"), ("candidates", "id"))

    # ── pipeline_stage_configs ──
    if not _has_table("pipeline_stage_configs"):
        op.create_table(
            "pipeline_stage_configs",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("stage_key", sa.String(50), nullable=False),
            sa.Column("label", sa.String(100), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("owner_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("sla_days", sa.Integer(), nullable=True),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("company_id", "stage_key", name="uq_stage_config_company_key"),
            **_table_kwargs(*ref),
        )
    _index("pipeline_stage_configs", "ix_pipeline_stage_configs_company_id", ["company_id"])

    # ── stage_reminder_log ──
    if not _has_table("stage_reminder_log"):
        op.create_table(
            "stage_reminder_log",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id"), nullable=False),
            sa.Column("stage_key", sa.String(50), nullable=False),
            sa.Column("stage_entered_at", sa.DateTime(), nullable=True),
            sa.Column("recipient_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("due_at", sa.DateTime(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False),
            sa.Column("sent_at", sa.DateTime(), nullable=True),
            **_table_kwargs(*ref),
        )
    _index("stage_reminder_log", "ix_stage_reminder_log_company_id", ["company_id"])
    _index("stage_reminder_log", "ix_stage_reminder_log_candidate_id", ["candidate_id"])
    _index("stage_reminder_log", "ix_stage_reminder_log_recipient_user_id", ["recipient_user_id"])
    _index("stage_reminder_log", "ix_stage_reminder_log_sent_at", ["sent_at"])

    # ── bulk_operations ──
    if not _has_table("bulk_operations"):
        op.create_table(
            "bulk_operations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("action", sa.String(30), nullable=False),
            sa.Column("params", sa.Text(), nullable=True),
            sa.Column("candidate_ids", sa.Text(), nullable=False),
            sa.Column("preview", sa.Text(), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="previewed"),
            sa.Column("eligible_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("affected_count", sa.Integer(), nullable=True),
            sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("executed_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("executed_at", sa.DateTime(), nullable=True),
            **_table_kwargs(*ref),
        )
    _index("bulk_operations", "ix_bulk_operations_company_id", ["company_id"])
    _index("bulk_operations", "ix_bulk_operations_created_at", ["created_at"])

    # ── candidate_merges ──
    if not _has_table("candidate_merges"):
        op.create_table(
            "candidate_merges",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("primary_candidate_id", sa.String(36), sa.ForeignKey("candidates.id"), nullable=False),
            sa.Column("merged_candidate_id", sa.String(36), sa.ForeignKey("candidates.id"), nullable=False),
            sa.Column("merged_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("field_choices", sa.Text(), nullable=True),
            sa.Column("primary_before", sa.Text(), nullable=True),
            sa.Column("merged_before", sa.Text(), nullable=True),
            sa.Column("moved", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("undone_at", sa.DateTime(), nullable=True),
            sa.Column("undone_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            **_table_kwargs(*ref),
        )
    _index("candidate_merges", "ix_candidate_merges_company_id", ["company_id"])
    _index("candidate_merges", "ix_candidate_merges_primary_candidate_id", ["primary_candidate_id"])
    _index("candidate_merges", "ix_candidate_merges_merged_candidate_id", ["merged_candidate_id"])

    # ── interview_scorecards ──
    if not _has_table("interview_scorecards"):
        op.create_table(
            "interview_scorecards",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("interview_id", sa.Integer(), sa.ForeignKey("interviews.id"), nullable=False),
            sa.Column("panelist_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("panelist_email", sa.String(255), nullable=True),
            sa.Column("panelist_name", sa.String(255), nullable=True),
            sa.Column("technical_score", sa.DECIMAL(5, 2), nullable=True),
            sa.Column("communication_score", sa.DECIMAL(5, 2), nullable=True),
            sa.Column("problem_solving_score", sa.DECIMAL(5, 2), nullable=True),
            sa.Column("cultural_fit_score", sa.DECIMAL(5, 2), nullable=True),
            sa.Column("overall_score", sa.DECIMAL(5, 2), nullable=True),
            sa.Column("strengths", sa.Text(), nullable=True),
            sa.Column("weaknesses", sa.Text(), nullable=True),
            sa.Column("recommendations", sa.Text(), nullable=True),
            sa.Column("hire_recommendation", sa.String(50), nullable=True),
            sa.Column("notes", sa.Text(), nullable=True),
            sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.UniqueConstraint("interview_id", "panelist_user_id",
                                name="uq_scorecard_interview_panelist"),
            **_table_kwargs(("companies", "id"), ("users", "id")),
        )
    _index("interview_scorecards", "ix_interview_scorecards_company_id", ["company_id"])
    _index("interview_scorecards", "ix_interview_scorecards_interview_id", ["interview_id"])
    _index("interview_scorecards", "ix_interview_scorecards_panelist_user_id", ["panelist_user_id"])


def downgrade() -> None:
    # Tables outright (see d0t1u2v3w4x5 for why indexes are not dropped first).
    for table in ("interview_scorecards", "candidate_merges", "bulk_operations",
                  "stage_reminder_log", "pipeline_stage_configs"):
        if _has_table(table):
            op.drop_table(table)
    for name in _fks_on("candidates", "merged_into_id"):
        op.drop_constraint(name, "candidates", type_="foreignkey")
    for name in ("ix_candidates_merged_into_id", "ix_candidates_source"):
        if _has_index("candidates", name):
            op.drop_index(name, table_name="candidates")
    for name, _ in reversed(CANDIDATE_COLUMNS):
        if _has_column("candidates", name):
            op.drop_column("candidates", name)
