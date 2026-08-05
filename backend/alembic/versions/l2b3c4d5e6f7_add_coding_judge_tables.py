"""Add coding-judge tables

Creates the schema for the online coding-assessment / judge feature:
  * coding_problems       — authored algorithmic problems + resource limits
  * problem_test_cases    — input/expected-output pairs (public + hidden)
  * code_submissions      — a candidate's submission, judged asynchronously
  * submission_results    — per-test-case outcome of a submission

This migration is **idempotent**: each table is created only if it does not
already exist. The app bootstraps schema via ``Base.metadata.create_all()`` on
startup, so on an already-running dev DB these tables may already be present;
guarding with the inspector lets ``alembic upgrade head`` succeed either way and
keeps a fresh-DB ``upgrade`` path correct.

Revision ID: l2b3c4d5e6f7
Revises: k1a2b3c4d5e6
Create Date: 2026-05-27 00:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'l2b3c4d5e6f7'
down_revision: Union[str, None] = 'k1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if not _has_table("coding_problems"):
        op.create_table(
            "coding_problems",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("company_id", sa.String(length=36), nullable=True),
            sa.Column("title", sa.String(length=255), nullable=False),
            sa.Column("slug", sa.String(length=280), nullable=True),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("difficulty", sa.String(length=10), nullable=True),
            sa.Column("tags", sa.JSON(), nullable=True),
            sa.Column("time_limit_ms", sa.Integer(), nullable=True),
            sa.Column("memory_limit_mb", sa.Integer(), nullable=True),
            sa.Column("allowed_languages", sa.JSON(), nullable=True),
            sa.Column("starter_code", sa.JSON(), nullable=True),
            sa.Column("reference_solution", sa.Text(), nullable=True),
            sa.Column("interview_id", sa.Integer(), nullable=True),
            sa.Column("job_id", sa.String(length=36), nullable=True),
            sa.Column("created_by", sa.String(length=36), nullable=True),
            sa.Column("status", sa.String(length=12), nullable=True),
            sa.Column("is_public", sa.Boolean(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_coding_problems_company_id", "coding_problems", ["company_id"])
        op.create_index("ix_coding_problems_slug", "coding_problems", ["slug"])
        op.create_index("ix_coding_problems_interview_id", "coding_problems", ["interview_id"])
        op.create_index("ix_coding_problems_job_id", "coding_problems", ["job_id"])

    if not _has_table("problem_test_cases"):
        op.create_table(
            "problem_test_cases",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("company_id", sa.String(length=36), nullable=True),
            sa.Column("problem_id", sa.String(length=36), nullable=False),
            sa.Column("input", sa.Text(), nullable=True),
            sa.Column("expected_output", sa.Text(), nullable=True),
            sa.Column("is_hidden", sa.Boolean(), nullable=True),
            sa.Column("is_sample", sa.Boolean(), nullable=True),
            sa.Column("weight", sa.Integer(), nullable=True),
            sa.Column("ordinal", sa.Integer(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["problem_id"], ["coding_problems.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_problem_test_cases_company_id", "problem_test_cases", ["company_id"])
        op.create_index("ix_problem_test_cases_problem_id", "problem_test_cases", ["problem_id"])

    if not _has_table("code_submissions"):
        op.create_table(
            "code_submissions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("company_id", sa.String(length=36), nullable=True),
            sa.Column("problem_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("contest_id", sa.String(length=36), nullable=True),
            sa.Column("language", sa.String(length=20), nullable=False),
            sa.Column("source_code", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=True),
            sa.Column("verdict", sa.String(length=8), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("max_score", sa.Float(), nullable=True),
            sa.Column("total_time_ms", sa.Integer(), nullable=True),
            sa.Column("max_memory_kb", sa.Integer(), nullable=True),
            sa.Column("passed_count", sa.Integer(), nullable=True),
            sa.Column("total_count", sa.Integer(), nullable=True),
            sa.Column("compile_output", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("proctoring_session_id", sa.String(length=36), nullable=True),
            sa.Column("submitted_at", sa.DateTime(), nullable=True),
            sa.Column("judged_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["problem_id"], ["coding_problems.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_code_submissions_company_id", "code_submissions", ["company_id"])
        op.create_index("ix_code_submissions_problem_id", "code_submissions", ["problem_id"])
        op.create_index("ix_code_submissions_user_id", "code_submissions", ["user_id"])
        op.create_index("ix_code_submissions_contest_id", "code_submissions", ["contest_id"])
        op.create_index("ix_code_submissions_proctoring_session_id", "code_submissions", ["proctoring_session_id"])
        op.create_index("ix_code_submissions_submitted_at", "code_submissions", ["submitted_at"])
        op.create_index("ix_code_submissions_problem_user", "code_submissions", ["problem_id", "user_id"])

    if not _has_table("submission_results"):
        op.create_table(
            "submission_results",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("submission_id", sa.String(length=36), nullable=False),
            sa.Column("test_case_id", sa.String(length=36), nullable=True),
            sa.Column("verdict", sa.String(length=8), nullable=True),
            sa.Column("time_ms", sa.Integer(), nullable=True),
            sa.Column("memory_kb", sa.Integer(), nullable=True),
            sa.Column("exit_code", sa.Integer(), nullable=True),
            sa.Column("stdout", sa.Text(), nullable=True),
            sa.Column("stderr", sa.Text(), nullable=True),
            sa.Column("ordinal", sa.Integer(), nullable=True),
            sa.Column("is_hidden", sa.Boolean(), nullable=True),
            sa.ForeignKeyConstraint(["submission_id"], ["code_submissions.id"]),
            sa.ForeignKeyConstraint(["test_case_id"], ["problem_test_cases.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_submission_results_submission_id", "submission_results", ["submission_id"])


def downgrade() -> None:
    for tbl in ("submission_results", "code_submissions", "problem_test_cases", "coding_problems"):
        if _has_table(tbl):
            op.drop_table(tbl)
