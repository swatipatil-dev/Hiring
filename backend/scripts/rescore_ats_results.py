"""Backfill: re-score stored ATS results under the current rubric.

Background
----------
Scoring changed in two ways that make old scores incomparable with new ones:

  1. Factor weights shifted toward demonstrated experience and projects, away
     from keyword placement (`DEFAULT_ATS_WEIGHTS`).
  2. A required skill now earns full credit only where the resume evidences it
     in work history or a project; the same skill in a bare list is discounted
     (`SKILL_EVIDENCE_WEIGHTS`).

Until every row is re-scored, a shortlist sorted by `ats_score` is comparing
candidates measured with two different rulers. This script recomputes
`ats_results` rows (and the denormalised `candidates.ats_score` the HR dashboard
sorts on) using exactly the inputs the live apply path uses.

Safety
------
* DRY-RUN by default: writes NOTHING unless `--apply` is passed.
* Idempotent: rows already at the current `rubric_version` are skipped unless
  `--force` is given, so re-running after `--apply` reports 0 changes.
* Connects to whatever DATABASE_URL / DB_* env vars point at (see
  src/config/database_config_mysql.py). To run against PRODUCTION, set those to
  the prod instance BEFORE invoking, and check the printed "Database/host" line
  before using --apply.
* Never touches `current_stage` or any pipeline state. Scores only.
* Skips and counts — never writes a 0 — when a candidate has no scoring text,
  the job is missing, or scoring raises.

A caveat to read out loud before anyone interprets the report: a candidate whose
PROFILE changed since they applied will get a score reflecting the new profile
*as well as* the new rubric. The delta is not purely the rubric's doing.

The report is the only record of the old scores. Keep it.

Usage
-----
    python scripts/rescore_ats_results.py --report before_after.csv
    python scripts/rescore_ats_results.py --job <job_id> --apply
    python scripts/rescore_ats_results.py --company <company_id> --limit 50
"""
import sys
import csv
import json
import argparse
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text
from src.config.database_config_mysql import db_manager
from src.models.models import ATSResult, Candidate, JobPosting, CandidateProfileExt
from src.services.enhanced_ats_service import (
    ATS_RUBRIC_VERSION,
    EnhancedATSScoringService,
)


# The same reader the live scoring path uses, so a backfilled score cannot
# differ from a live one merely because the two parsed the column differently.
from src.services.skills_parsing import parse_skills as _parse_skills, parse_skills_or_none


def _profile_inputs(profile):
    """The structured sub-score inputs, shaped exactly as candidate_routes builds them.

    Mirrors routes/candidate_routes.py so a backfilled score is one the live
    path would also have produced. Diverging here would silently manufacture
    scores that no application flow can reproduce.
    """
    if not profile:
        return {"educations": [], "institution_names": [], "work_experiences": [],
                "links_text": "", "permanent_pincode": None}

    educations = [
        {"education_level": e.education_level, "degree": e.degree,
         "institution_name": e.institution_name, "grade": e.grade}
        for e in (profile.educations or [])
    ]
    work_experiences = [
        {"employment_type": w.employment_type, "job_title": w.job_title}
        for w in (profile.work_experiences or [])
    ]
    link_bits = [profile.github_url or "", profile.linkedin_url or ""]
    link_bits += [p.repository_url or "" for p in (profile.projects or [])]
    link_bits += [p.project_url or "" for p in (profile.projects or [])]

    return {
        "educations": educations,
        "institution_names": [e["institution_name"] for e in educations if e["institution_name"]],
        "work_experiences": work_experiences,
        "links_text": " ".join(b for b in link_bits if b),
        "permanent_pincode": (profile.permanent_pincode or profile.pincode),
    }


def _stored_rubric_version(row):
    breakdown = row.scoring_breakdown
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown)
        except (ValueError, TypeError):
            return None
    return (breakdown or {}).get("rubric_version") if isinstance(breakdown, dict) else None


