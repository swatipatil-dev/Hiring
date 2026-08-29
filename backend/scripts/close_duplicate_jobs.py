"""Close the extra copies of a job, keeping one open per role.

Nothing stopped the same role being posted repeatedly until the guards in
`src/services/job_dedup.py` landed, so the board accumulated duplicates — one dev
database had `ui/ux designer` nine times in a single department, and 21 of its 34 job
rows sat inside a duplicate group.

This closes the extras. It does not delete or merge them, and that is the whole design:
in that same database only 3 of the 21 duplicates were unreferenced. The other 18 carried
42 interviews, 10 candidates and 10 ATS results between them. Deleting would destroy live
interview history; merging would rewrite it. Setting `status='closed'` takes them out of
the active lists, leaves every foreign key pointing where it always did, and is undone by
reopening one row.

Choosing which copy survives
----------------------------
Not simply the newest. `ui/ux designer` was eight drafts and one active, and the active
one is the likelier home of the real interviews — keeping a late draft and closing the job
people are actually interviewing for would be worse than the duplicates. So the survivor
is the row with the most attached activity, then the most advanced status, then the newest.

Usage::

    python scripts/close_duplicate_jobs.py                     # dry run, prints the plan
    python scripts/close_duplicate_jobs.py --apply
    python scripts/close_duplicate_jobs.py --company-id <uuid>

Safe to re-run: once each group holds one open row there is nothing left to match, so a
second run reports no work.
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sqlalchemy as sa

from src.config.database_config_mysql import db_manager
from src.models.models import JobPosting

# Most advanced first. A job someone is actively hiring for outranks a draft nobody
# published, whatever the timestamps say.
STATUS_RANK = {"active": 3, "pending_approval": 2, "draft": 1}


def _activity(conn, job_id: str) -> int:
    """How much real work hangs off this job.

    Counted rather than merely detected, so the busiest row wins rather than the first
    one that happens to have a single stray record.
    """
    total = 0
    for table, col in (("candidates", "job_id"), ("interviews", "job_id"),
                       ("ats_results", "job_id"), ("candidate_requests", "job_id")):
        try:
            total += conn.execute(
                sa.text(f"SELECT COUNT(*) FROM {table} WHERE {col} = :j"),
                {"j": job_id},
            ).scalar() or 0
        except Exception:
            # A table this deployment does not have is not a reason to abandon the run.
            pass
    return total


def _groups(db, company_id=None):
    """Every (company, department, title) that has more than one job still open."""
    q = db.query(JobPosting).filter(JobPosting.status != "closed")
    if company_id:
        q = q.filter(JobPosting.company_id == company_id)

    buckets = {}
    for job in q.all():
        key = (job.company_id, job.department_id, (job.title or "").strip().lower())
        buckets.setdefault(key, []).append(job)
    return {k: v for k, v in buckets.items() if len(v) > 1}


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="write the changes (default is a dry run)")
    ap.add_argument("--company-id", help="limit to one company")
    ap.add_argument("--include-active", action="store_true",
                    help="also close duplicate jobs that are ACTIVE and have candidates "
                         "or interviews attached. Off by default: several 'copies' of a "
                         "role can be genuine parallel hiring rounds, and closing one "
                         "mid-flight disrupts real recruitment.")
    args = ap.parse_args()

    db = db_manager.get_db_session()
    conn = db.connection()
    try:
        groups = _groups(db, args.company_id)
        if not groups:
            print("No duplicate open jobs. Nothing to do.")
            return 0

        total_closing = 0
        total_held = 0
        for (_company, dept, title), jobs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
            ranked = sorted(
                jobs,
                key=lambda j: (
                    _activity(conn, j.id),
                    STATUS_RANK.get(j.status, 0),
                    j.created_at or sa.sql.null(),
                ),
                reverse=True,
            )
            keep, rest = ranked[0], ranked[1:]

            # An extra copy that is still ACTIVE and carrying candidates or interviews is
            # not obviously an accident — it may be a second requisition for the same
            # role, and closing one mid-flight disrupts real hiring. Held back unless
            # asked for explicitly, and always printed, so the decision is visible rather
            # than silently made on someone's behalf.
            close, held = [], []
            for j in rest:
                risky = j.status == "active" and _activity(conn, j.id) > 0
                (held if risky and not args.include_active else close).append(j)
            total_closing += len(close)
            total_held += len(held)

            print(f"\n{title!r}  (department {str(dept)[:8] or '-'})  — {len(jobs)} copies")
            print(f"  KEEP  {keep.id[:8]}  {keep.status:16} "
                  f"activity={_activity(conn, keep.id):<4} created={keep.created_at:%Y-%m-%d}")
            for j in close:
                print(f"  close {j.id[:8]}  {j.status:16} "
                      f"activity={_activity(conn, j.id):<4} created={j.created_at:%Y-%m-%d}")
                if args.apply:
                    j.status = "closed"
            for j in held:
                print(f"  KEPT  {j.id[:8]}  {j.status:16} "
                      f"activity={_activity(conn, j.id):<4} created={j.created_at:%Y-%m-%d}"
                      f"   <-- active with live work; needs --include-active")

        held_note = ""
        if total_held:
            held_note = (f" {total_held} active job(s) with live work were left alone — "
                         f"review those by hand, or pass --include-active.")

        if args.apply:
            db.commit()
            print(f"\nClosed {total_closing} duplicate job(s). No rows were deleted and "
                  f"no references were changed.{held_note}")
        else:
            print(f"\nDry run — nothing written. {total_closing} job(s) would be closed."
                  f"{held_note}")
            print("Re-run with --apply once the plan above looks right.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
