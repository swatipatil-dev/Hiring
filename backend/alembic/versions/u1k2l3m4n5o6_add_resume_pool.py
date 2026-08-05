"""Add resume_pool table

Stores resumes HR uploads through the Resume Matcher. Previously those uploads were
deleted right after text extraction and nothing was persisted, so a matched resume
could never be searched for or promoted later. Each row keeps the stored file key,
the extracted text, the parsed profile (name/college/year of passing/role) and the
ranked job matches, plus a link to the Candidate it was promoted into.

Idempotent — create_all does not create this table for existing databases in a way
that would conflict, so this migration is the authoritative add.

Revision ID: u1k2l3m4n5o6
Revises: t0j1k2l3m4n5
Create Date: 2026-07-28 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'u1k2l3m4n5o6'
down_revision: Union[str, None] = 't0j1k2l3m4n5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(table)


def upgrade() -> None:
    if _has_table("resume_pool"):
        return

    op.create_table(
        "resume_pool",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
        sa.Column("uploaded_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),

        sa.Column("filename", sa.String(500), nullable=True),
        sa.Column("resume_path", sa.String(500), nullable=True),
        sa.Column("resume_text", sa.Text, nullable=True),
        sa.Column("content_hash", sa.String(64), nullable=True),

        sa.Column("full_name", sa.String(255), nullable=True),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(50), nullable=True),
        sa.Column("college", sa.String(255), nullable=True),
        sa.Column("year_of_passing", sa.Integer, nullable=True),
        sa.Column("job_role", sa.String(255), nullable=True),
        sa.Column("highest_degree", sa.String(100), nullable=True),
        sa.Column("extracted_skills", sa.Text, nullable=True),
        sa.Column("experience_years", sa.DECIMAL(4, 2), nullable=True),

        sa.Column("best_job_id", sa.String(36), sa.ForeignKey("job_postings.id"), nullable=True),
        sa.Column("best_score", sa.DECIMAL(5, 2), nullable=True),
        sa.Column("match_results", sa.Text, nullable=True),

        sa.Column("status", sa.String(20), nullable=True, server_default="new"),
        sa.Column("moved_candidate_id", sa.String(36), sa.ForeignKey("candidates.id"), nullable=True),

        sa.Column("uploaded_at", sa.DateTime, nullable=True),
        sa.Column("updated_at", sa.DateTime, nullable=True),
    )

    # Indexes backing the Resume Matcher search/filter bar.
    op.create_index("ix_resume_pool_company_id", "resume_pool", ["company_id"])
    op.create_index("ix_resume_pool_content_hash", "resume_pool", ["content_hash"])
    op.create_index("ix_resume_pool_email", "resume_pool", ["email"])
    op.create_index("ix_resume_pool_college", "resume_pool", ["college"])
    op.create_index("ix_resume_pool_year_of_passing", "resume_pool", ["year_of_passing"])
    op.create_index("ix_resume_pool_job_role", "resume_pool", ["job_role"])
    op.create_index("ix_resume_pool_best_score", "resume_pool", ["best_score"])
    op.create_index("ix_resume_pool_uploaded_at", "resume_pool", ["uploaded_at"])


def downgrade() -> None:
    if _has_table("resume_pool"):
        op.drop_table("resume_pool")
