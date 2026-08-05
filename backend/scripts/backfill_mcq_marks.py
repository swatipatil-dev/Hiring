"""Backfill: make every MCQ worth exactly 1 mark, and repair stored test totals.

Background
----------
`interviews.total_marks` (the "/ N" denominator HR sees) is the SUM of each
question's `points`, NOT a question count. Historically a question whose `points`
was NULL/0 was scored as 10 marks (`points = q.points or 10`), so a 40-question
MCQ test could show "/49" (39x1 + 1x10). The scoring/creation code has been fixed
so an MCQ is always worth 1 mark; this script repairs data written before the fix:

  1. Normalize `interview_questions.points` -> 1 for every MCQ question
     (i.e. every question that is not `coding` and not `communication`).
  2. Recompute `interviews.total_marks` and `interviews.test_score` for COMPLETED
     interviews from their stored `answers_json`, weighting each MCQ as exactly 1
     mark. Coding and communication answers keep their existing weighting.

Safety
------
* DRY-RUN by default: writes NOTHING unless `--apply` is passed.
* Idempotent: re-running after `--apply` reports 0 changes.
* Connects to whatever DATABASE_URL / DB_* env vars point at (see
  src/config/database_config_mysql.py). To run against PRODUCTION, set those to the
  prod Cloud SQL instance (e.g. via the Cloud SQL proxy) BEFORE invoking, and check
  the printed "Database/host" line before using --apply.

Usage
-----
    python scripts/backfill_mcq_marks.py                 # dry-run (default)
    python scripts/backfill_mcq_marks.py --apply         # write + commit
    python scripts/backfill_mcq_marks.py --interview 78  # limit recompute to one interview
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager
from src.models.models import Interview
from src.models.question_models import Question


def _recompute(entries, qmap):
    """Return (total_marks, test_score) for one interview's scored answers,
    weighting each MCQ as exactly 1 mark. Mirrors submit_interview() for coding
    and communication so only the MCQ weighting changes.

    `qmap` maps question_id -> Question (used to know each answer's real type;
    answers_json alone can't distinguish a coding question scored without test
    cases from an MCQ)."""
    total = 0
    score = 0.0
    for e in entries:
        if not isinstance(e, dict):
            continue
        q = qmap.get(str(e.get("question_id")))
        qtype = ((q.question_type if q else None) or e.get("type") or "").lower()

        if qtype == "communication" or e.get("type") == "communication":
            total += int(e.get("points") or 0)
            score += float(e.get("awarded") or 0)
        elif qtype == "coding" or "max_points" in e:  # max_points marks a coding answer
            if e.get("pending_regrade"):
                continue  # dropped from the denominator until re-graded (matches submit)
            weight = e.get("max_points")
            if weight is None:
                weight = (q.points if q else None) or 10
            total += int(weight)
            if "max_points" in e:            # graded via the coding path
                score += float(e.get("points") or 0)
            elif e.get("is_correct"):        # coding w/o test cases -> scored like MCQ
                score += int(weight)
        else:  # MCQ -> exactly 1 mark
            total += 1
            if e.get("is_correct"):
                score += 1
    return total, round(score, 2)


def normalize_question_points(session, apply):
    """Set MCQ question points to 1 (leaving coding/communication untouched)."""
    where = ("LOWER(COALESCE(question_type,'')) NOT IN ('coding','communication') "
             "AND (points IS NULL OR points <> 1)")
    n = session.execute(text(f"SELECT COUNT(*) FROM interview_questions WHERE {where}")).scalar() or 0
    if apply and n:
        session.execute(text(f"UPDATE interview_questions SET points = 1 WHERE {where}"))
    return n


def recompute_interviews(session, apply, only_interview=None):
    q = session.query(Interview).filter(
        Interview.status == "COMPLETED",
        Interview.answers_json.isnot(None),
    )
    if only_interview is not None:
        q = q.filter(Interview.id == only_interview)

    changed, skipped = [], 0
    for iv in q.all():
        try:
            entries = json.loads(iv.answers_json)
        except (ValueError, TypeError):
            skipped += 1
            continue
        if not isinstance(entries, list):
            skipped += 1
            continue

        qmap = {
            str(row.id): row
            for row in session.query(Question).filter(Question.interview_id == iv.id).all()
        }
        new_total, new_score = _recompute(entries, qmap)
        new_total_stored = new_total if new_total > 0 else None

        old_total = iv.total_marks
        old_score = float(iv.test_score) if iv.test_score is not None else None
        unchanged = (
            new_total_stored == old_total
            and ((old_score is None and new_score == 0)
                 or (old_score is not None and abs(old_score - new_score) < 1e-6))
        )
        if unchanged:
            continue

        changed.append((iv.id, old_score, old_total, new_score, new_total_stored))
        if apply:
            iv.total_marks = new_total_stored
            iv.test_score = new_score
    return changed, skipped


def main():
    ap = argparse.ArgumentParser(
        description="Backfill MCQ marks to 1 each and recompute stored test totals.")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    ap.add_argument("--interview", type=int, default=None,
                    help="Limit the interview recompute to a single interview id.")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    session = db_manager.get_db_session()
    try:
        print(f"=== Backfill MCQ marks [{mode}] ===")
        try:
            info = session.execute(text("SELECT DATABASE(), @@hostname")).fetchone()
            print(f"Database: {info[0]}   host: {info[1]}")
        except Exception:
            pass

        n_points = normalize_question_points(session, args.apply)
        print(f"\n[1] MCQ questions with points != 1 -> set to 1: {n_points}")

        changed, skipped = recompute_interviews(session, args.apply, args.interview)
        print(f"\n[2] COMPLETED interviews whose score/total changes: {len(changed)} "
              f"(skipped {skipped} with unparseable answers_json)")
        for iv_id, os_, ot_, ns_, nt_ in changed[:25]:
            print(f"    interview {iv_id}: {os_}/{ot_}  ->  {ns_}/{nt_}")
        if len(changed) > 25:
            print(f"    ... and {len(changed) - 25} more")

        if args.apply:
            session.commit()
            print("\nApplied and committed.")
        else:
            session.rollback()
            print("\nDry-run only — nothing written. Re-run with --apply to commit.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
