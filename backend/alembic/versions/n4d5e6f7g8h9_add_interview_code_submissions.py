"""Add interview_code_submissions table

Persists each per-question coding 'evaluate' attempt a candidate makes during an
online interview test (an ``interview_questions`` row, not a standalone
``coding_problems`` row). Attempts made while the judge sandbox is unavailable are
stored with ``status='pending'`` and a null verdict so nothing is lost.

Idempotent: the table is created only if it does not already exist. The app also
bootstraps schema via ``Base.metadata.create_all()`` on startup, so on a running
dev DB the table may already be present; guarding with the inspector lets
``alembic upgrade head`` succeed either way.

Revision ID: n4d5e6f7g8h9
Revises: m3c4d5e6f7g8
Create Date: 2026-05-29 12:30:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'n4d5e6f7g8h9'
down_revision: Union[str, None] = 'm3c4d5e6f7g8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(name: str) -> bool:
    bind = op.get_bind()
    return sa.inspect(bind).has_table(name)


def upgrade() -> None:
    if not _has_table("interview_code_submissions"):
        op.create_table(
            "interview_code_submissions",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("company_id", sa.String(length=36), nullable=True),
            sa.Column("interview_id", sa.Integer(), nullable=True),
            sa.Column("question_id", sa.String(length=36), nullable=False),
            sa.Column("user_id", sa.String(length=36), nullable=True),
            sa.Column("language", sa.String(length=20), nullable=False),
            sa.Column("source_code", sa.Text(), nullable=False),
            sa.Column("status", sa.String(length=12), nullable=True),
            sa.Column("verdict", sa.String(length=8), nullable=True),
            sa.Column("score", sa.Float(), nullable=True),
            sa.Column("max_score", sa.Float(), nullable=True),
            sa.Column("passed_count", sa.Integer(), nullable=True),
            sa.Column("total_count", sa.Integer(), nullable=True),
            sa.Column("message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["company_id"], ["companies.id"]),
            sa.ForeignKeyConstraint(["interview_id"], ["interviews.id"]),
            sa.ForeignKeyConstraint(["question_id"], ["interview_questions.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_interview_code_submissions_company_id", "interview_code_submissions", ["company_id"])
        op.create_index("ix_interview_code_submissions_interview_id", "interview_code_submissions", ["interview_id"])
        op.create_index("ix_interview_code_submissions_question_id", "interview_code_submissions", ["question_id"])
        op.create_index("ix_interview_code_submissions_user_id", "interview_code_submissions", ["user_id"])
        op.create_index("ix_interview_code_submissions_created_at", "interview_code_submissions", ["created_at"])
        op.create_index("ix_interview_code_submissions_q_user", "interview_code_submissions", ["question_id", "user_id"])


def downgrade() -> None:
    if _has_table("interview_code_submissions"):
        op.drop_table("interview_code_submissions")
