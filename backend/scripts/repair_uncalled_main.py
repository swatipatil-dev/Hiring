"""Repair coding questions whose reference solution defines ``def main():`` but
never CALLS it — so the program runs and prints nothing, making the question
ungradeable (the stdin/stdout judge sees empty output).

These slipped in when question generation ran while the sandbox validator was
unavailable. The fix is to append a top-level ``main()`` call to the reference
(``sample_answer``). Idempotent — only touches references missing the call.

After running this, re-run ``scripts/backfill_hidden_testcases.py`` to synthesize
their now-validatable test cases.

Usage::
    python scripts/repair_uncalled_main.py --dry-run
    python scripts/repair_uncalled_main.py
"""
import sys
import re
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager

_DEF_NOARG_MAIN = re.compile(r"\bdef\s+main\s*\(\s*\)\s*:")


def _defines_noarg_main(src: str) -> bool:
    return bool(_DEF_NOARG_MAIN.search(src or ""))


def _calls_main(src: str) -> bool:
    for ln in (src or "").splitlines():
        t = ln.strip()
        if t.startswith("def "):
            continue
        if re.search(r"\bmain\s*\(\s*\)", t):
            return True
    return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="report only; write nothing")
    args = ap.parse_args()

    db = db_manager.get_db_session()
    fixed = 0
    try:
        rows = db.execute(text(
            "SELECT id, sample_answer FROM interview_questions "
            "WHERE question_type='coding' AND sample_answer LIKE '%def main(%'"
        )).fetchall()
        for qid, sa in rows:
            if not _defines_noarg_main(sa) or _calls_main(sa):
                continue
            new_sa = sa.rstrip() + "\n\nmain()\n"
            print(f"[FIX] {qid[:8]} — appended main() call")
            if not args.dry_run:
                db.execute(
                    text("UPDATE interview_questions SET sample_answer=:sa WHERE id=:id"),
                    {"sa": new_sa, "id": qid},
                )
            fixed += 1
        if not args.dry_run:
            db.commit()
        print(f"\nDone. fixed={fixed}{' (dry-run, nothing written)' if args.dry_run else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
