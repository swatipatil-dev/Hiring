"""
📝 SCORECARDS — independent panellist scorecards, blind until you submit

Every panellist on an interview (the lead in ``interviewer_email`` plus everyone in
``panel_members``) writes their own ``interview_scorecards`` row. The rules:

  * a draft is visible only to its author;
  * a submitted scorecard is locked — it cannot be edited;
  * a panellist sees nobody else's scores for an interview until they have submitted
    their own for it (the anchoring this prevents is the whole point of a panel);
  * HR and admins who are not on the panel see every submitted scorecard.

The same blindness applies to the consolidated ``InterviewFeedback`` row and to the
candidate-level views in advanced_features_routes, which used to show an interviewer
every other interviewer's scores for a candidate: see ``feedback_visibility``.
"""

import json
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException

from src.models.models import (
    InterviewFeedback, InterviewerProfile, InterviewScorecard, Notification,
)
from src.services.interview_panel import parse_panel

VALID_RECOMMENDATIONS = ("strong_hire", "hire", "proceed", "no_hire", "strong_no_hire")
SCORE_FIELDS = ("technical_score", "communication_score", "problem_solving_score", "cultural_fit_score")
_WEIGHTS = {"technical_score": 0.3, "communication_score": 0.2,
            "problem_solving_score": 0.3, "cultural_fit_score": 0.2}


def _email(e):
    return (e or "").strip().lower()


def panel_roster(interview):
    """[{name, email}] — the lead first, each person once."""
    roster, seen = [], set()
    lead = _email(interview.interviewer_email)
    if lead:
        roster.append({"name": interview.interviewer_name or interview.interviewer_email,
                       "email": interview.interviewer_email})
        seen.add(lead)
    for m in parse_panel(interview):
        e = _email(m.get("email"))
        if e and e not in seen:
            seen.add(e)
            roster.append({"name": m.get("name") or m.get("email"), "email": m.get("email")})
    return roster


def is_panelist(user, interview):
    return _email(user.email) in {_email(m["email"]) for m in panel_roster(interview)}


def _legacy_author_submitted(db, user, interview_ids):
    """Interview ids whose consolidated InterviewFeedback this user wrote (the lead's
    pre-scorecard path) — that counts as having submitted."""
    if not interview_ids:
        return set()
    return {iid for (iid,) in db.query(InterviewFeedback.interview_id)
            .join(InterviewerProfile, InterviewerProfile.id == InterviewFeedback.interviewer_id)
            .filter(InterviewFeedback.interview_id.in_(list(interview_ids)),
                    InterviewerProfile.user_id == user.id).all()}


def submitted_interview_ids(db, user, interview_ids):
    """Of ``interview_ids``, those this user has submitted a scorecard (or the
    consolidated feedback) for."""
    ids = list(interview_ids)
    if not ids:
        return set()
    done = {iid for (iid,) in db.query(InterviewScorecard.interview_id).filter(
        InterviewScorecard.interview_id.in_(ids),
        InterviewScorecard.panelist_user_id == user.id,
        InterviewScorecard.status == "submitted",
    ).all()}
    return done | _legacy_author_submitted(db, user, ids)


def feedback_visibility(db, user, interviews):
    """Ids of ``interviews`` (all for one candidate) whose scores ``user`` may see.

    * On the panel of an interview: visible once they have submitted for it.
    * Not on the panel: HR and admins see it. An interviewer sees it only once they
      have nothing pending on this candidate and have submitted at least once — so
      reading a colleague's earlier round can't colour a round still to be scored.
    """
    interviews = list(interviews)
    on_panel = {iv.id for iv in interviews if is_panelist(user, iv)}
    submitted = submitted_interview_ids(db, user, on_panel)
    live_panel = {iv.id for iv in interviews
                  if iv.id in on_panel and (iv.status or "").upper() != "CANCELLED"}
    pending = live_panel - submitted

    visible = set()
    for iv in interviews:
        if iv.id in on_panel:
            if iv.id in submitted:
                visible.add(iv.id)
        elif user.role in ("hr", "admin", "superadmin"):
            visible.add(iv.id)
        elif submitted and not pending:
            visible.add(iv.id)
    return visible


# ─── writing ─────────────────────────────────────────────────────────────────

def _score(value, field):
    if value is None or value == "":
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        raise HTTPException(400, f"{field} must be a number")
    if v < 0 or v > 100:
        raise HTTPException(400, f"{field} must be between 0 and 100 (or 0-10)")
    # Same convention as the existing feedback form: 0-10 is scaled to 0-100.
    return v * 10 if v <= 10 else v


