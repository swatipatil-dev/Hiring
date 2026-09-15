"""Repair: canonicalise question-type strings written with a label or an alias.

Background
----------
`interview_questions.question_type` and the `question_types[].type` entries inside
`interviews.skills_config_data` are free-form strings — `SkillQuestionType.type` is a
plain `str` with no enum and no validator (routes/enhanced_interviewer_routes.py),
persisted with `model_dump()` and read back by the drafts endpoint through a bare
`json.loads`. So whatever spelling a row was written with is what every reader gets.

The frontend keys everything off the canonical value: TYPE_LABELS, TYPE_TIME_DEFAULTS,
the pool key, the MCQ-only "Add Manually"/"CSV" actions, and the `question_type` it
sends to the generator. A row holding `descriptive` where `short_answer` belongs loses
its label, silently takes the 5-minute-per-question fallback instead of its real time,
and fails to match at set-assembly time.

This script rewrites those strings to the canonical key. It does NOT invent a type for
a value it does not recognise — an unknown string is reported and left alone, so a
genuinely new question type is never quietly turned into something else.

Safety
------
* DRY-RUN by default: writes NOTHING unless `--apply` is passed.
* Idempotent: re-running after `--apply` reports 0 changes.
* Connects to whatever DATABASE_URL / DB_* env vars point at (see
  src/config/database_config_mysql.py). Check the printed "Database/host" line before
  using --apply.

Usage
-----
    python scripts/repair_question_types.py            # dry-run (default)
    python scripts/repair_question_types.py --apply    # write + commit
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager

# The keys the wizard and the generator agree on.
CANONICAL = {
    "multiple_choice", "coding", "short_answer",
    "case_study", "aptitude", "communication",
}

# Values seen in the wild that mean a canonical type. Keep this in step with
# normalizeQuestionType() in frontend/src/pages/shared/CreateTestWizard.jsx.
ALIASES = {
    "mcq": "multiple_choice",
    "multiplechoice": "multiple_choice",
    "multiple-choice": "multiple_choice",
    "descriptive": "short_answer",
    "shortanswer": "short_answer",
    "casestudy": "case_study",
}

# Types the ORM enum allows that the wizard has no row for. They are left alone:
# rewriting them would change what a stored question actually is.
KNOWN_NON_WIZARD = {"behavioral", "technical", "situational"}


def canonical_for(value):
    """Return the canonical key for `value`, or None to leave it untouched."""
    if value is None:
        return None
    key = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if not key or key in CANONICAL or key in KNOWN_NON_WIZARD:
        return None
    return ALIASES.get(key) or ALIASES.get(key.replace("_", ""))


def repair_questions(session, apply):
    """Canonicalise interview_questions.question_type."""
    rows = session.execute(text(
        "SELECT question_type, COUNT(*) FROM interview_questions GROUP BY question_type"
    )).fetchall()

    changes, unknown = [], []
    for value, count in rows:
        target = canonical_for(value)
        if target:
            changes.append((value, target, count))
        elif value is not None and str(value).strip().lower() not in CANONICAL \
                and str(value).strip().lower() not in KNOWN_NON_WIZARD:
            unknown.append((value, count))

    if apply:
        for value, target, _ in changes:
            session.execute(
                text("UPDATE interview_questions SET question_type = :t WHERE question_type = :v"),
                {"t": target, "v": value},
            )
    return changes, unknown


def repair_skills_config(session, apply):
    """Canonicalise question_types[].type inside interviews.skills_config_data."""
    rows = session.execute(text(
        "SELECT id, skills_config_data FROM interviews WHERE skills_config_data IS NOT NULL"
    )).fetchall()

    changed = []
    for iv_id, blob in rows:
        try:
            cfg = json.loads(blob)
        except (ValueError, TypeError):
            continue  # unparseable blobs are left for a human; rewriting would lose data
        if not isinstance(cfg, dict):
            continue

        edits = []
        for skill in (cfg.get("skills") or []):
            if not isinstance(skill, dict):
                continue
            for qt in (skill.get("question_types") or []):
                if not isinstance(qt, dict):
                    continue
                target = canonical_for(qt.get("type"))
                if target:
                    edits.append((skill.get("name"), qt.get("type"), target))
                    qt["type"] = target

        if edits:
            changed.append((iv_id, edits))
            if apply:
                session.execute(
                    text("UPDATE interviews SET skills_config_data = :b WHERE id = :i"),
                    {"b": json.dumps(cfg), "i": iv_id},
                )
    return changed


def main():
    ap = argparse.ArgumentParser(
        description="Canonicalise stored question-type strings.")
    ap.add_argument("--apply", action="store_true", help="Write changes (default: dry-run).")
    args = ap.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    session = db_manager.get_db_session()
    try:
        print(f"=== Repair question types [{mode}] ===")
        try:
            info = session.execute(text("SELECT DATABASE(), @@hostname")).fetchone()
            print(f"Database: {info[0]}   host: {info[1]}")
        except Exception:
            pass

        changes, unknown = repair_questions(session, args.apply)
        total = sum(c for _, _, c in changes)
        print(f"\n[1] interview_questions.question_type — {total} row(s) to rewrite")
        for value, target, count in changes:
            print(f"    {value!r} -> {target!r}  ({count} rows)")
        if not changes:
            print("    nothing to do")
        if unknown:
            print("    left alone (unrecognised, needs a human):")
            for value, count in unknown:
                print(f"      {value!r}  ({count} rows)")

        sc_changed = repair_skills_config(session, args.apply)
        print(f"\n[2] interviews.skills_config_data — {len(sc_changed)} interview(s) to rewrite")
        for iv_id, edits in sc_changed[:25]:
            for name, old, new in edits:
                print(f"    interview {iv_id}: skill {name!r}  {old!r} -> {new!r}")
        if not sc_changed:
            print("    nothing to do")
        elif len(sc_changed) > 25:
            print(f"    ... and {len(sc_changed) - 25} more")

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
