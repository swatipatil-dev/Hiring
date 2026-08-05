"""Tenant-isolation database health check.

Scans every tenant-scoped table in `recruitment_db` and reports:

  * **MISMATCH** — rows whose ``company_id`` disagrees with their parent's
    (e.g. an ATSResult attached to Candidate from Company A but stamped with
    Company B's id).  This should be 0 in a clean DB.

  * **NULL** — rows with ``company_id IS NULL`` while their parent has a
    company_id (data the auto-filter will hide from tenants — these rows
    can't be re-attached without operator review).

Run after deploys, after backfill scripts, or on a cron to detect drift.

Exits 0 if everything is clean, 1 if any mismatch / null-with-parent is
found, 2 on connection errors.

Usage::

    python scripts/verify_tenant_isolation.py
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config.database_config_mysql import db_manager


# Each entry: (child_table, parent_join_sql, child_alias, parent_alias)
# parent_join_sql must yield a `c` (child) and `p` (parent) alias so the
# subsequent NULL / mismatch checks compose uniformly.
CHECKS = [
    # — tables whose direct parent is `candidates`
    ("ats_results",
     "ats_results c JOIN candidates p ON c.candidate_id = p.id"),
    ("candidate_stage_history",
     "candidate_stage_history c JOIN candidates p ON c.candidate_id = p.id"),

    # — tables whose direct parent is `interviews`
    ("interview_feedback",
     "interview_feedback c JOIN interviews p ON c.interview_id = p.id"),
    ("interview_questions",
     "interview_questions c JOIN interviews p ON c.interview_id = p.id"),
    ("candidate_question_history",
     "candidate_question_history c JOIN interviews p ON c.interview_id = p.id"),
    ("question_set_assignments",
     "question_set_assignments c JOIN interviews p ON c.interview_id = p.id"),
    ("proctoring_sessions",
     "proctoring_sessions c JOIN interviews p ON c.test_id = p.id"),

    # — tables whose direct parent is `support_requests`
    ("support_messages",
     "support_messages c JOIN support_requests p ON c.request_id = p.id"),

    # — tables whose direct parent is `users`
    ("hr_profiles",
     "hr_profiles c JOIN users p ON c.user_id = p.id"),
    ("interviewer_profiles",
     "interviewer_profiles c JOIN users p ON c.user_id = p.id"),
    ("candidate_profile_ext",
     "candidate_profile_ext c JOIN users p ON c.user_id = p.id"),

    # — tables whose parent is `candidate_profile_ext`
    ("candidate_work_experience",
     "candidate_work_experience c JOIN candidate_profile_ext p ON c.profile_id = p.id"),
    ("candidate_education",
     "candidate_education c JOIN candidate_profile_ext p ON c.profile_id = p.id"),
    ("candidate_skills",
     "candidate_skills c JOIN candidate_profile_ext p ON c.profile_id = p.id"),
    ("candidate_certifications",
     "candidate_certifications c JOIN candidate_profile_ext p ON c.profile_id = p.id"),
    ("candidate_projects",
     "candidate_projects c JOIN candidate_profile_ext p ON c.profile_id = p.id"),

    # — tables whose parent is `proctoring_sessions`
    ("proctoring_violations",
     "proctoring_violations c JOIN proctoring_sessions p ON c.session_id = p.id"),
    ("proctoring_transcripts",
     "proctoring_transcripts c JOIN proctoring_sessions p ON c.session_id = p.id"),

    # — tables that point directly at `companies`
    ("users",                  None),
    ("departments",            None),
    ("job_postings",           None),
    ("candidates",             None),
    ("interviews",             None),
    ("offers",                 None),
    ("candidate_requests",     None),
    ("notifications",          None),
    ("support_requests",       None),
]


def _scalar(conn, sql: str, **params) -> int:
    return int(conn.execute(text(sql), params).scalar() or 0)


def main() -> int:
    total_mismatch = 0
    total_null_with_parent = 0
    total_orphan = 0
    rows = []

    try:
        with db_manager.engine.connect() as conn:
            for entry in CHECKS:
                if len(entry) == 2:
                    table, join = entry
                else:
                    continue

                # `total` rows in the child table
                total = _scalar(conn, f"SELECT COUNT(*) FROM {table}")

                # Rows where company_id IS NULL — for top-level tables this
                # tells you "rows that won't be visible to any tenant", for
                # child tables also report NULL-with-parent-has-id (real bugs).
                null_count = _scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE company_id IS NULL")

                mismatch = 0
                null_with_parent = 0
                if join:
                    mismatch = _scalar(
                        conn,
                        f"SELECT COUNT(*) FROM {join} "
                        f"WHERE c.company_id IS NOT NULL "
                        f"  AND p.company_id IS NOT NULL "
                        f"  AND c.company_id <> p.company_id"
                    )
                    null_with_parent = _scalar(
                        conn,
                        f"SELECT COUNT(*) FROM {join} "
                        f"WHERE c.company_id IS NULL "
                        f"  AND p.company_id IS NOT NULL"
                    )

                total_mismatch += mismatch
                total_null_with_parent += null_with_parent
                total_orphan += null_count

                rows.append((table, total, null_count, mismatch, null_with_parent))

    except Exception as exc:
        print(f"ERROR: could not connect to database: {exc}")
        return 2

    # Report
    print(f"{'table':30s}  {'rows':>6}  {'null':>6}  {'mismatch':>8}  {'null_w_parent':>14}")
    print("-" * 78)
    for table, total, null_count, mismatch, null_with_parent in rows:
        flag = ""
        if mismatch or null_with_parent:
            flag = "  <-- FIX"
        print(f"{table:30s}  {total:>6}  {null_count:>6}  {mismatch:>8}  {null_with_parent:>14}{flag}")

    print("-" * 78)
    print(f"TOTAL mismatch={total_mismatch}  null_with_parent={total_null_with_parent}  total_null={total_orphan}")

    if total_mismatch or total_null_with_parent:
        print("\nFAIL — cross-company drift detected.  Re-run the backfill scripts:")
        print("  python scripts/backfill_orphan_company_ids.py")
        print("  python scripts/backfill_profile_company_ids.py")
        print("  python scripts/backfill_full_company_ids.py")
        return 1

    print("\nPASS — every tenant-scoped row matches its parent's company_id.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
