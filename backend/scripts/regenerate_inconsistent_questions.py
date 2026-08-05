"""Regenerate coding questions whose REFERENCE fails its own test cases.

Unlike ``regenerate_broken_questions.py`` (which targets questions with too FEW
hidden cases), this targets questions that are INCONSISTENT: grading the stored
reference solution (``sample_answer``) against the stored ``test_cases`` does not
return AC on every case. These come from generation done while the Docker sandbox
validator was unavailable — symptoms seen in the audit:
  * empty / wrong ``expected_output`` (LLM values never verified),
  * off-by-one or otherwise wrong reference solutions,
  * Python solutions misclassified under a SQL category ("near \"def\": syntax error").

For each inconsistent slot we generate a fresh coding problem via the normal
pipeline (``PromptBuilder`` + ``gemini_service`` + ``_validate_question_content``),
accept only ones that are actually gradeable (reference runs and grades AC against
>= 1 sample + ``--min-hidden`` hidden cases) and are NOT duplicates of existing
questions, then overwrite the broken row's content (keeping its id / position).

Self-consistency is checked in the Docker sandbox, so Docker Desktop must be running.

Usage::
    python scripts/regenerate_inconsistent_questions.py --report        # scan only, write nothing
    python scripts/regenerate_inconsistent_questions.py --dry-run       # show planned replacements
    python scripts/regenerate_inconsistent_questions.py                 # apply
    python scripts/regenerate_inconsistent_questions.py --interview 78  # one interview only
"""
import sys
import re
import json
import asyncio
import argparse
from types import SimpleNamespace
from collections import defaultdict
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


def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())[:80]


def _hidden(cases) -> int:
    return sum(1 for c in cases if isinstance(c, dict) and not c.get("is_sample"))


def _parse_cases(raw):
    try:
        cases = json.loads(raw) if raw else []
    except Exception:
        return []
    return cases if isinstance(cases, list) else []


def _lang_of(sample_answer, category):
    return "sql" if looks_like_sql(sample_answer or "", category) else "python"


def _is_consistent(sample_answer, cases, category) -> bool:
    """True iff grading the reference against its own cases returns AC on all of them."""
    if not cases:
        return False
    if not (sample_answer or "").strip():
        return False
    lang = _lang_of(sample_answer, category)
    probe = SimpleNamespace(test_cases=json.dumps(cases), points=10, sample_answer=sample_answer)
    r = grade_interview_question(probe, lang, sample_answer or "", reveal_hidden=True)
    if r.get("unavailable"):
        # Sandbox down — can't judge; treat as consistent so we never wrongly replace.
        raise RuntimeError("sandbox unavailable during consistency check")
    return r.get("verdict") == "AC" and bool(r.get("total")) and r["passed"] == r["total"]


def _gradeable(q, min_hidden: int):
    """Return (lang, cases) if freshly generated q is a gradeable coding question, else None."""
    cases = q.test_cases if isinstance(q.test_cases, list) else _parse_cases(getattr(q, "test_cases", None))
    if _hidden(cases) < min_hidden:
        return None
    lang = _lang_of(getattr(q, "sample_answer", ""), getattr(q, "category", None))
    probe = SimpleNamespace(test_cases=json.dumps(cases), points=q.points or 10,
                            sample_answer=q.sample_answer)
    r = grade_interview_question(probe, lang, q.sample_answer or "", reveal_hidden=True)
    if r.get("verdict") == "AC" and r.get("passed") and r["passed"] == r["total"] and r["total"] >= 1 + min_hidden:
        return lang, cases
    return None