def _list(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = [s.strip() for s in value.split("\n") if s.strip()]
    return json.dumps([str(s)[:500] for s in value][:20])


def _get_or_create(db, interview, user):
    card = db.query(InterviewScorecard).filter(
        InterviewScorecard.interview_id == interview.id,
        InterviewScorecard.panelist_user_id == user.id,
    ).first()
    if card is None:
        card = InterviewScorecard(
            company_id=interview.company_id, interview_id=interview.id, panelist_user_id=user.id,
            panelist_email=user.email,
            panelist_name=f"{user.first_name} {user.last_name}".strip(), status="draft",
        )
        db.add(card)
    return card


def save_scorecard(db, interview, user, data, submit=False):
    """Save the caller's draft, or submit it. The caller commits."""
    if not is_panelist(user, interview):
        raise HTTPException(403, "Only panellists on this interview can write a scorecard.")
    if (interview.status or "").upper() == "CANCELLED":
        raise HTTPException(409, "This interview was cancelled.")
    existing = db.query(InterviewScorecard).filter(
        InterviewScorecard.interview_id == interview.id,
        InterviewScorecard.panelist_user_id == user.id,
    ).first()
    if existing is not None and existing.status == "submitted":
        raise HTTPException(409, "Your scorecard is already submitted and locked.")

    # Validate everything before touching the row, so a refused request writes nothing.
    changes = {}
    for f in SCORE_FIELDS:
        if f in data:
            changes[f] = _score(data.get(f), f)
    for f in ("strengths", "weaknesses"):
        if f in data:
            changes[f] = _list(data.get(f))
    for f in ("recommendations", "notes"):
        if f in data:
            changes[f] = (data.get(f) or "").strip()[:5000] or None
    if "hire_recommendation" in data:
        rec = data.get("hire_recommendation") or None
        if rec is not None and rec not in VALID_RECOMMENDATIONS:
            raise HTTPException(400, f"hire_recommendation must be one of: {list(VALID_RECOMMENDATIONS)}")
        changes["hire_recommendation"] = rec

    def value(f):
        return changes[f] if f in changes else (getattr(existing, f) if existing else None)

    scores = {f: value(f) for f in SCORE_FIELDS}
    if submit:
        missing = [f for f in SCORE_FIELDS if scores[f] is None]
        if not value("hire_recommendation"):
            missing.append("hire_recommendation")
        if missing:
            raise HTTPException(400, {"message": "Complete the scorecard before submitting.",
                                      "missing": missing})

    card = existing or _get_or_create(db, interview, user)
    for f, v in changes.items():
        setattr(card, f, v)
    if all(v is not None for v in scores.values()):
        card.overall_score = round(sum(float(scores[f]) * w for f, w in _WEIGHTS.items()), 2)
    else:
        card.overall_score = None

    if submit:
        card.status = "submitted"
        card.submitted_at = datetime.utcnow()
        db.flush()
        _notify_if_panel_complete(db, interview)
    return card


def _notify_if_panel_complete(db, interview):
    roster = {_email(m["email"]) for m in panel_roster(interview)}
    if not roster:
        return
    done = {_email(e) for (e,) in db.query(InterviewScorecard.panelist_email).filter(
        InterviewScorecard.interview_id == interview.id,
        InterviewScorecard.status == "submitted").all()}
    if not roster <= done or interview.job is None:
        return
    from src.services.job_access import job_hr_user_ids
    cand = interview.candidate
    who = f"{cand.first_name} {cand.last_name}".strip() if cand else "the candidate"
    for uid in job_hr_user_ids(db, interview.job):
        db.add(Notification(
            user_id=uid, company_id=interview.company_id,
            title="All scorecards submitted",
            message=f"Every panellist has submitted a scorecard for {who} ({interview.job.title}).",
            type="scorecards_complete", reference_type="interview", reference_id=str(interview.id),
        ))


# ─── reading ─────────────────────────────────────────────────────────────────

def _f(v):
    return float(v) if isinstance(v, Decimal) else v


def _loads(v):
    try:
        return json.loads(v) if v else []
    except (TypeError, ValueError):
        return [v]


def card_view(card):
    return {
        "id": card.id,
        "panelist_user_id": card.panelist_user_id,
        "panelist_name": card.panelist_name,
        "panelist_email": card.panelist_email,
        "status": card.status,
        "submitted_at": card.submitted_at.isoformat() if card.submitted_at else None,
        "updated_at": card.updated_at.isoformat() if card.updated_at else None,
        **{f: _f(getattr(card, f)) for f in SCORE_FIELDS},
        "overall_score": _f(card.overall_score),
        "strengths": _loads(card.strengths),
        "weaknesses": _loads(card.weaknesses),
        "recommendations": card.recommendations,
        "hire_recommendation": card.hire_recommendation,
        "notes": card.notes,
    }


def scorecard_overview(db, interview, user):
    """What ``user`` may see of an interview's scorecards."""
    cards = db.query(InterviewScorecard).filter(
        InterviewScorecard.interview_id == interview.id).all()
    mine = next((c for c in cards if c.panelist_user_id == user.id), None)
    panelist = is_panelist(user, interview)
    submitted_self = bool(mine and mine.status == "submitted") or \
        bool(_legacy_author_submitted(db, user, [interview.id]))
    can_see_others = submitted_self if panelist else user.role in ("hr", "admin", "superadmin")

    by_email = {_email(c.panelist_email): c for c in cards}
    panel = []
    for m in panel_roster(interview):
        c = by_email.get(_email(m["email"]))
        panel.append({"name": m["name"], "email": m["email"],
                      "status": c.status if c else "not_started",
                      "submitted_at": c.submitted_at.isoformat() if c and c.submitted_at else None})

    others = None
    if can_see_others:
        others = [card_view(c) for c in cards
                  if c.status == "submitted" and c.panelist_user_id != user.id]
    submitted = [c for c in cards if c.status == "submitted"]
    summary = None
    if can_see_others and submitted:
        votes = {}
        for c in submitted:
            if c.hire_recommendation:
                votes[c.hire_recommendation] = votes.get(c.hire_recommendation, 0) + 1
        overall = [float(c.overall_score) for c in submitted if c.overall_score is not None]
        summary = {"submitted": len(submitted),
                   "average_overall": round(sum(overall) / len(overall), 1) if overall else None,
                   "recommendations": votes}
    return {
        "interview_id": interview.id,
        "is_panelist": panelist,
        "my_scorecard": card_view(mine) if mine else None,
        "can_edit": panelist and not submitted_self and (interview.status or "").upper() != "CANCELLED",
        "others_visible": can_see_others,
        "hidden_reason": None if can_see_others else
        "Other panellists' scorecards appear once you submit your own.",
        "panel": panel,
        "others": others,
        "summary": summary,
    }
