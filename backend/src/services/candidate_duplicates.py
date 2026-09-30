"""
🪞 CANDIDATE DUPLICATES — detection and controlled merging

Detection. The database already refuses a second application with the same email
for the same job (uq_candidates_company_job_email). What slips through is the same
person under a different email, or re-typed by HR. Two applications match on:

  email     normalised (case, and for Gmail the dots and +tag)      strong
  phone     the last 10 digits                                      strong
  linkedin  the profile path                                        strong
  name      same first + last name on the same job                  weak

Cross-job matches are reported as "same person, other job" for context; they are
separate applications, not duplicates, and cannot be merged.

Merging (same job only) is controlled:
  * preview first — shows every differing field, blockers and warnings;
  * HR picks, per field, which value survives;
  * the merged row is kept, marked ``merged_into_id`` and stage ``merged``, and hidden
    from pipeline lists — nothing is deleted;
  * the secondary's interviews and internal notes move to the primary;
  * a ``candidate_merges`` row snapshots both sides, so the merge can be undone.
"""

import json
import re
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException

from src.models.models import (
    Candidate, CandidateMerge, CandidateStageHistory, Interview, JobPosting, Offer,
)

# Fields HR may choose between. Email is not one: the primary keeps its email, because
# the secondary's email is already taken on this job by the secondary row itself (and
# a registered candidate is matched to their applications by email). To keep the other
# email, merge the other way round.
MERGE_FIELDS = [
    "first_name", "last_name", "phone", "linkedin_url", "github_url", "portfolio_url",
    "current_salary", "expected_salary", "notice_period", "experience_years",
    "source", "source_detail", "referrer_name", "referrer_email", "current_stage",
]
# Chosen as one unit: the resume file, its text and what was extracted from it.
RESUME_FIELDS = ["resume_path", "resume_text", "extracted_skills"]
_SNAPSHOT_FIELDS = MERGE_FIELDS + RESUME_FIELDS + ["merged_into_id", "user_id"]

_GMAIL = {"gmail.com", "googlemail.com"}


def normalize_email(email):
    email = (email or "").strip().lower()
    if "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    local = local.split("+", 1)[0]
    if domain in _GMAIL:
        local = local.replace(".", "")
        domain = "gmail.com"
    return f"{local}@{domain}" if local else None


def normalize_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 7 else None


def normalize_linkedin(url):
    url = (url or "").strip().lower()
    m = re.search(r"linkedin\.com/(in|pub)/([^/?#\s]+)", url)
    return f"{m.group(1)}/{m.group(2).rstrip('/')}" if m else None


def _norm_name(first, last):
    name = f"{first or ''} {last or ''}".strip().lower()
    return re.sub(r"\s+", " ", name) or None


def _keys(c):
    return {
        "email": normalize_email(c.email),
        "phone": normalize_phone(c.phone),
        "linkedin": normalize_linkedin(c.linkedin_url),
        "name": _norm_name(c.first_name, c.last_name),
    }


def _light_query(db, company_id, job_ids=None):
    q = db.query(Candidate.id, Candidate.job_id, Candidate.email, Candidate.phone,
                 Candidate.linkedin_url, Candidate.first_name, Candidate.last_name,
                 Candidate.current_stage, Candidate.created_at).filter(
        Candidate.company_id == company_id,
        Candidate.job_id.isnot(None),
        Candidate.merged_into_id.is_(None),
    )
    if job_ids is not None:
        q = q.filter(Candidate.job_id.in_(job_ids))
    return q


def _match_reasons(a, b):
    ka, kb = _keys(a), _keys(b)
    reasons = [k for k in ("email", "phone", "linkedin") if ka[k] and ka[k] == kb[k]]
    if not reasons and a.job_id == b.job_id and ka["name"] and ka["name"] == kb["name"]:
        reasons.append("name")
    return reasons


def _summary(c, job_titles):
    return {
        "candidate_id": c.id,
        "name": f"{c.first_name} {c.last_name}".strip(),
        "email": c.email,
        "phone": c.phone,
        "job_id": c.job_id,
        "job_title": job_titles.get(c.job_id),
        "current_stage": c.current_stage,
        "applied_at": c.created_at.isoformat() if c.created_at else None,
    }