def rescore(session, args):
    """Recompute every selected ATSResult. Returns (rows, counters)."""
    from routes.candidate_profile_routes import build_profile_text

    svc = EnhancedATSScoringService()
    counters = defaultdict(int)
    rows = []

    query = session.query(ATSResult)
    if args.job:
        query = query.filter(ATSResult.job_id == args.job)
    if args.company:
        query = query.filter(ATSResult.company_id == args.company)
    query = query.order_by(ATSResult.job_id)
    if args.limit:
        query = query.limit(args.limit)
    results = query.all()

    # Cache per-job JD inputs and per-candidate parsed resumes: scoring N
    # candidates against one job should parse that JD's skills once, and one
    # candidate against M jobs should run the resume-only half once.
    job_cache, prepared_cache = {}, {}

    for row in results:
        counters["examined"] += 1
        if not args.force and _stored_rubric_version(row) == ATS_RUBRIC_VERSION:
            counters["already_current"] += 1
            continue

        job = job_cache.get(row.job_id)
        if job is None:
            job = session.query(JobPosting).filter(JobPosting.id == row.job_id).first()
            job_cache[row.job_id] = job
        if job is None:
            counters["skipped_missing_job"] += 1
            continue

        candidate = session.query(Candidate).filter(Candidate.id == row.candidate_id).first()
        if candidate is None:
            counters["skipped_missing_candidate"] += 1
            continue

        profile = None
        if candidate.user_id:
            profile = session.query(CandidateProfileExt).filter(
                CandidateProfileExt.user_id == candidate.user_id
            ).first()
        scoring_text = (build_profile_text(profile) if profile else "") or (candidate.resume_text or "")
        if not scoring_text.strip():
            counters["skipped_no_text"] += 1
            continue

        prepared = prepared_cache.get(row.candidate_id)
        if prepared is None:
            prepared = svc.prepare_resume(scoring_text)
            prepared_cache[row.candidate_id] = prepared

        try:
            scored = svc.enhanced_score_candidate(
                resume_text=scoring_text,
                job_description=(job.description or "") + " " + (job.requirements or ""),
                required_skills=_parse_skills(job.required_skills),
                required_experience=job.experience_years or 0,
                # Must match the live path exactly, including the None sentinel
                # — flattening it to [] would opt every legacy job into the new
                # scoring and make the report measure the wrong thing.
                preferred_skills=parse_skills_or_none(job.preferred_skills),
                prepared=prepared,
                **_profile_inputs(profile),
            )
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the run
            counters["skipped_error"] += 1
            print(f"  ! {row.candidate_id[:8]}/{row.job_id[:8]}: {exc}")
            continue

        old_score = float(row.ats_score or 0.0)
        old_breakdown = row.scoring_breakdown
        if isinstance(old_breakdown, str):
            try:
                old_breakdown = json.loads(old_breakdown)
            except (ValueError, TypeError):
                old_breakdown = {}
        old_rating = (old_breakdown or {}).get("rating", "") if isinstance(old_breakdown, dict) else ""
        old_skills = float(row.skills_match_score or 0.0)

        rows.append({
            "candidate_id": row.candidate_id,
            "job_id": row.job_id,
            "old_score": round(old_score, 2),
            "new_score": scored.ats_score,
            "delta": round(scored.ats_score - old_score, 2),
            "old_rating": old_rating,
            "new_rating": scored.rating,
            "old_skills": round(old_skills, 2),
            "new_skills": scored.skills_match_score,
            "evidence_weighted": (
                scored.scoring_breakdown.get("sub_scores", {})
                .get("skills", {}).get("evidence_weighted")
            ),
        })

        if args.apply:
            row.ats_score = scored.ats_score
            row.skills_match_score = scored.skills_match_score
            row.semantic_score = scored.semantic_score
            row.experience_score = scored.experience_score
            row.keyword_score = scored.keyword_score
            row.matched_skills = json.dumps(scored.matched_skills)
            row.missing_skills = json.dumps(scored.missing_skills)
            row.scoring_breakdown = json.dumps(scored.scoring_breakdown)
            # The HR dashboard sorts and filters on this denormalised copy; if it
            # drifts from ats_results the list order stops matching the numbers
            # shown in it. Same transaction, always.
            candidate.ats_score = scored.ats_score
        counters["rescored"] += 1

    return rows, counters


