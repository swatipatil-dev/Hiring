"""
📦 BULK ACTIONS — preview, confirm, execute, audit

A bulk action is two requests. ``preview`` classifies every selected candidate
(eligible / skipped with a reason / warnings) and stores the batch as a
``bulk_operations`` row. ``execute`` runs it only when the caller echoes back the
eligible count they were shown — so nobody rejects 212 people believing it was 12 —
and only within PREVIEW_TTL, re-checking each candidate because the pipeline may
have moved on in between.

Every executed batch leaves: the bulk_operations row (who, what, per-candidate
outcome), a stage-history row per candidate naming the batch, and an audit_log row.
"""

import json
from datetime import datetime, timedelta

from fastapi import HTTPException

from src.models.models import ATSResult, BulkOperation, Candidate, CandidateStageHistory, Offer

ACTIONS = {
    "move_stage": "Move to stage",
    "shortlist": "Shortlist",
    "reject": "Reject",
    "add_note": "Add internal note",
}
MAX_BATCH = 500
PREVIEW_TTL = timedelta(minutes=15)
_LOCKED = {"hired", "onboarding"}
_NOT_A_TARGET = {"merged", "withdrawn"}   # set by a merge / by the candidate only


def _target_stage(db, company_id, action, params):
    from src.services.pipeline_stages import valid_stage_keys
    if action == "shortlist":
        return "shortlisted"
    if action == "reject":
        return "rejected"
    if action == "move_stage":
        target = (params.get("target_stage") or "").strip()
        if target in _NOT_A_TARGET or target not in valid_stage_keys(db, company_id):
            raise HTTPException(400, f"'{target}' is not a stage candidates can be moved to")
        return target
    return None


def _validate(db, company_id, action, candidate_ids, params):
    if action not in ACTIONS:
        raise HTTPException(400, f"action must be one of: {sorted(ACTIONS)}")
    ids = list(dict.fromkeys(str(i) for i in (candidate_ids or []) if i))
    if not ids:
        raise HTTPException(400, "Select at least one candidate")
    if len(ids) > MAX_BATCH:
        raise HTTPException(400, f"At most {MAX_BATCH} candidates per bulk action")
    if action == "add_note" and not (params.get("note") or "").strip():
        raise HTTPException(400, "A note is required")
    return ids, _target_stage(db, company_id, action, params)


def _classify(db, visible_query, ids, action, target):
    """(eligible [{id,name,from_stage}], skipped [{id,name,reason}], warnings [str])."""
    found = {c.id: c for c in visible_query.filter(Candidate.id.in_(ids)).all()}
    needs_review = {cid for (cid,) in db.query(ATSResult.candidate_id).filter(
        ATSResult.candidate_id.in_(list(found)), ATSResult.parse_status == "needs_review").all()} \
        if found and action in ("shortlist", "move_stage") else set()
    with_offer = {cid for (cid,) in db.query(Offer.candidate_id).filter(
        Offer.candidate_id.in_(list(found)), Offer.status.in_(["draft", "sent"])).all()} \
        if found and action in ("reject", "move_stage") else set()

    eligible, skipped = [], []
    review_n = offer_n = reopen_n = 0
    for cid in ids:
        c = found.get(cid)
        if c is None:
            skipped.append({"candidate_id": cid, "name": None, "reason": "Not found or not on your jobs"})
            continue
        name = f"{c.first_name} {c.last_name}".strip()
        stage = c.current_stage or ""
        reason = None
        if c.merged_into_id:
            reason = "Merged into another application"
        elif action != "add_note":
            if stage == "withdrawn":
                reason = "Withdrawn by the candidate"
            elif stage in _LOCKED:
                reason = f"Already {stage}"
            elif stage == target:
                reason = f"Already in {target.replace('_', ' ')}"
        if reason:
            skipped.append({"candidate_id": cid, "name": name, "reason": reason})
            continue
        eligible.append({"candidate_id": cid, "name": name, "from_stage": stage})
        review_n += cid in needs_review
        offer_n += cid in with_offer
        reopen_n += stage == "rejected" and target not in (None, "rejected")

    warnings = []
    if review_n:
        warnings.append(f"{review_n} candidate(s) have a resume flagged for manual review — "
                        "their ATS score may be unreliable.")
    if offer_n:
        warnings.append(f"{offer_n} candidate(s) have an outstanding offer.")
    if reopen_n:
        warnings.append(f"{reopen_n} candidate(s) are currently rejected and will be reopened.")
    return eligible, skipped, warnings


def preview_bulk(db, user, visible_query, action, candidate_ids, params=None):
    params = dict(params or {})
    ids, target = _validate(db, user.company_id, action, candidate_ids, params)
    eligible, skipped, warnings = _classify(db, visible_query, ids, action, target)
    op = BulkOperation(
        company_id=user.company_id, action=action,
        params=json.dumps({k: params.get(k) for k in ("target_stage", "note", "notify_candidates")
                           if params.get(k) is not None}),
        candidate_ids=json.dumps(ids),
        preview=json.dumps({"eligible": eligible, "skipped": skipped, "warnings": warnings,
                            "target_stage": target}),
        status="previewed", eligible_count=len(eligible),
        created_by_id=user.id, expires_at=datetime.utcnow() + PREVIEW_TTL,
    )
    db.add(op)
    db.flush()
    return op


