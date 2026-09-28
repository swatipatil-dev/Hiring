"""proctoring_review

Human review of proctoring, and fairer handling of candidates:

- `proctoring_reviews`: a reviewer's Confirmed / Dismissed / Inconclusive outcome, with
  remarks, per flagged event or per session. Append-only audit trail.
- `assessment_explanations`: the candidate's own explanation of a test.
- `interviews`: retest attempts (`attempt_number`, `previous_attempt_id`) and
  accessibility accommodations (`accommodations` JSON, the candidate's
  `accommodation_request`, and its `accommodation_status`).

Idempotent: every table and column is only created if it isn't already there. All new
columns are nullable, so existing rows keep working unchanged.

Revision ID: i5y6z7a8b9c0
Revises: h4x5y6z7a8b9
Create Date: 2026-09-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'i5y6z7a8b9c0'
down_revision = 'h4x5y6z7a8b9'
branch_labels = None
depends_on = None

INTERVIEW_COLUMNS = [
    ("attempt_number", sa.Integer()),
    ("previous_attempt_id", sa.Integer()),
    ("accommodations", sa.Text()),
    ("accommodation_request", sa.Text()),
    ("accommodation_status", sa.String(20)),
]


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if not _has_table("proctoring_reviews"):
        op.create_table(
            "proctoring_reviews",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("session_id", sa.String(36), sa.ForeignKey("proctoring_sessions.id"), nullable=False),
            sa.Column("violation_id", sa.String(36), sa.ForeignKey("proctoring_violations.id"), nullable=True),
            sa.Column("outcome", sa.String(20), nullable=False),
            sa.Column("remarks", sa.Text(), nullable=True),
            sa.Column("reviewer_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            # Microsecond precision: the latest review wins, so ties must not happen.
            sa.Column("created_at", sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql"),
                      nullable=True),
        )
        op.create_index("ix_proctoring_reviews_company_id", "proctoring_reviews", ["company_id"])
        op.create_index("ix_proctoring_reviews_session_id", "proctoring_reviews", ["session_id"])
        op.create_index("ix_proctoring_reviews_violation_id", "proctoring_reviews", ["violation_id"])

    if not _has_table("assessment_explanations"):
        op.create_table(
            "assessment_explanations",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("interview_id", sa.Integer(), sa.ForeignKey("interviews.id"), nullable=False),
            sa.Column("candidate_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("text", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
        )
        op.create_index("ix_assessment_explanations_company_id", "assessment_explanations", ["company_id"])
        op.create_index("ix_assessment_explanations_interview_id", "assessment_explanations", ["interview_id"])

    for name, type_ in INTERVIEW_COLUMNS:
        if not _has_column("interviews", name):
            op.add_column("interviews", sa.Column(name, type_, nullable=True))
    # FK added separately so a re-run after a partial upgrade doesn't try to add it twice.
    fks = {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys("interviews")}
    if "fk_interviews_previous_attempt_id" not in fks:
        op.create_foreign_key(
            "fk_interviews_previous_attempt_id", "interviews", "interviews",
            ["previous_attempt_id"], ["id"],
        )


def downgrade() -> None:
    fks = {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys("interviews")}
    if "fk_interviews_previous_attempt_id" in fks:
        op.drop_constraint("fk_interviews_previous_attempt_id", "interviews", type_="foreignkey")
    for name, _ in reversed(INTERVIEW_COLUMNS):
        if _has_column("interviews", name):
            op.drop_column("interviews", name)
    if _has_table("assessment_explanations"):
        op.drop_table("assessment_explanations")
    if _has_table("proctoring_reviews"):
        op.drop_table("proctoring_reviews")