def summarise(rows):
    """Print the checks that decide whether this is safe to apply."""
    if not rows:
        print("\nNothing to summarise.")
        return

    deltas = [r["delta"] for r in rows]
    olds = [r["old_score"] for r in rows]
    news = [r["new_score"] for r in rows]
    mean = lambda xs: sum(xs) / len(xs)  # noqa: E731

    print(f"\n  mean score   {mean(olds):6.2f} -> {mean(news):6.2f}  ({mean(deltas):+.2f})")
    print(f"  range        [{min(news):.2f}, {max(news):.2f}]")

    band_changes = [r for r in rows if r["old_rating"] and r["old_rating"] != r["new_rating"]]
    print(f"  rating band changed:      {len(band_changes)} of {len(rows)}")

    # The invariant that bounds this change: evidence weights are all <= 1.0, so
    # an evidence-weighted skills sub-score must never rise. A violation means
    # something other than the intended discount moved.
    risen = [r for r in rows if r["evidence_weighted"] and r["new_skills"] > r["old_skills"] + 0.01]
    print(f"  skills sub-score ROSE:    {len(risen)}"
          + ("  <-- INVARIANT VIOLATED, investigate before --apply" if risen else "  (expected: 0)"))

    out_of_range = [r for r in rows if not (0 <= r["new_score"] <= 100)]
    print(f"  out of [0,100]:           {len(out_of_range)}")

    if abs(mean(deltas)) > 5:
        print("\n  ! mean moved more than 5 points - the rubric is more aggressive than intended.")
        print("    Retune DEFAULT_ATS_WEIGHTS before applying.")

    by_job = defaultdict(list)
    for r in rows:
        by_job[r["job_id"]].append(r)
    reordered = 0
    for job_rows in by_job.values():
        if len(job_rows) < 2:
            continue
        old_order = [r["candidate_id"] for r in sorted(job_rows, key=lambda r: -r["old_score"])]
        new_order = [r["candidate_id"] for r in sorted(job_rows, key=lambda r: -r["new_score"])]
        if old_order != new_order:
            reordered += 1
    print(f"  jobs whose ranking moved: {reordered} of {len(by_job)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="write and commit (default: dry-run)")
    parser.add_argument("--force", action="store_true",
                        help="re-score even rows already at the current rubric version")
    parser.add_argument("--job", help="limit to one job id")
    parser.add_argument("--company", help="limit to one company id")
    parser.add_argument("--limit", type=int, help="cap the number of rows examined")
    parser.add_argument("--report", help="write a before/after CSV here")
    args = parser.parse_args()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"=== Re-score ATS results ({mode}) — target rubric_version {ATS_RUBRIC_VERSION} ===")

    session = db_manager.get_db_session()
    try:
        try:
            info = session.execute(text("SELECT DATABASE(), @@hostname")).fetchone()
            print(f"Database/host: {info[0]} @ {info[1]}")
        except Exception:
            print("Database/host: (could not determine)")

        rows, counters = rescore(session, args)

        print("\nCounts:")
        for key in ("examined", "already_current", "rescored", "skipped_no_text",
                    "skipped_missing_job", "skipped_missing_candidate", "skipped_error"):
            print(f"  {key:26} {counters[key]}")

        summarise(rows)

        if args.report and rows:
            with open(args.report, "w", newline="", encoding="utf-8") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
                writer.writeheader()
                writer.writerows(rows)
            print(f"\nReport written: {args.report}")
            print("This is the ONLY record of the old scores. Keep it.")

        if args.apply:
            session.commit()
            print("\nCommitted.")
        else:
            session.rollback()
            print("\nDry-run: nothing written. Re-run with --apply to commit.")
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
