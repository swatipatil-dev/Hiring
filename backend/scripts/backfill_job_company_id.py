"""One-shot migration: populate JobPosting.company_id from the owning HR (or creator) user.

Background:
    Multi-tenant scoping was added after the first batch of jobs was created, so legacy
    rows have company_id IS NULL. The HR's job-update / job-approve endpoints filter by
    company_id == current_user.company_id, so an HR user with a non-NULL company_id
    cannot edit those legacy jobs (404 "Job not found"). This script backfills the
    missing company_id from each job's HR profile owner, falling back to created_by.

Usage:
    python scripts/backfill_job_company_id.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.database_config_mysql import db_manager
from sqlalchemy import text


SQL_BACKFILL = """
UPDATE job_postings j
LEFT JOIN hr_profiles h ON j.hr_id = h.id
LEFT JOIN users uh ON h.user_id = uh.id
LEFT JOIN users uc ON j.created_by_id = uc.id
SET j.company_id = COALESCE(uh.company_id, uc.company_id)
WHERE j.company_id IS NULL
  AND COALESCE(uh.company_id, uc.company_id) IS NOT NULL
"""


def main() -> None:
    with db_manager.engine.begin() as conn:
        before_null = conn.execute(text(
            "SELECT COUNT(*) FROM job_postings WHERE company_id IS NULL"
        )).scalar()
        print(f"Jobs missing company_id before: {before_null}")

        result = conn.execute(text(SQL_BACKFILL))
        print(f"Updated {result.rowcount} job(s)")

        after_null = conn.execute(text(
            "SELECT COUNT(*) FROM job_postings WHERE company_id IS NULL"
        )).scalar()
        print(f"Jobs still missing company_id: {after_null}")

        if after_null:
            orphans = conn.execute(text("""
                SELECT id, title, hr_id, created_by_id
                FROM job_postings
                WHERE company_id IS NULL
                LIMIT 20
            """)).fetchall()
            print("\nRemaining orphans (owners also lack company_id — review manually):")
            for row in orphans:
                print(f"  {row.id}  title={row.title!r}  hr_id={row.hr_id}  created_by={row.created_by_id}")


if __name__ == "__main__":
    main()
