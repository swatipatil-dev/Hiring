"""One-time cleanup of orphan DRAFT interviews left by the old wizard duplicate-draft bug.

Safe by design — only deletes a DRAFT interview when it has NO content and NO candidate:
  * status == 'DRAFT'
  * candidate_id IS NULL
  * no rows in interview_questions
  * no rows in question_set_assignments
For jobs with several drafts, the most-recent draft is ALWAYS kept (even if empty), so an
in-progress draft is never removed.

Usage (run from backend/):
    python scripts/cleanup_draft_interviews.py            # dry-run (no changes)
    python scripts/cleanup_draft_interviews.py --apply    # actually delete

Run it when no interviewer is mid-creation. After the get-or-create fix, no new orphan
drafts are produced, so this is a one-shot maintenance task.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text, bindparam
from src.config.database_config_mysql import db_manager

APPLY = "--apply" in sys.argv


def main():
    with db_manager.engine.begin() as c:
        drafts = c.execute(text("""
            SELECT i.id, i.job_id, i.title, i.created_by, i.created_at,
                   (SELECT COUNT(*) FROM interview_questions q WHERE q.interview_id = i.id) AS n_q,
                   (SELECT COUNT(*) FROM question_set_assignments s WHERE s.interview_id = i.id) AS n_s
            FROM interviews i
            WHERE i.status = 'DRAFT' AND i.candidate_id IS NULL
            ORDER BY i.job_id, i.created_at DESC, i.id DESC
        """)).fetchall()

        empty = lambda r: r.n_q == 0 and r.n_s == 0
        # Per job, keep the newest draft (first per job due to ORDER BY DESC).
        newest_per_job = {}
        for r in drafts:
            if r.job_id is not None and r.job_id not in newest_per_job:
                newest_per_job[r.job_id] = r.id

        to_delete, kept = [], []
        for r in drafts:
            keep_as_newest = r.job_id is not None and newest_per_job.get(r.job_id) == r.id
            if empty(r) and not keep_as_newest:
                to_delete.append(r)
            else:
                kept.append(r)

        print(f"DRAFT interviews (no candidate): {len(drafts)} | keep: {len(kept)} | delete: {len(to_delete)}")
        print(f"{'ID':>6}  {'job_id':<38} {'Q':>3} {'S':>3}  title")
        print("-" * 80)
        for r in to_delete:
            print(f"{r.id:>6}  {str(r.job_id):<38} {r.n_q:>3} {r.n_s:>3}  {(r.title or '')[:28]}  <- DELETE (empty)")
        if not to_delete:
            print("  (nothing to delete)")

        print(f"\nKept (has content, or newest-per-job): {len(kept)}")
        for r in kept:
            tag = "newest-per-job" if (r.job_id and newest_per_job.get(r.job_id) == r.id) else "has content"
            print(f"  keep {r.id} (Q={r.n_q} S={r.n_s}) — {tag}")

        if not APPLY:
            print(f"\nDRY-RUN — no changes. Re-run with --apply to delete {len(to_delete)} orphan draft(s).")
            return

        ids = [r.id for r in to_delete]
        if ids:
            c.execute(
                text("DELETE FROM interviews WHERE id IN :ids").bindparams(bindparam("ids", expanding=True)),
                {"ids": ids},
            )
        print(f"\nAPPLIED — deleted {len(ids)} orphan draft interview(s).")


if __name__ == "__main__":
    main()
