"""Regenerate broken coding questions in a test (interview).

A "broken" coding question is one that can't be auto-graded: fewer than
``--min-hidden`` hidden test cases (usually a buggy/un-runnable reference left
over from generation done while the sandbox validator was unavailable).

For each broken slot we GENERATE fresh coding problems via the normal pipeline
(``PromptBuilder`` + ``gemini_service`` + ``_validate_question_content``), accept
only ones that are actually gradeable (reference runs, grades AC against >= 1
sample + min_hidden hidden cases) and are NOT duplicates of existing questions,
then overwrite the broken row's content (keeping its id/position in the test).

Usage::
    python scripts/regenerate_broken_questions.py --interview 78 --dry-run
    python scripts/regenerate_broken_questions.py --interview 78
"""
import sys
import re
import json
import asyncio
import argparse
from types import SimpleNamespace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager
from src.models.models import Interview
from src.models.question_models import Question
from src.services.gemini_service import gemini_service
from src.prompt_builder import PromptBuilder
from src.judge.sql_testgen import looks_like_sql
from src.judge.interview_grading import grade_interview_question
from generator_router import _validate_question_content

_LANG_ALIASES = {"python3": "python", "py": "python", "node": "javascript", "c++": "cpp"}


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())[:80]


def _hidden(cases) -> int:
    return sum(1 for c in cases if isinstance(c, dict) and not c.get("is_sample"))


def _gradeable(q, min_hidden: int):
    """Return (lang, cases) if q is a gradeable coding question, else None."""
    cases = q.test_cases if isinstance(q.test_cases, list) else []
    if _hidden(cases) < min_hidden:
        return None
    lang = "sql" if looks_like_sql(getattr(q, "sample_answer", ""), getattr(q, "category", None)) else "python"
    probe = SimpleNamespace(test_cases=json.dumps(cases), points=q.points or 10,
                            sample_answer=q.sample_answer)
    r = grade_interview_question(probe, lang, q.sample_answer or "", reveal_hidden=True)
    if r.get("verdict") == "AC" and r.get("passed") and r["passed"] == r["total"] and r["total"] >= 1 + min_hidden:
        return lang, cases
    return None


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interview", type=int, required=True)
    ap.add_argument("--min-hidden", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--max-rounds", type=int, default=8)
    args = ap.parse_args()

    db = db_manager.get_db_session()
    try:
        iv = db.query(Interview).filter(Interview.id == args.interview).first()
        if not iv:
            print(f"interview {args.interview} not found"); return

        coding = db.query(Question).filter(
            Question.interview_id == args.interview,
            Question.question_type == "coding").all()
        broken = [q for q in coding
                  if _hidden(json.loads(q.test_cases) if q.test_cases else []) < args.min_hidden]
        seen = {_norm(q.question_text) for q in coding}  # dedup against ALL current texts
        print(f"interview {args.interview}: {len(coding)} coding, {len(broken)} broken to replace")
        if not broken:
            print("nothing to do"); return

        pb = PromptBuilder()
        accepted = []  # list of (question_text, sample_answer, code_template, test_cases, points, lang)
        rounds = 0
        while len(accepted) < len(broken) and rounds < args.max_rounds:
            rounds += 1
            need = len(broken) - len(accepted)
            prompt = pb.build_generation_prompt(
                job_position=iv.job_position or "Software Engineer",
                interview_type=iv.interview_type or "technical",
                difficulty_level=iv.difficulty_level or "Medium",
                question_count=min(5, need + 1),
                question_types=["coding"],
            )
            try:
                gen = await gemini_service.generate_questions(prompt, min(5, need + 1))
            except Exception as e:
                print(f"  round {rounds}: generation error {str(e)[:80]}"); continue

            for q in gen:
                q.question_type = "coding"
                try:
                    _validate_question_content(q, "coding")   # validates + tops up hidden cases
                except Exception:
                    continue
                key = _norm(q.question_text)
                if key in seen:
                    continue
                ok = _gradeable(q, args.min_hidden)
                if not ok:
                    continue
                lang, cases = ok
                seen.add(key)
                accepted.append((q.question_text, q.sample_answer,
                                 getattr(q, "code_template", None), cases, q.points or 10, lang))
                print(f"  [accept {len(accepted)}/{len(broken)}] {lang} — {q.question_text[:55]!r}")
                if len(accepted) >= len(broken):
                    break
            print(f"  round {rounds}: have {len(accepted)}/{len(broken)} accepted")

        # Assign accepted replacements to broken slots (raw SQL update; keep ids).
        n = min(len(accepted), len(broken))
        for i in range(n):
            q = broken[i]
            qtext, sa, tmpl, cases, pts, lang = accepted[i]
            print(f"[REPLACE] {q.id[:8]} <- {lang} {qtext[:45]!r} ({_hidden(cases)} hidden)")
            if not args.dry_run:
                db.execute(text(
                    "UPDATE interview_questions SET question_text=:qt, sample_answer=:sa, "
                    "code_template=:tpl, test_cases=:tc, points=:pts, updated_at=NOW() "
                    "WHERE id=:id"),
                    {"qt": qtext, "sa": sa, "tpl": tmpl, "tc": json.dumps(cases),
                     "pts": pts, "id": q.id})
                db.commit()
        leftover = len(broken) - n
        print(f"\nDone. replaced={n} still_broken={leftover}"
              f"{' (dry-run)' if args.dry_run else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
