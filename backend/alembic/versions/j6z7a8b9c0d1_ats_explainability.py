"""ats_explainability

Resume scoring that can be explained, overridden, traced and checked:

- job_postings: `preferred_skills` (separate from the mandatory `required_skills`) and
  `current_jd_version_id`.
- job_description_versions: a snapshot of the job each time its scoring-relevant parts
  change, so every score can be traced to the description it was measured against.
- ats_results: parse status/confidence/issues, scoring source, evidence coverage,
  mandatory met/total, the explanation (with resume excerpts), provenance (rules,
  JD version, model versions, input hash, scored_at) and an HR override.
- ats_score_events: append-only history of scores and overrides (with reasons).
- review_samples, screening_reviews, missed_candidate_investigations: independent
  review of rankings, including applicants below the shortlist cutoff.
- resume_pool: `parse_status`, and `match_results` widened to MEDIUMTEXT so
  explanations fit (TEXT is 64 KB). The ALTER rebuilds the table: run off-hours.

Idempotent, and every new column is nullable, so existing rows keep working: a NULL
means "scored before this was recorded".

Revision ID: j6z7a8b9c0d1
Revises: i5y6z7a8b9c0
Create Date: 2026-09-28 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision: str = 'j6z7a8b9c0d1'
down_revision = 'i5y6z7a8b9c0'
branch_labels = None
depends_on = None

MEDIUMTEXT = sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql")
DATETIME6 = sa.DateTime().with_variant(mysql.DATETIME(fsp=6), "mysql")

JOB_COLUMNS = [
    ("preferred_skills", sa.Text()),
    ("current_jd_version_id", sa.String(36)),
]
ATS_COLUMNS = [
    ("parse_status", sa.String(20)),
    ("parse_confidence", sa.Float()),
    ("parse_issues", sa.Text()),
    ("scoring_source", sa.String(20)),
    ("evidence_coverage", sa.Float()),
    ("mandatory_met", sa.Integer()),
    ("mandatory_total", sa.Integer()),
    ("explanation", MEDIUMTEXT),
    ("scoring_rules_version", sa.String(32)),
    ("jd_version_id", sa.String(36)),
    ("model_version", sa.String(255)),
    ("input_hash", sa.String(64)),
    ("scored_at", sa.DateTime()),
    ("override_score", sa.Float()),
    ("override_event_id", sa.String(36)),
]


def _insp():
    return sa.inspect(op.get_bind())


def _has_table(name):
    return _insp().has_table(name)


def _has_column(table, column):
    return column in {c["name"] for c in _insp().get_columns(table)}


def _has_index(table, name):
    return name in {i["name"] for i in _insp().get_indexes(table)}


def _tenant_cols():
    return [
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True, index=True),
    ]


def upgrade() -> None:
    for name, type_ in JOB_COLUMNS:
        if not _has_column("job_postings", name):
            op.add_column("job_postings", sa.Column(name, type_, nullable=True))
    if not _has_index("job_postings", "ix_job_postings_current_jd_version_id"):
        op.create_index("ix_job_postings_current_jd_version_id", "job_postings", ["current_jd_version_id"])

    if not _has_table("job_description_versions"):
        op.create_table(
            "job_description_versions", *_tenant_cols(),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
            sa.Column("version_no", sa.Integer(), nullable=False),
            sa.Column("title", sa.String(255)),
            sa.Column("description", sa.Text()),
            sa.Column("requirements", sa.Text()),
            sa.Column("required_skills", sa.Text()),
            sa.Column("preferred_skills", sa.Text()),
            sa.Column("experience_years", sa.Integer()),
            sa.Column("fingerprint", sa.String(64), nullable=False),
            sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime()),
            sa.UniqueConstraint("job_id", "version_no", name="uq_jd_version_job_no"),
        )

    for name, type_ in ATS_COLUMNS:
        if not _has_column("ats_results", name):
            op.add_column("ats_results", sa.Column(name, type_, nullable=True))
    if not _has_index("ats_results", "ix_ats_results_jd_version_id"):
        op.create_index("ix_ats_results_jd_version_id", "ats_results", ["jd_version_id"])
    if not _has_index("ats_results", "ix_ats_results_job_parse_status"):
        op.create_index("ix_ats_results_job_parse_status", "ats_results", ["job_id", "parse_status"])

    if not _has_table("ats_score_events"):
        op.create_table(
            "ats_score_events", *_tenant_cols(),
            sa.Column("ats_result_id", sa.String(36), nullable=True, index=True),
            sa.Column("candidate_id", sa.String(36), nullable=False, index=True),
            sa.Column("job_id", sa.String(36), nullable=False, index=True),
            sa.Column("event_type", sa.String(20), nullable=False),
            sa.Column("model_score", sa.Float()),
            sa.Column("previous_effective_score", sa.Float()),
            sa.Column("new_effective_score", sa.Float()),
            sa.Column("reason", sa.Text()),
            sa.Column("actor_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("scoring_rules_version", sa.String(32)),
            sa.Column("jd_version_id", sa.String(36)),
            sa.Column("created_at", DATETIME6),
        )

    if not _has_table("review_samples"):
        op.create_table(
            "review_samples", *_tenant_cols(),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
            sa.Column("created_by_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("cutoff_type", sa.String(20), nullable=False),
            sa.Column("cutoff_value", sa.Float(), nullable=False),
            sa.Column("size", sa.Integer(), nullable=False),
            sa.Column("seed", sa.Integer(), nullable=False),
            sa.Column("candidate_ids", sa.Text(), nullable=False),
            sa.Column("below_cutoff_population", sa.Integer(), nullable=False),
            sa.Column("created_at", sa.DateTime()),
        )

    if not _has_table("screening_reviews"):
        op.create_table(
            "screening_reviews", *_tenant_cols(),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
            sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id"), nullable=False, index=True),
            sa.Column("reviewer_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("judgement", sa.String(20), nullable=False),
            sa.Column("reason", sa.Text(), nullable=False),
            sa.Column("blind", sa.Boolean()),
            sa.Column("sample_id", sa.String(36), nullable=True),
            sa.Column("model_score_at_review", sa.Float()),
            sa.Column("effective_score_at_review", sa.Float()),
            sa.Column("rank_at_review", sa.Integer()),
            sa.Column("parse_status_at_review", sa.String(20)),
            sa.Column("scoring_rules_version", sa.String(32)),
            sa.Column("jd_version_id", sa.String(36)),
            sa.Column("created_at", DATETIME6),
        )

    if not _has_table("missed_candidate_investigations"):
        op.create_table(
            "missed_candidate_investigations", *_tenant_cols(),
            sa.Column("job_id", sa.String(36), sa.ForeignKey("job_postings.id"), nullable=False, index=True),
            sa.Column("candidate_id", sa.String(36), sa.ForeignKey("candidates.id"), nullable=False, index=True),
            sa.Column("review_id", sa.String(36), nullable=True),
            sa.Column("cause", sa.String(40), nullable=False),
            sa.Column("notes", sa.Text()),
            sa.Column("investigator_user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            sa.Column("created_at", sa.DateTime()),
        )

    if not _has_column("resume_pool", "parse_status"):
        op.add_column("resume_pool", sa.Column("parse_status", sa.String(20), nullable=True))
    if op.get_bind().dialect.name == "mysql":
        op.alter_column("resume_pool", "match_results",
                        existing_type=sa.Text(), type_=mysql.MEDIUMTEXT(), existing_nullable=True)


def downgrade() -> None:
    if op.get_bind().dialect.name == "mysql":
        # Rows over 64 KB would be truncated on the way back; nothing in v1 wrote them.
        op.alter_column("resume_pool", "match_results",
                        existing_type=mysql.MEDIUMTEXT(), type_=sa.Text(), existing_nullable=True)
    if _has_column("resume_pool", "parse_status"):
        op.drop_column("resume_pool", "parse_status")
    for t in ("missed_candidate_investigations", "screening_reviews", "review_samples",
              "ats_score_events", "job_description_versions"):
        if _has_table(t):
            op.drop_table(t)
    # MySQL uses (job_id, parse_status) to back the job_id foreign key when it is the only
    # index starting with job_id, and refuses to drop it. Give the FK its own index first.
    if _has_index("ats_results", "ix_ats_results_job_parse_status") and not any(
        i["column_names"][:1] == ["job_id"] and i["name"] != "ix_ats_results_job_parse_status"
        for i in _insp().get_indexes("ats_results")
    ):
        op.create_index("ix_ats_results_job_id", "ats_results", ["job_id"])
    for name in ("ix_ats_results_job_parse_status", "ix_ats_results_jd_version_id"):
        if _has_index("ats_results", name):
            op.drop_index(name, table_name="ats_results")
    for name, _ in reversed(ATS_COLUMNS):
        if _has_column("ats_results", name):
            op.drop_column("ats_results", name)
    if _has_index("job_postings", "ix_job_postings_current_jd_version_id"):
        op.drop_index("ix_job_postings_current_jd_version_id", table_name="job_postings")
    for name, _ in reversed(JOB_COLUMNS):
        if _has_column("job_postings", name):
            op.drop_column("job_postings", name)
