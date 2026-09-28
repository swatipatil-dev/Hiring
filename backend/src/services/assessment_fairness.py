"""Accommodations and retests for candidate assessments.

One place for the rules the candidate pages, HR pages and the proctoring service all rely
on, so an adjustment HR approves is applied the same way everywhere:

- Accommodations live on the candidate's own Interview row (`accommodations` JSON,
  `accommodation_request`, `accommodation_status`). Only APPROVED ones take effect.
- Extra time scales that candidate's own question copies (the clock is the sum of their
  questions' `time_limit_minutes`). The original minutes are remembered so re-approving
  with a different percentage never compounds.
- Monitoring adjustments are enforced by the proctoring server, which reads them from the
  database at session start: never from the candidate's browser.
- A retest is a NEW Interview cloned from the same template, pointing back at the attempt
  it replaces; the earlier attempt and its proctoring record stay as they were.
"""
from __future__ import annotations

import json
import math
from typing import Optional

ACCOMMODATION_STATUSES = ("requested", "approved", "declined")
MAX_EXTRA_TIME_PERCENT = 200

# Flags an accommodation switches off. The proctoring server drops them before they are
# warned about, stored or scored.
GAZE_FLAGS = frozenset({"SUSPICIOUS_GAZE", "HEAD_TURNED", "HEAD_DOWN"})
AUDIO_FLAGS = frozenset({"MULTIPLE_VOICES", "BACKGROUND_NOISE", "FLAGGED_SPEECH", "MIC_MUTED"})


def parse_accommodations(raw) -> dict:
    if not raw:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError):
        return {}


def clean_accommodations(payload: dict) -> dict:
    """Validate and normalise the settings HR can grant. Unknown keys are dropped."""
    pct = int(payload.get("extra_time_percent") or 0)
    if pct < 0 or pct > MAX_EXTRA_TIME_PERCENT:
        raise ValueError(f"Extra time must be between 0 and {MAX_EXTRA_TIME_PERCENT}%")
    notes = (payload.get("notes") or "").strip()
    if len(notes) > 2000:
        raise ValueError("Notes are limited to 2000 characters")
    return {
        "extra_time_percent": pct,
        "audio_monitoring_off": bool(payload.get("audio_monitoring_off")),
        "gaze_flags_off": bool(payload.get("gaze_flags_off")),
        "fullscreen_not_required": bool(payload.get("fullscreen_not_required")),
        "notes": notes or None,
    }


def approved_accommodations(interview) -> dict:
    """The accommodations in force for this attempt, or {} if none are approved."""
    if not interview or interview.accommodation_status != "approved":
        return {}
    acc = parse_accommodations(interview.accommodations)
    return {k: v for k, v in acc.items() if not k.startswith("_")}


def suppressed_flags(acc: dict) -> frozenset:
    flags = set()
    if acc.get("gaze_flags_off"):
        flags |= GAZE_FLAGS
    if acc.get("audio_monitoring_off"):
        flags |= AUDIO_FLAGS
    return frozenset(flags)


def describe(acc: dict) -> list[str]:
    """Plain-language list of what an accommodation changes, for candidate and HR."""
    out = []
    if acc.get("extra_time_percent"):
        out.append(f"{acc['extra_time_percent']}% extra time")
    if acc.get("audio_monitoring_off"):
        out.append("Microphone monitoring off")
    if acc.get("gaze_flags_off"):
        out.append("Looking-away and gaze flags off")
    if acc.get("fullscreen_not_required"):
        out.append("Fullscreen not required")
    return out


def apply_extra_time(db, interview, percent: int) -> None:
    """Scale this attempt's question times by `percent`, from their original values.

    The originals are stored under `_base_minutes` in the accommodations JSON the first
    time, so approving 25% and later changing it to 50% gives 1.5x the original, not
    1.25 x 1.5. Rounds up: an adjustment never shortens a question.
    """
    from src.models.question_models import Question as InterviewQuestion

    acc = parse_accommodations(interview.accommodations)
    questions = db.query(InterviewQuestion).filter(InterviewQuestion.interview_id == interview.id).all()
    base = acc.get("_base_minutes") or {str(q.id): (q.time_limit_minutes or 5) for q in questions}
    acc["_base_minutes"] = base
    factor = 1 + (percent or 0) / 100
    total = 0
    for q in questions:
        minutes = int(base.get(str(q.id), q.time_limit_minutes or 5))
        q.time_limit_minutes = math.ceil(minutes * factor)
        total += q.time_limit_minutes
    if total:
        interview.duration_minutes = total
    interview.accommodations = json.dumps(acc)


def set_accommodations(db, interview, status: str, settings: Optional[dict]) -> None:
    """Record HR's decision. Approving applies extra time; declining restores times."""
    if status not in ("approved", "declined"):
        raise ValueError("status must be approved or declined")
    acc = parse_accommodations(interview.accommodations)
    if status == "approved":
        acc.update(clean_accommodations(settings or {}))
    interview.accommodations = json.dumps(acc)
    interview.accommodation_status = status
    pct = acc.get("extra_time_percent", 0) if status == "approved" else 0
    if pct or acc.get("_base_minutes"):
        apply_extra_time(db, interview, pct)


def source_template_id(interview) -> Optional[int]:
    try:
        cfg = json.loads(interview.interview_config_data or "{}")
        return int(cfg.get("_source_test_id")) if isinstance(cfg, dict) and cfg.get("_source_test_id") else None
    except (TypeError, ValueError):
        return None
