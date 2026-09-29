"""Idempotent backfill: give every tenant-scoped row a company_id.

The tenant filter (src/tenancy/events.py) scopes every read of a TenantScoped table to
the caller's company, so a row with a NULL company_id belongs to no tenant and is
invisible to its own company. Such rows exist because, until the filter's binding was
fixed, the insert-time stamp never ran during a request and many code paths omitted
company_id. **Run this before deploying the tenant-filter change**, then again whenever
scripts/verify_tenant_isolation.py reports NULL rows.

Each table's company comes from its parent, in the order below — parents are filled
before children, so one run cascades all the way down (interview → questions →
answers). A rule only fills NULLs from a parent that has a company; an explicit value
is never overwritten, and a mismatched value is left for verify_tenant_isolation.py to
report.

Dry run by default: every UPDATE runs inside one transaction that is rolled back, so
the counts include the cascade exactly as a real run would produce it.

Usage::

    python scripts/backfill_orphan_company_ids.py            # dry run: report only
    python scripts/backfill_orphan_company_ids.py --apply    # commit the changes
"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import inspect, text

from src.config.database_config_mysql import db_manager


# (table, [(parent_table, child_column, parent_column), ...]) — parents are tried in
# order, so the most authoritative source comes first. Order of the outer list is
# topological: a parent table always appears before its children.
RULES = [
    # Staff and candidate profiles belong to their user's company.
    ("hr_profiles",           [("users", "user_id", "id")]),
    ("interviewer_profiles",  [("users", "user_id", "id")]),
    ("candidate_profile_ext", [("users", "user_id", "id")]),

    # Jobs, then the applications and tests hanging off them.
    ("job_postings", [("departments", "department_id", "id"),
                      ("users", "created_by_id", "id"),
                      ("hr_profiles", "hr_id", "id")]),
    ("candidates",   [("job_postings", "job_id", "id"),
                      ("users", "user_id", "id")]),
    ("interviews",   [("job_postings", "job_id", "id"),
                      ("candidates", "candidate_id", "id"),
                      ("hr_profiles", "assigned_hr_id", "id")]),

    # Interview children.
    ("interview_feedback",         [("interviews", "interview_id", "id")]),
    ("interview_questions",        [("interviews", "interview_id", "id")]),
    ("question_set_assignments",   [("interviews", "interview_id", "id")]),
    ("candidate_question_history", [("interviews", "interview_id", "id")]),
    ("communication_answers",      [("interviews", "interview_id", "id")]),
    ("interview_code_submissions", [("interviews", "interview_id", "id")]),
    ("assessment_explanations",    [("interviews", "interview_id", "id")]),
    ("proctoring_sessions",        [("interviews", "test_id", "id"),
                                    ("users", "candidate_id", "id")]),
    ("proctoring_violations",      [("proctoring_sessions", "session_id", "id")]),
    ("proctoring_transcripts",     [("proctoring_sessions", "session_id", "id")]),
    ("proctoring_reviews",         [("proctoring_sessions", "session_id", "id")]),

    # Candidate children.
    ("ats_results",                     [("candidates", "candidate_id", "id")]),
    ("ats_score_events",                [("candidates", "candidate_id", "id")]),
    ("candidate_stage_history",         [("candidates", "candidate_id", "id")]),
    ("offers",                          [("candidates", "candidate_id", "id"),
                                         ("job_postings", "job_id", "id")]),
    ("screening_reviews",               [("job_postings", "job_id", "id")]),
    ("missed_candidate_investigations", [("job_postings", "job_id", "id")]),

    # Job children.
    ("job_description_versions", [("job_postings", "job_id", "id")]),
    ("job_hr_collaborators",     [("job_postings", "job_id", "id")]),
    ("job_transfer_history",     [("job_postings", "job_id", "id")]),
    ("review_samples",           [("job_postings", "job_id", "id")]),
    ("candidate_requests",       [("job_postings", "job_id", "id"),
                                  ("users", "requested_by", "id")]),

    # Profile children.
    ("candidate_work_experience", [("candidate_profile_ext", "profile_id", "id")]),
    ("candidate_education",       [("candidate_profile_ext", "profile_id", "id")]),
    ("candidate_skills",          [("candidate_profile_ext", "profile_id", "id")]),
    ("candidate_certifications",  [("candidate_profile_ext", "profile_id", "id")]),
    ("candidate_projects",        [("candidate_profile_ext", "profile_id", "id")]),

    # Everything else owned by a user or a parent record.
    ("support_requests",        [("users", "created_by", "id")]),
    ("support_messages",        [("support_requests", "request_id", "id")]),
    ("notifications",           [("users", "user_id", "id")]),
    ("mail_templates",          [("users", "created_by", "id")]),
    ("coding_problems",         [("users", "created_by", "id")]),
    ("problem_test_cases",      [("coding_problems", "problem_id", "id")]),
    ("code_submissions",        [("users", "user_id", "id")]),
    ("resume_pool",             [("users", "uploaded_by_id", "id")]),
    ("resume_pool_attachments", [("resume_pool", "entry_id", "id")]),
    ("job_deletion_archives",   [("users", "deleted_by_id", "id")]),
]

# Tenant-scoped tables with no parent to derive a company from. NULL rows here are
# reported for an operator to review; jd_templates NULLs are the intentional built-ins.
REPORT_ONLY = ["users", "departments", "jd_templates", "email_log"]


def _columns(insp, table):
    return {c["name"] for c in insp.get_columns(table)}


def _nulls(conn, table):
    return conn.execute(text(f"SELECT COUNT(*) FROM `{table}` WHERE company_id IS NULL")).scalar()


def run(apply: bool) -> int:
    """Run every rule; commit if `apply`, else roll back. Returns rows still NULL."""
    insp = inspect(db_manager.engine)
    tables = set(insp.get_table_names())
    remaining = 0

    with db_manager.engine.connect() as conn:
        trans = conn.begin()
        try:
            print(f"{'table':34s} {'null_before':>11s} {'filled':>7s} {'null_after':>10s}")
            for table, parents in RULES:
                if table not in tables or "company_id" not in _columns(insp, table):
                    print(f"{table:34s}  (skipped: table or company_id column missing)")
                    continue
                before = _nulls(conn, table)
                filled = 0
                child_cols = _columns(insp, table)
                for parent, child_col, parent_col in parents:
                    if parent not in tables or child_col not in child_cols:
                        continue
                    filled += conn.execute(text(f"""
                        UPDATE `{table}` c
                        JOIN `{parent}` p ON c.`{child_col}` = p.`{parent_col}`
                        SET c.company_id = p.company_id
                        WHERE c.company_id IS NULL AND p.company_id IS NOT NULL
                    """)).rowcount
                after = _nulls(conn, table)
                remaining += after
                print(f"{table:34s} {before:>11d} {filled:>7d} {after:>10d}")

            print()
            for table in REPORT_ONLY:
                if table in tables and "company_id" in _columns(insp, table):
                    n = _nulls(conn, table)
                    note = " (built-in templates are meant to be NULL)" if table == "jd_templates" else ""
                    print(f"{table:34s} NULL rows: {n}{note} — no parent to derive from; review by hand")
        except Exception:
            trans.rollback()
            raise
        if apply:
            trans.commit()
            print("\nApplied.")
        else:
            trans.rollback()
            print("\nDry run — nothing was changed. Re-run with --apply to commit.")
    return remaining


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true", help="commit the changes (default: dry run)")
    args = parser.parse_args()
    remaining = run(args.apply)
    if remaining:
        print(f"\n{remaining} row(s) still have no company: their parent has none either. "
              "They are invisible to every tenant until an operator assigns them.")


if __name__ == "__main__":
    main()
