"""De-duplicate question records in the ``interview_questions`` table.

The table accumulated identical question rows (the same generated/imported question
stored many times), which surfaced as repeated entries in the "Use Previous Questions"
picker. This script removes the redundant copies while keeping exactly one per group.

A row is NEVER deleted if a candidate has interacted with it — i.e. it is referenced by
``interview_code_submissions``, ``communication_answers``, ``candidate_question_history``,
or appears (by id) inside any interview's ``answers_json`` blob. Such rows are reported as
"kept (in use)" instead. ``question_set_assignments`` is handled by the ORM cascade on
delete, so it is not a blocker.

Two passes:
  A) bank pool  (is_pool_question = True)  grouped by (norm text, skill, type)
  B) assigned   (is_pool_question = False) grouped by (interview_id, norm text, type)

Default is a DRY RUN (report only). Pass ``--apply`` to actually delete. Idempotent: a
second run finds nothing.

Usage::
    python scripts/dedupe_questions.py            # preview
    python scripts/dedupe_questions.py --apply     # delete redundant rows
"""
import re
import sys
import json
import argparse
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config.database_config_mysql import db_manager
from src.models.models import Interview
from src.models.question_models import (
    Question,
    CandidateQuestionHistory,
    CommunicationAnswer,
)
from src.models.judge_models import InterviewCodeSubmission


def _normalize_text(s) -> str:
    """Lowercase, collapse all whitespace to single spaces, strip. Matches the
    de-dup rule used by generator_router so groupings line up with the app."""
    if not s:
        return ""
    return " ".join(str(s).split()).strip().lower()


def _build_inuse_ids(db) -> set:
    """Question ids a candidate has interacted with — never safe to delete."""
    inuse = set()
    for (qid,) in db.query(InterviewCodeSubmission.question_id).distinct():
        if qid:
            inuse.add(qid)
    for (qid,) in db.query(CommunicationAnswer.question_id).distinct():
        if qid:
            inuse.add(qid)
    for (qid,) in db.query(CandidateQuestionHistory.source_question_id).distinct():
        if qid:
            inuse.add(qid)
    # answers_json: JSON list of objects keyed by question_id (MCQ / communication)
    for (blob,) in db.query(Interview.answers_json).filter(Interview.answers_json.isnot(None)):
        try:
            for a in json.loads(blob) or []:
                qid = a.get("question_id") if isinstance(a, dict) else None
                if qid:
                    inuse.add(qid)
        except (ValueError, TypeError):
            continue
    return inuse


def _keep_rank(q, inuse: set):
    """Sort key for choosing the keeper; the MAX wins. Prefer in-use (so we retain
    what a candidate touched), then approved, then richer content, then newest."""
    has_content = 1 if (q.test_cases or q.options) else 0
    created = q.created_at.timestamp() if q.created_at else 0
    return (
        1 if q.id in inuse else 0,
        1 if q.is_approved else 0,
        has_content,
        created,
    )


def _dedupe_pass(db, rows, key_fn, inuse, label):
    """Group rows by key_fn; in each group keep one and delete the other DELETABLE
    rows. Returns (groups_with_dupes, deleted_count, kept_in_use_count)."""
    groups = defaultdict(list)
    for q in rows:
        groups[key_fn(q)].append(q)

    groups_with_dupes = deleted = kept_in_use = 0
    for key, members in groups.items():
        if len(members) < 2:
            continue
        groups_with_dupes += 1
        keeper = max(members, key=lambda q: _keep_rank(q, inuse))
        for q in members:
            if q is keeper:
                continue
            if q.id in inuse:
                kept_in_use += 1
                continue
            db.delete(q)  # cascade removes question_set_assignments rows
            deleted += 1

    print(f"[{label}] groups_with_dupes={groups_with_dupes}  to_delete={deleted}  kept_in_use={kept_in_use}")
    return groups_with_dupes, deleted, kept_in_use


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry-run preview)")
    args = ap.parse_args()

    db = db_manager.get_db_session()
    try:
        total_before = db.query(Question).count()
        inuse = _build_inuse_ids(db)
        print(f"total question rows: {total_before}")
        print(f"in-use (protected) question ids: {len(inuse)}")

        all_rows = db.query(Question).all()
        bank = [q for q in all_rows if q.is_pool_question]
        assigned = [q for q in all_rows if not q.is_pool_question]

        # Pass A: bank pool (the "Use Previous Questions" picker source)
        _dedupe_pass(
            db, bank,
            lambda q: (_normalize_text(q.question_text), q.skill_name, q.question_type),
            inuse, "bank",
        )
        # Pass B: assigned rows — dedupe within each interview
        _dedupe_pass(
            db, assigned,
            lambda q: (q.interview_id, _normalize_text(q.question_text), q.question_type),
            inuse, "assigned",
        )

        if args.apply:
            db.commit()
            total_after = db.query(Question).count()
            print(f"APPLIED. rows {total_before} -> {total_after} (removed {total_before - total_after})")
        else:
            db.rollback()
            print("DRY RUN -- no changes written. Re-run with --apply to delete.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