def _job_titles(db, job_ids):
    if not job_ids:
        return {}
    return dict(db.query(JobPosting.id, JobPosting.title).filter(JobPosting.id.in_(list(job_ids))).all())


def find_duplicates_for(db, candidate, visible_job_ids=None):
    """Possible duplicates of one candidate, strongest first."""
    rows = [r for r in _light_query(db, candidate.company_id, visible_job_ids).all()
            if r.id != candidate.id]
    matches = []
    for r in rows:
        reasons = _match_reasons(candidate, r)
        if reasons:
            matches.append((r, reasons))
    titles = _job_titles(db, {r.job_id for r, _ in matches})
    out = []
    for r, reasons in matches:
        same_job = r.job_id == candidate.job_id
        out.append({
            **_summary(r, titles),
            "reasons": reasons,
            "confidence": "weak" if reasons == ["name"] else "strong",
            "same_job": same_job,
            "can_merge": same_job,
        })
    out.sort(key=lambda m: (not m["same_job"], m["confidence"] != "strong"))
    return out


def find_duplicate_groups(db, company_id, job_ids=None, include_cross_job=False, limit=200):
    """Groups of applications that look like the same person.

    Union-find over the match keys, so A~B by phone and B~C by email is one group.
    By default only same-job groups are returned — those are the ones to act on.
    """
    rows = _light_query(db, company_id, job_ids).all()
    parent = {r.id: r.id for r in rows}
    reasons = {}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b, why):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
        reasons.setdefault(a, set()).add(why)
        reasons.setdefault(b, set()).add(why)

    buckets = {}
    for r in rows:
        k = _keys(r)
        scope = "*" if include_cross_job else r.job_id
        for kind in ("email", "phone", "linkedin"):
            if k[kind]:
                buckets.setdefault((kind, scope, k[kind]), []).append(r.id)
        if k["name"]:
            buckets.setdefault(("name", r.job_id, k["name"]), []).append(r.id)
    for (kind, _scope, _val), ids in buckets.items():
        for other in ids[1:]:
            union(ids[0], other, kind)

    by_id = {r.id: r for r in rows}
    groups = {}
    for r in rows:
        root = find(r.id)
        groups.setdefault(root, []).append(r)
    titles = _job_titles(db, {r.job_id for r in rows})
    out = []
    for members in groups.values():
        if len(members) < 2:
            continue
        why = sorted(set().union(*(reasons.get(m.id, set()) for m in members)))
        same_job = len({m.job_id for m in members}) == 1
        members.sort(key=lambda m: m.created_at or datetime.min)
        out.append({
            "reasons": why,
            "confidence": "weak" if why == ["name"] else "strong",
            "same_job": same_job,
            "candidates": [_summary(by_id[m.id], titles) for m in members],
        })
    out.sort(key=lambda g: (g["confidence"] != "strong", -len(g["candidates"])))
    return out[:limit]


# ─── merging ─────────────────────────────────────────────────────────────────

