"""Idempotent backfill: import registered portal candidates into the resume pool.

The HR Resume Matcher shows `resume_pool` rows. Until now those only ever came from
HR uploading a file, so everyone who registered on the candidate portal was invisible
there. ``POST /api/v1/hr/resume-pool/import-registered`` does this from the UI in
batches; this script is the ops path for a tenant with more registered users than a
single HTTP request can score before timing out.

Safe to re-run: people already in the pool are skipped (matched on
``resume_pool.source_user_id``), so a second run imports only newcomers.

Usage::

    python scripts/backfill_resume_pool_from_registered.py
    python scripts/backfill_resume_pool_from_registered.py --company-id <uuid>
    python scripts/backfill_resume_pool_from_registered.py --no-score

``--no-score`` writes the rows without running ATS scoring. That turns minutes of
sentence-transformer work into seconds; HR can then rank them with Re-match, or you
can re-run this script without the flag once out of hours.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.database_config_mysql import db_manager
from src.models.models import Company, User
from src.services.resume_pool_import import IMPORT_BATCH_MAX, import_registered_users


def backfill_company(db, company, score: bool) -> dict:
    """Import every registered user for one company, a batch at a time."""
    totals = {"imported": 0, "refreshed": 0, "skipped": 0, "failed": 0,
              "scored": 0, "scoreless": 0}

    # Prepare the active jobs once for the whole company rather than per batch.
    from routes.hr_routes import _prepare_active_jobs
    prepared_jobs = _prepare_active_jobs(db, company.id)

    # Any HR/admin in the company is a reasonable "who imported this" attribution;
    # uploaded_by_id is only ever displayed, never used for access control.
    actor = db.query(User).filter(
        User.company_id == company.id,
        User.role.in_(["hr", "admin", "superadmin"]),
    ).first()

    while True:
        counts = import_registered_users(
            db,
            company_id=company.id,
            actor_user_id=actor.id if actor else None,
            limit=IMPORT_BATCH_MAX,
            prepared_jobs=prepared_jobs,
            score=score,
        )
        for key in totals:
            totals[key] += counts[key]

        handled = counts["imported"] + counts["refreshed"] + counts["skipped"] + counts["failed"]
        if counts["remaining"] <= 0 or handled == 0:
            break

    totals["active_job_count"] = len(prepared_jobs)
    return totals


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--company-id", help="Only backfill this company")
    parser.add_argument("--no-score", action="store_true",
                        help="Create rows without ATS scoring (much faster)")
    args = parser.parse_args()

    db = db_manager.get_session()
    try:
        companies = db.query(Company)
        if args.company_id:
            companies = companies.filter(Company.id == args.company_id)
        companies = companies.all()

        if not companies:
            print("No companies found — nothing to do.")
            return

        for company in companies:
            totals = backfill_company(db, company, score=not args.no_score)
            print(
                f"{company.name}: imported {totals['imported']}, "
                f"refreshed {totals['refreshed']}, skipped {totals['skipped']}, "
                f"failed {totals['failed']} "
                f"({totals['scored']} ranked, {totals['scoreless']} with nothing to score; "
                f"{totals['active_job_count']} open roles)"
            )
    finally:
        db.close()


if __name__ == "__main__":
    main()
