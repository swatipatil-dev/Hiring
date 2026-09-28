"""Preview how the current scoring rules would re-rank a job's applicants. Saves nothing.

Scores never change on deploy: existing results keep their score and show "scoring rules
updated" until HR rescores. Run this first to see what a rescore would do.

    python scripts/ats_rules_dry_run.py --job <job_id> [--top 10] [--csv out.csv]

Prints, per applicant, the stored score and rank against the re-computed ones; how many of
the top N stay in the top N; Kendall's tau between the two rankings; and the biggest movers
with the evidence that moved them.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from itertools import combinations

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


def kendall_tau(order_a, order_b):
    """Kendall's tau-a between two rankings of the same items (1 = identical, -1 = reversed)."""
    pos_b = {x: i for i, x in enumerate(order_b)}
    items = [x for x in order_a if x in pos_b]
    if len(items) < 2:
        return 1.0
    conc = disc = 0
    for x, y in combinations(items, 2):
        a = order_a.index(x) - order_a.index(y)
        b = pos_b[x] - pos_b[y]
        if a * b > 0:
            conc += 1
        elif a * b < 0:
            disc += 1
    total = len(items) * (len(items) - 1) / 2
    return (conc - disc) / total


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--job", required=True)
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--csv")
    args = ap.parse_args()

    from src.config.database_config_mysql import get_db
    from src.models.models import ATSResult, Candidate, JobPosting
    from src.services.ats_scoring_pipeline import effective_score, score_for_job
    from routes.ats_explain_routes import rescoring_inputs
    from src.services.enhanced_ats_service import SCORING_RULES_VERSION, EnhancedATSScoringService

    db = next(get_db())
    try:
        job = db.query(JobPosting).filter(JobPosting.id == args.job).first()
        if not job:
            sys.exit(f"No job {args.job}")
        svc = EnhancedATSScoringService()
        rows = []
        for cand in db.query(Candidate).filter(Candidate.job_id == job.id).all():
            stored = db.query(ATSResult).filter(ATSResult.candidate_id == cand.id,
                                                ATSResult.job_id == job.id).first()
            out = score_for_job(rescoring_inputs(db, cand), job, service=svc)
            ex = getattr(out.result, "explanation", {}) or {}
            levels = [c["level"] for c in (ex.get("mandatory") or {}).get("items", [])]
            rows.append({
                "candidate": f"{cand.first_name} {cand.last_name}".strip(),
                "old": effective_score(stored) if stored else None,
                "old_rules": stored.scoring_rules_version if stored else None,
                "new": float(out.result.ats_score or 0),
                "parse": out.parse["status"],
                "mandatory": f"{getattr(out.result, 'mandatory_met', '?')}/{getattr(out.result, 'mandatory_total', '?')}",
                "shown/listed/related/missing": "/".join(str(levels.count(k)) for k in
                                                         ("demonstrated", "listed", "related", "not_found")),
                "coverage": getattr(out.result, "evidence_coverage", None),
                "unknown": ",".join(r["key"] for r in ex.get("factors", []) if r["status"] != "scored"),
            })
    finally:
        db.close()

    ranked_old = [r["candidate"] for r in sorted((r for r in rows if r["old"] is not None),
                                                  key=lambda r: -r["old"])]
    ranked_new = [r["candidate"] for r in sorted((r for r in rows if r["parse"] == "ok"),
                                                  key=lambda r: -r["new"])]
    for r in rows:
        r["old_rank"] = ranked_old.index(r["candidate"]) + 1 if r["candidate"] in ranked_old else None
        r["new_rank"] = ranked_new.index(r["candidate"]) + 1 if r["candidate"] in ranked_new else None

    print(f"Job: {job.title}  ({len(rows)} applicants)  rules now: {SCORING_RULES_VERSION}\n")
    header = f"{'Candidate':28} {'old':>6} {'new':>6} {'rank':>9}  {'parse':12} {'mand':>5}  evidence  unknown"
    print(header)
    print("-" * len(header))
    for r in sorted(rows, key=lambda r: (r["new_rank"] or 999)):
        rank = f"{r['old_rank'] or '-':>3} -> {r['new_rank'] or '-':<3}"
        old = f"{r['old']:.1f}" if r["old"] is not None else "-"
        print(f"{r['candidate'][:28]:28} {old:>6} {r['new']:>6.1f} {rank:>9}  {r['parse']:12} "
              f"{r['mandatory']:>5}  {r['shown/listed/related/missing']:9} {r['unknown']}")

    n = min(args.top, len(ranked_old), len(ranked_new))
    if n:
        overlap = len(set(ranked_old[:n]) & set(ranked_new[:n]))
        print(f"\nTop {n}: {overlap} of {n} stay in the top {n}.")
    print(f"Kendall's tau between old and new ranking: {kendall_tau(ranked_old, ranked_new):.2f}")
    held = [r["candidate"] for r in rows if r["parse"] != "ok"]
    if held:
        print(f"Held for manual review (not ranked): {', '.join(held)}")
    movers = sorted((r for r in rows if r["old"] is not None), key=lambda r: -abs(r["new"] - r["old"]))[:5]
    if movers:
        print("\nBiggest movers:")
        for r in movers:
            print(f"  {r['candidate']}: {r['old']:.1f} -> {r['new']:.1f} "
                  f"(skills shown/listed/related/missing {r['shown/listed/related/missing']}; "
                  f"left out: {r['unknown'] or 'nothing'})")

    if args.csv:
        with open(args.csv, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nWrote {args.csv}")


if __name__ == "__main__":
    main()
