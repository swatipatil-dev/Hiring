"""Checking a ranking against independent human review.

The resume ranking decides who reaches a person. This measures how often it gets that
wrong, including below the shortlist cutoff, where no one would otherwise look:

- draw a seeded random sample of applicants BELOW the cutoff for a blind review (the
  reviewer sees the resume, not the score or rank);
- record each independent judgement (suitable / not suitable, with a reason), with the
  model's view at that moment snapshotted beside it;
- report agreement between the ranking and the reviewers, the share of suitable people
  the ranking put below the cutoff, an estimate of how many more there are, and every
  missed candidate with the cause found when it was investigated.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from datetime import datetime
from typing import Optional

JUDGEMENTS = ("suitable", "not_suitable")
CAUSES = ("parse_failure", "synonym_not_recognised", "experience_not_detected", "other")


def ranked_results(db, job):
    """[(candidate, ats_result, effective_score)] best first; needs_review ones last."""
    from src.models.models import ATSResult, Candidate
    from src.services.ats_scoring_pipeline import effective_score
    rows = (db.query(Candidate, ATSResult)
            .join(ATSResult, (ATSResult.candidate_id == Candidate.id) & (ATSResult.job_id == job.id))
            .filter(Candidate.job_id == job.id).all())
    ok = [(c, r, effective_score(r) or 0.0) for c, r in rows if r.parse_status != "needs_review"]
    held = [(c, r, effective_score(r) or 0.0) for c, r in rows if r.parse_status == "needs_review"]
    ok.sort(key=lambda t: -t[2])
    return ok, held


def split_by_cutoff(ok, held, cutoff_score: Optional[float] = None, top_n: Optional[int] = None):
    """(above, below) by score threshold or by top-N rank. needs_review results are never
    "above": they were not ranked, so they count as below for the purpose of review."""
    if top_n is not None:
        above, below = ok[:top_n], ok[top_n:]
    else:
        threshold = 50.0 if cutoff_score is None else cutoff_score
        above = [t for t in ok if t[2] >= threshold]
        below = [t for t in ok if t[2] < threshold]
    return above, below + held


def draw_sample(db, job, *, cutoff_score=None, top_n=None, size=10, seed=None,
                include_needs_review=True, reviewer_id=None):
    from src.models.models import ReviewSample, ScreeningReview
    ok, held = ranked_results(db, job)
    _, below = split_by_cutoff(ok, held if include_needs_review else [], cutoff_score, top_n)
    already = {cid for (cid,) in db.query(ScreeningReview.candidate_id).filter(
        ScreeningReview.job_id == job.id, ScreeningReview.reviewer_user_id == reviewer_id).all()}
    pool = sorted([c.id for c, _, _ in below if c.id not in already])
    seed = seed if seed is not None else random.SystemRandom().randint(1, 2**31 - 1)
    picked = random.Random(seed).sample(pool, min(size, len(pool)))
    sample = ReviewSample(
        company_id=job.company_id, job_id=job.id, created_by_id=reviewer_id,
        cutoff_type="top_n" if top_n is not None else "score",
        cutoff_value=float(top_n if top_n is not None else (50.0 if cutoff_score is None else cutoff_score)),
        size=len(picked), seed=seed, candidate_ids=json.dumps(picked),
        below_cutoff_population=len(below), created_at=datetime.utcnow(),
    )
    db.add(sample)
    db.flush()
    return sample


def record_review(db, job, candidate, *, judgement, reason, reviewer_id, sample_id=None):
    from src.models.models import ScreeningReview
    from src.services.enhanced_ats_service import SCORING_RULES_VERSION
    ok, held = ranked_results(db, job)
    rank = next((i + 1 for i, (c, _, _) in enumerate(ok) if c.id == candidate.id), None)
    row = next((r for c, r, _ in ok + held if c.id == candidate.id), None)
    from src.services.ats_scoring_pipeline import effective_score
    review = ScreeningReview(
        company_id=job.company_id, job_id=job.id, candidate_id=candidate.id,
        reviewer_user_id=reviewer_id, judgement=judgement, reason=reason,
        blind=sample_id is not None, sample_id=sample_id,
        model_score_at_review=float(row.ats_score) if row and row.ats_score is not None else None,
        effective_score_at_review=effective_score(row),
        rank_at_review=rank, parse_status_at_review=row.parse_status if row else None,
        scoring_rules_version=row.scoring_rules_version if row else SCORING_RULES_VERSION,
        jd_version_id=row.jd_version_id if row else None, created_at=datetime.utcnow(),
    )
    db.add(review)
    db.flush()
    return review


def _kappa(tp, fp, fn, tn) -> Optional[float]:
    n = tp + fp + fn + tn
    if not n:
        return None
    po = (tp + tn) / n
    pe = ((tp + fp) * (tp + fn) + (fn + tn) * (fp + tn)) / (n * n)
    return None if pe == 1 else round((po - pe) / (1 - pe), 3)


def suggested_cause(row) -> str:
    """A starting point for the investigation, from what the explanation shows."""
    if row is None or row.parse_status == "needs_review":
        return "parse_failure"
    try:
        ex = json.loads(row.explanation) if row.explanation else {}
    except (TypeError, ValueError):
        ex = {}
    if (ex.get("experience") or {}).get("relevant_status") == "unknown":
        return "experience_not_detected"
    items = (ex.get("mandatory") or {}).get("items") or []
    if any(i.get("level") == "related" for i in items):
        return "synonym_not_recognised"
    return "other"


def report(db, job, *, cutoff_score=None, top_n=None):
    from src.models.models import MissedCandidateInvestigation, ReviewSample, ScreeningReview
    ok, held = ranked_results(db, job)
    above, below = split_by_cutoff(ok, held, cutoff_score, top_n)
    above_ids = {c.id for c, _, _ in above}
    rows = {c.id: (c, r) for c, r, _ in ok + held}

    latest = {}
    for rv in db.query(ScreeningReview).filter(ScreeningReview.job_id == job.id).order_by(
            ScreeningReview.created_at).all():
        latest[rv.candidate_id] = rv            # latest judgement per candidate
    tp = fp = fn = tn = 0
    missed = []
    investigations = {}
    for inv in db.query(MissedCandidateInvestigation).filter(
            MissedCandidateInvestigation.job_id == job.id).order_by(MissedCandidateInvestigation.created_at).all():
        investigations[inv.candidate_id] = inv
    for cid, rv in latest.items():
        suitable = rv.judgement == "suitable"
        model_yes = cid in above_ids
        if suitable and model_yes:
            tp += 1
        elif not suitable and model_yes:
            fp += 1
        elif suitable and not model_yes:
            fn += 1
            cand, row = rows.get(cid, (None, None))
            inv = investigations.get(cid)
            missed.append({
                "candidate_id": cid,
                "name": f"{cand.first_name} {cand.last_name}".strip() if cand else cid,
                "score": rv.effective_score_at_review, "rank_at_review": rv.rank_at_review,
                "parse_status": rv.parse_status_at_review, "reviewer_reason": rv.reason,
                "blind": rv.blind, "suggested_cause": suggested_cause(row),
                "investigation": ({"cause": inv.cause, "notes": inv.notes,
                                   "at": inv.created_at.isoformat() if inv.created_at else None}
                                  if inv else None),
            })
        else:
            tn += 1

    reviewed = tp + fp + fn + tn
    suitable_total = tp + fn
    samples = db.query(ReviewSample).filter(ReviewSample.job_id == job.id).all()
    sampled_ids = {cid for s in samples for cid in json.loads(s.candidate_ids or "[]")}
    sampled_reviewed = [latest[c] for c in sampled_ids if c in latest]
    missed_in_samples = sum(1 for rv in sampled_reviewed if rv.judgement == "suitable" and rv.candidate_id not in above_ids)
    estimated_missed = (round(missed_in_samples / len(sampled_reviewed) * len(below))
                        if sampled_reviewed else None)
    return {
        "job_id": job.id,
        "cutoff": {"type": "top_n" if top_n is not None else "score",
                   "value": top_n if top_n is not None else (50.0 if cutoff_score is None else cutoff_score)},
        "population": {"above_cutoff": len(above), "below_cutoff": len(below), "not_ranked": len(held)},
        "reviewed": reviewed,
        "confusion": {"suitable_above": tp, "not_suitable_above": fp,
                      "suitable_below": fn, "not_suitable_below": tn},
        "agreement": round((tp + tn) / reviewed, 3) if reviewed else None,
        "kappa": _kappa(tp, fp, fn, tn),
        "miss_rate": round(fn / suitable_total, 3) if suitable_total else None,
        "precision_above": round(tp / (tp + fp), 3) if (tp + fp) else None,
        "blind_sample": {"reviewed": len(sampled_reviewed), "missed": missed_in_samples,
                         "estimated_missed_below_cutoff": estimated_missed},
        "missed": missed,
        "causes": dict(Counter(m["investigation"]["cause"] for m in missed if m["investigation"])),
    }