def execute_bulk(db, user, op_id, confirm_count, visible_query, request=None):
    """Run a previewed batch. Returns (op, rejected_candidates) — the caller commits,
    then sends rejection emails, so no email goes out for a rolled-back change."""
    from src.services.audit_log import record_audit
    from src.services.stage_change import apply_stage_change

    op = db.query(BulkOperation).filter(
        BulkOperation.id == op_id, BulkOperation.company_id == user.company_id).first()
    if not op or op.created_by_id != user.id:
        raise HTTPException(404, "Bulk action not found")
    if op.status != "previewed":
        raise HTTPException(409, f"This bulk action is already {op.status}.")
    if op.expires_at and datetime.utcnow() > op.expires_at:
        raise HTTPException(410, "This preview has expired. Preview the action again.")
    if confirm_count is None or int(confirm_count) != op.eligible_count:
        raise HTTPException(400, f"Confirmation mismatch: this action applies to {op.eligible_count} "
                                 f"candidate(s). Send confirm_count={op.eligible_count} to proceed.")

    params = json.loads(op.params or "{}")
    preview = json.loads(op.preview or "{}")
    target = preview.get("target_stage")
    previewed_ids = [e["candidate_id"] for e in preview.get("eligible", [])]
    # Re-check: something may have changed since the preview.
    eligible, skipped, _ = _classify(db, visible_query, previewed_ids, op.action, target)
    now_ok = {e["candidate_id"] for e in eligible}
    candidates = {c.id: c for c in visible_query.filter(Candidate.id.in_(list(now_ok))).all()} \
        if now_ok else {}

    tag = f"Bulk {ACTIONS[op.action].lower()} (batch {op.id[:8]})"
    note = (params.get("note") or "").strip()
    outcomes, rejected = [], []
    for e in eligible:
        c = candidates.get(e["candidate_id"])
        if c is None:
            continue
        if op.action == "add_note":
            db.add(CandidateStageHistory(
                company_id=c.company_id, candidate_id=c.id, from_stage="__note__",
                to_stage="hr", changed_by_id=user.id, notes=f"{note} — {tag}"))
            outcomes.append({**e, "outcome": "note_added"})
            continue
        apply_stage_change(db, c, target, user.id, notes=f"{tag}" + (f": {note}" if note else ""))
        outcomes.append({**e, "outcome": "moved", "to_stage": target})
        if target == "rejected":
            rejected.append(c)
    for s in skipped:
        outcomes.append({**s, "outcome": "skipped", "reason": f"Changed since preview: {s['reason']}"})

    op.status = "executed"
    op.executed_by_id = user.id
    op.executed_at = datetime.utcnow()
    op.affected_count = sum(1 for o in outcomes if o["outcome"] != "skipped")
    op.result = json.dumps(outcomes)
    record_audit(db, action=f"hr.bulk.{op.action}", actor=user, company_id=user.company_id,
                 resource=f"bulk_operations/{op.id}", request=request,
                 detail={"target_stage": target, "affected": op.affected_count,
                         "skipped_since_preview": len(skipped)},
                 commit=False)
    if not params.get("notify_candidates", True):
        rejected = []
    return op, rejected


def cancel_bulk(db, user, op_id):
    op = db.query(BulkOperation).filter(
        BulkOperation.id == op_id, BulkOperation.company_id == user.company_id).first()
    if not op or op.created_by_id != user.id:
        raise HTTPException(404, "Bulk action not found")
    if op.status != "previewed":
        raise HTTPException(409, f"This bulk action is already {op.status}.")
    op.status = "cancelled"
    return op


def op_view(op, users=None, include_detail=True):
    users = users or {}
    creator = users.get(op.created_by_id)
    out = {
        "id": op.id,
        "action": op.action,
        "action_label": ACTIONS.get(op.action, op.action),
        "status": op.status,
        "params": json.loads(op.params or "{}"),
        "eligible_count": op.eligible_count,
        "affected_count": op.affected_count,
        "requested_count": len(json.loads(op.candidate_ids or "[]")),
        "created_by_id": op.created_by_id,
        "created_by_name": f"{creator.first_name} {creator.last_name}".strip() if creator else None,
        "created_at": op.created_at.isoformat() if op.created_at else None,
        "expires_at": op.expires_at.isoformat() if op.expires_at else None,
        "executed_at": op.executed_at.isoformat() if op.executed_at else None,
    }
    if include_detail:
        out["preview"] = json.loads(op.preview or "{}")
        out["result"] = json.loads(op.result) if op.result else None
    return out