def _scan(db, only_interview=None):
    """Return {interview_id: (interview_row, [healthy...], [broken Question...])}."""
    qy = db.query(Question).filter(Question.question_type == "coding")
    if only_interview is not None:
        qy = qy.filter(Question.interview_id == only_interview)
    coding = qy.all()
    by_iv = defaultdict(list)
    for q in coding:
        by_iv[q.interview_id].append(q)

    out = {}
    for iv_id, qs in by_iv.items():
        iv = db.query(Interview).filter(Interview.id == iv_id).first() if iv_id is not None else None
        broken = []
        for q in qs:
            cases = _parse_cases(q.test_cases)
            try:
                ok = _is_consistent(q.sample_answer, cases, q.category)
            except RuntimeError as e:
                print(f"  ! {q.id[:8]}: {e}; aborting (start Docker Desktop and retry)")
                sys.exit(2)
            if not ok:
                broken.append(q)
        out[iv_id] = (iv, [q for q in qs if q not in broken], broken)
    return out


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interview", type=int, default=None, help="only this interview id")
    ap.add_argument("--min-hidden", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true", help="report planned replacements; write nothing")
    ap.add_argument("--report", action="store_true", help="scan + print inventory, then exit")
    ap.add_argument("--max-rounds", type=int, default=8)
    args = ap.parse_args()

    db = db_manager.get_db_session()  # no tenant bound -> sees every company's rows
    try:
        print("Scanning coding questions for self-consistency (runs each reference in the sandbox)...")
        scan = _scan(db, args.interview)
        total_broken = sum(len(b) for _, (_iv, _h, b) in scan.items())
        total_q = sum(len(h) + len(b) for _, (_iv, h, b) in scan.items())
        print(f"\ncoding questions: {total_q} | healthy: {total_q - total_broken} | inconsistent: {total_broken}")
        for iv_id, (iv, healthy, broken) in sorted(scan.items(), key=lambda kv: (kv[0] is None, kv[0])):
            if broken:
                label = f"interview {iv_id}" + (f" ({iv.job_position})" if iv and iv.job_position else "")
                print(f"  {label}: {len(broken)} inconsistent -> {[q.id[:8] for q in broken]}")

        if args.report or total_broken == 0:
            print("\n(report only)" if args.report else "\nNothing to regenerate.")
            return

        pb = PromptBuilder()
        grand_replaced = 0
        for iv_id, (iv, healthy, broken) in scan.items():
            if not broken:
                continue
            label = f"interview {iv_id}"
            print(f"\n=== {label}: regenerating {len(broken)} question(s) ===")
            seen = {_norm(q.question_text) for q in (healthy + broken)}
            accepted = []  # (qtext, sample_answer, code_template, cases, points, lang)
            rounds = 0
            job_pos = (iv.job_position if iv else None) or "Software Engineer"
            iv_type = (iv.interview_type if iv else None) or "technical"
            diff = (iv.difficulty_level if iv else None) or "Medium"

            while len(accepted) < len(broken) and rounds < args.max_rounds:
                rounds += 1
                need = len(broken) - len(accepted)
                prompt = pb.build_generation_prompt(
                    job_position=job_pos, interview_type=iv_type, difficulty_level=diff,
                    question_count=min(5, need + 1), question_types=["coding"])
                try:
                    gen = await gemini_service.generate_questions(prompt, min(5, need + 1))
                except Exception as e:
                    print(f"  round {rounds}: generation error {str(e)[:80]}")
                    continue
                for q in gen:
                    q.question_type = "coding"
                    try:
                        _validate_question_content(q, "coding")
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
                print(f"  round {rounds}: {len(accepted)}/{len(broken)} accepted")

            n = min(len(accepted), len(broken))
            for i in range(n):
                q = broken[i]
                qtext, sa, tmpl, cases, pts, lang = accepted[i]
                print(f"  [REPLACE] {q.id[:8]} <- {lang} {qtext[:45]!r} ({_hidden(cases)} hidden)")
                if not args.dry_run:
                    db.execute(text(
                        "UPDATE interview_questions SET question_text=:qt, sample_answer=:sa, "
                        "code_template=:tpl, test_cases=:tc, points=:pts, category=:cat, "
                        "options=NULL, correct_answer=NULL, updated_at=NOW() WHERE id=:id"),
                        {"qt": qtext, "sa": sa, "tpl": tmpl, "tc": json.dumps(cases),
                         "pts": pts, "cat": ("sql" if lang == "sql" else "coding"), "id": q.id})
                    db.commit()
            grand_replaced += n
            if n < len(broken):
                print(f"  ! {len(broken) - n} slot(s) still inconsistent (generation could not produce gradeable replacements)")

        print(f"\nDone. replaced={grand_replaced} / {total_broken}"
              f"{' (dry-run, nothing written)' if args.dry_run else ''}")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