def _val(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _snapshot(c):
    return {f: _val(getattr(c, f)) for f in _SNAPSHOT_FIELDS}


def _blockers_and_warnings(db, primary, secondary):
    blockers, warnings = [], []
    if primary.id == secondary.id:
        blockers.append("An application cannot be merged into itself.")
    if primary.company_id != secondary.company_id:
        blockers.append("Applications belong to different companies.")
    if primary.job_id != secondary.job_id:
        blockers.append("Only applications for the same job can be merged; "
                        "applications for different jobs are separate applications.")
    if primary.merged_into_id or secondary.merged_into_id:
        blockers.append("One of these applications has already been merged.")
    if db.query(Offer.id).filter(Offer.candidate_id == secondary.id).first():
        blockers.append("The application being merged away has an offer. "
                        "Merge the other way round, or withdraw the offer first.")
    if (secondary.current_stage or "") in ("hired", "onboarding"):
        blockers.append("The application being merged away is hired. Merge the other way round.")
    moving = db.query(Interview.id).filter(Interview.candidate_id == secondary.id).count()
    if moving:
        warnings.append(f"{moving} interview/test record(s) will move to the kept application.")
    if secondary.user_id and secondary.user_id != primary.user_id:
        warnings.append("The application being merged away belongs to a candidate account; "
                        "that account will see it as combined with the other application.")
    return blockers, warnings


def _load_pair(db, company_id, primary_id, secondary_id):
    rows = {c.id: c for c in db.query(Candidate).filter(
        Candidate.id.in_([primary_id, secondary_id]),
        Candidate.company_id == company_id,
    ).all()}
    if primary_id not in rows or secondary_id not in rows:
        raise HTTPException(404, "Candidate not found")
    return rows[primary_id], rows[secondary_id]


def preview_merge(db, company_id, primary_id, secondary_id):
    primary, secondary = _load_pair(db, company_id, primary_id, secondary_id)
    blockers, warnings = _blockers_and_warnings(db, primary, secondary)
    fields = []
    for f in MERGE_FIELDS:
        a, b = _val(getattr(primary, f)), _val(getattr(secondary, f))
        fields.append({"field": f, "primary": a, "merged": b, "differs": a != b,
                       "default": "primary" if a not in (None, "") or b in (None, "") else "merged"})
    resume_differs = (primary.resume_path or None) != (secondary.resume_path or None)
    fields.append({"field": "resume", "primary": primary.resume_path, "merged": secondary.resume_path,
                   "differs": resume_differs,
                   "default": "primary" if primary.resume_path or not secondary.resume_path else "merged"})
    return {
        "primary": _snapshot(primary) | {"id": primary.id, "email": primary.email},
        "merged": _snapshot(secondary) | {"id": secondary.id, "email": secondary.email},
        "fields": fields,
        "blockers": blockers,
        "warnings": warnings,
        "can_merge": not blockers,
    }


def merge_candidates(db, company_id, primary_id, secondary_id, choices, actor, reason=None):
    """Merge ``secondary`` into ``primary`` using ``choices`` ({field: "primary"|"merged"},
    missing fields default to the preview's default). The caller commits."""
    from src.services.stage_change import apply_stage_change

    primary, secondary = _load_pair(db, company_id, primary_id, secondary_id)
    blockers, _ = _blockers_and_warnings(db, primary, secondary)
    if blockers:
        raise HTTPException(409, {"message": "These applications cannot be merged.", "blockers": blockers})
    choices = dict(choices or {})
    unknown = set(choices) - set(MERGE_FIELDS) - {"resume"}
    if unknown:
        raise HTTPException(400, f"Unknown merge fields: {sorted(unknown)}")
    if any(v not in ("primary", "merged") for v in choices.values()):
        raise HTTPException(400, "Each choice must be 'primary' or 'merged'")

    defaults = {f["field"]: f["default"] for f in preview_merge(db, company_id, primary_id, secondary_id)["fields"]}
    resolved = {f: choices.get(f, defaults[f]) for f in defaults}

    primary_before, merged_before = _snapshot(primary), _snapshot(secondary)
    stage_before = primary.current_stage
    for f in MERGE_FIELDS:
        if f == "current_stage":
            continue
        if resolved[f] == "merged":
            setattr(primary, f, getattr(secondary, f))
    if resolved["resume"] == "merged":
        for f in RESUME_FIELDS:
            setattr(primary, f, getattr(secondary, f))
    if not primary.user_id and secondary.user_id:
        primary.user_id = secondary.user_id

    moved_interviews = [iv.id for iv in db.query(Interview).filter(
        Interview.candidate_id == secondary.id, Interview.company_id == company_id).all()]
    if moved_interviews:
        db.query(Interview).filter(Interview.id.in_(moved_interviews)) \
            .update({Interview.candidate_id: primary.id}, synchronize_session=False)
    moved_notes = [n.id for n in db.query(CandidateStageHistory).filter(
        CandidateStageHistory.candidate_id == secondary.id,
        CandidateStageHistory.from_stage == "__note__").all()]
    if moved_notes:
        db.query(CandidateStageHistory).filter(CandidateStageHistory.id.in_(moved_notes)) \
            .update({CandidateStageHistory.candidate_id: primary.id}, synchronize_session=False)

    record = CandidateMerge(
        company_id=company_id, primary_candidate_id=primary.id, merged_candidate_id=secondary.id,
        merged_by_id=actor.id, reason=(reason or "").strip()[:2000] or None,
        field_choices=json.dumps(resolved), primary_before=json.dumps(primary_before, default=str),
        merged_before=json.dumps(merged_before, default=str),
        moved=json.dumps({"interviews": moved_interviews, "notes": moved_notes}),
    )
    db.add(record)
    db.flush()

    name = f"{secondary.first_name} {secondary.last_name}".strip()
    if resolved["current_stage"] == "merged" and secondary.current_stage != stage_before:
        apply_stage_change(db, primary, secondary.current_stage, actor.id,
                           notes=f"Stage taken from merged application ({name})", notify=False)
    db.add(CandidateStageHistory(
        company_id=company_id, candidate_id=primary.id, from_stage="__note__",
        to_stage="hr", changed_by_id=actor.id,   # a note's to_stage is its visibility
        notes=f"Merged duplicate application {secondary.email} into this one (merge {record.id})."
              + (f" Reason: {record.reason}" if record.reason else ""),
    ))
    secondary.merged_into_id = primary.id
    apply_stage_change(db, secondary, "merged", actor.id,
                       notes=f"Merged into {primary.email} (merge {record.id})", notify=False)
    return record


def undo_merge(db, company_id, merge_id, actor):
    """Reverse a merge: restore both rows' fields, move interviews and notes back.
    Refused if the kept application has since been merged again. The caller commits."""
    from src.services.stage_change import apply_stage_change

    record = db.query(CandidateMerge).filter(
        CandidateMerge.id == merge_id, CandidateMerge.company_id == company_id).first()
    if not record:
        raise HTTPException(404, "Merge not found")
    if record.undone_at:
        raise HTTPException(409, "This merge has already been undone.")
    primary, secondary = _load_pair(db, company_id, record.primary_candidate_id,
                                    record.merged_candidate_id)
    if primary.merged_into_id:
        raise HTTPException(409, "The kept application has since been merged into another; undo that first.")
    if secondary.merged_into_id != primary.id:
        raise HTTPException(409, "The merged application has changed since the merge.")

    before = json.loads(record.primary_before or "{}")
    choices = json.loads(record.field_choices or "{}")
    for f in MERGE_FIELDS:
        if f != "current_stage" and choices.get(f) == "merged":
            setattr(primary, f, before.get(f))
    if choices.get("resume") == "merged":
        for f in RESUME_FIELDS:
            setattr(primary, f, before.get(f))
    if before.get("user_id") is None and primary.user_id == secondary.user_id:
        primary.user_id = None
    if choices.get("current_stage") == "merged" and before.get("current_stage") \
            and primary.current_stage != before["current_stage"]:
        apply_stage_change(db, primary, before["current_stage"], actor.id,
                           notes=f"Merge {record.id} undone", notify=False)

    moved = json.loads(record.moved or "{}")
    if moved.get("interviews"):
        db.query(Interview).filter(Interview.id.in_(moved["interviews"]),
                                   Interview.candidate_id == primary.id) \
            .update({Interview.candidate_id: secondary.id}, synchronize_session=False)
    if moved.get("notes"):
        db.query(CandidateStageHistory).filter(CandidateStageHistory.id.in_(moved["notes"])) \
            .update({CandidateStageHistory.candidate_id: secondary.id}, synchronize_session=False)

    merged_before = json.loads(record.merged_before or "{}")
    secondary.merged_into_id = None
    apply_stage_change(db, secondary, merged_before.get("current_stage") or "applied", actor.id,
                       notes=f"Merge {record.id} undone", notify=False)
    record.undone_at = datetime.utcnow()
    record.undone_by_id = actor.id
    return record


def merge_view(m):
    return {
        "id": m.id,
        "primary_candidate_id": m.primary_candidate_id,
        "merged_candidate_id": m.merged_candidate_id,
        "merged_by_id": m.merged_by_id,
        "reason": m.reason,
        "field_choices": json.loads(m.field_choices or "{}"),
        "moved": json.loads(m.moved or "{}"),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "undone_at": m.undone_at.isoformat() if m.undone_at else None,
        "undone_by_id": m.undone_by_id,
    }
