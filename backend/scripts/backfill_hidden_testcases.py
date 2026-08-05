"""Backfill HIDDEN test cases for coding questions.

Many coding questions were saved with only a single visible sample case (or none),
so a candidate could hard-code the sample and still score AC. This script finds
those questions and, using the reference solution (``sample_answer``) as ground
truth, tops each up to ``--min-hidden`` hidden cases via
``src.judge.hidden_testgen.ensure_hidden_testcases``:

  * SQL questions          → synthesize seed datasets, compute output via SQLite.
  * stdin/stdout questions → synthesize inputs via the LLM, compute output by
                             running the reference in the Docker sandbox.

Expected outputs are always COMPUTED (never trusted from the LLM), so new cases
are correct by construction. Questions whose ``sample_answer`` isn't runnable code
(open-ended prose) are skipped and reported. Idempotent — safe to re-run.

Usage::

    python scripts/backfill_hidden_testcases.py --report      # inventory only, write nothing
    python scripts/backfill_hidden_testcases.py --dry-run       # show what would change
    python scripts/backfill_hidden_testcases.py                 # apply (skip already-sufficient)
    python scripts/backfill_hidden_testcases.py --all            # also top up sufficient ones
    python scripts/backfill_hidden_testcases.py --min-hidden 3 --limit 20
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.config.database_config_mysql import db_manager
from src.judge.hidden_testgen import ensure_hidden_testcases


def _parse_cases(raw):
    try:
        cases = json.loads(raw) if raw else []
    except Exception:
        return []
    return cases if isinstance(cases, list) else []


def _hidden(cases):
    return sum(1 for c in cases if isinstance(c, dict) and not c.get("is_sample"))


def _fetch(db):
    return db.execute(text(
        "SELECT id, question_text, sample_answer, test_cases, category "
        "FROM interview_questions WHERE question_type='coding' "
        "AND sample_answer IS NOT NULL AND sample_answer <> ''"
    )).fetchall()


def _report(db):
    rows = _fetch(db)
    zero = one = ok = below = 0
    for _id, _qt, _sa, raw_tc, _cat in rows:
        cs = _parse_cases(raw_tc)
        h = _hidden(cs)
        if len(cs) == 0:
            zero += 1
        elif len(cs) == 1:
            one += 1
        else:
            ok += 1
        if h < 3:
            below += 1
    print(f"coding questions (with reference): {len(rows)}")
    print(f"  cases==0: {zero}   cases==1: {one}   cases>=2: {ok}")
    print(f"  questions with < 3 hidden cases: {below}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true",
                    help="also top up questions that already have enough hidden cases")
    ap.add_argument("--dry-run", action="store_true", help="report changes; write nothing")
    ap.add_argument("--report", action="store_true", help="print inventory and exit")
    ap.add_argument("--min-hidden", type=int, default=3, help="target hidden cases (default 3)")
    ap.add_argument("--limit", type=int, default=0, help="process at most N questions (0 = all)")
    args = ap.parse_args()

    db = db_manager.get_db_session()  # no tenant bound -> sees every company's rows
    try:
        if args.report:
            _report(db)
            return

        rows = _fetch(db)
        tally = dict(ok=0, partial=0, skip_openended=0, skip_sufficient=0,
                     skip_no_sandbox=0, failed=0)
        processed = 0

        for qid, qtext, sample_answer, raw_tc, category in rows:
            existing = _parse_cases(raw_tc)
            if _hidden(existing) >= args.min_hidden and not args.all:
                tally["skip_sufficient"] += 1
                continue
            if args.limit and processed >= args.limit:
                break
            processed += 1

            try:
                cases = ensure_hidden_testcases(
                    qtext, sample_answer, category, existing, min_hidden=args.min_hidden)
            except Exception as e:
                print(f"[FAIL]            {qid[:8]} — {str(e)[:80]}")
                tally["failed"] += 1
                continue

            if cases is None:
                print(f"[SKIP-OPENENDED]  {qid[:8]} — reference not runnable code")
                tally["skip_openended"] += 1
                continue

            new_hidden = _hidden(cases)
            # Helper returns existing unchanged when the sandbox is down (stdin Qs).
            if new_hidden < args.min_hidden and new_hidden <= _hidden(existing):
                print(f"[SKIP-NO-SANDBOX] {qid[:8]} — could not synthesize (sandbox/LLM down?)")
                tally["skip_no_sandbox"] += 1
                continue

            tag = "OK" if new_hidden >= args.min_hidden else "PARTIAL"
            print(f"[{tag:14}] {qid[:8]} — {len(cases)} case(s), {new_hidden} hidden", flush=True)
            tally["ok" if tag == "OK" else "partial"] += 1
            if not args.dry_run:
                db.execute(
                    text("UPDATE interview_questions SET test_cases=:tc WHERE id=:id"),
                    {"tc": json.dumps(cases), "id": qid},
                )
                db.commit()  # commit per-question so progress persists and is observable

        if not args.dry_run:
            db.commit()
        print(
            f"\nDone. ok={tally['ok']} partial={tally['partial']} "
            f"skip-openended={tally['skip_openended']} "
            f"skip-sufficient={tally['skip_sufficient']} "
            f"skip-no-sandbox={tally['skip_no_sandbox']} failed={tally['failed']}"
            f"{' (dry-run, nothing written)' if args.dry_run else ''}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
