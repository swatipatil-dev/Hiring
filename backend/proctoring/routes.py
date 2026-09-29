"""
Proctoring API Routes — REST + WebSocket endpoints for the proctoring system.
Includes face-matching against the candidate's registered photo.
"""
import asyncio
import base64
import hmac
import json
import logging
import os
import secrets
import sys
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Dict, List, Optional, Union

def _log(msg):
    """Force-flush log to stderr so it appears in uvicorn output."""
    sys.stderr.write(f"{msg}\n")
    sys.stderr.flush()

import numpy as np
import cv2
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, HTTPException, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.config.database_config_mysql import get_db
from src.auth import get_current_user, require_role

from .config import ProctorConfig
from .runtime import inference_slots

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/proctor", tags=["Proctoring"])

# Active proctoring sessions (pipeline objects keyed by session_id)
#
# PROCESS-LOCAL BY NECESSITY: the values hold live MediaPipe graph handles, which
# cannot be serialised to Redis or shared between workers. A session's WebSocket must
# therefore reach the same process that started it — run proctoring as a SINGLE
# gunicorn worker, on its own service if the rest of the API needs more.
_sessions: Dict[str, dict] = {}  # {session_id: {"pipeline": ..., "db_session_id": ..., "candidate_id": ...}}

# Ceiling on concurrent sessions. Sessions are cheap in memory now that models are
# pooled, so this is really a CPU bound: past the point where frames queue for an
# inference slot longer than the sampling interval, everyone's proctoring degrades
# together. Budget roughly 1.5 sessions per inference slot, then calibrate against
# `peak_in_use` and `waits` from GET /pool-stats under real load.
MAX_SESSIONS = int(os.getenv("PROCTOR_MAX_SESSIONS", "0")) or max(
    4, int(inference_slots() * 1.5)
)


# ---------- Pydantic models ----------

class MonitoringConsent(BaseModel):
    """The candidate agreed to the monitoring disclosure on the instructions page."""
    accepted_at: str                   # ISO timestamp from the candidate's browser
    disclosure_version: str            # which wording they saw (TestInstructions.jsx)


class StartSessionRequest(BaseModel):
    # Ignored: the session belongs to the authenticated candidate. Still accepted so
    # an older client that sends it keeps working.
    candidate_id: Optional[str] = None
    test_id: Union[int, str]   # accepts string from URL params, stored as int FK
    interview_id: Optional[int] = None
    monitoring_consent: Optional[MonitoringConsent] = None

    @property
    def test_id_int(self) -> int:
        return int(self.test_id)


class StartSessionResponse(BaseModel):
    session_id: str
    ws_video_url: str
    ws_audio_url: str
    # Approved accommodation: the client should not open the audio stream at all.
    audio_monitoring_off: bool = False
    # No ws_behavioral_url: the behavioral engine is disabled (see
    # core/behavioral_engine.py), so advertising a stream nothing consumes only
    # invites a client to feed a module that would manufacture violations. The
    # WS handler itself is kept as the on-ramp if the feature is revived.
    status: str
    face_registered: bool
    # True while the stored reference photo is still being loaded/registered in the
    # background. The client must NOT fall back to live self-enrollment until this
    # clears, or every candidate would enroll their own face and identity verification
    # would pass unconditionally.
    enrollment_pending: bool = False


class SessionStatusResponse(BaseModel):
    session_id: str
    status: str
    cheating_score: float
    frames_processed: int
    avg_cheating_score: float
    max_cheating_score: float
    face_match_score: float
    flags: list
    # Modules that failed to initialise (objects/audio/pose/behavioral degrade silently),
    # and which face engine is live — so a half-blind session is visible, not inferred.
    degraded_modules: list = []
    face_engine: str = "unknown"


# ---------- Helpers ----------

@contextmanager
def _db_session():
    """
    Get a DB session for proctoring persistence, and *return it to the pool*.

    The previous `next(get_db())` pulled the session out of the FastAPI dependency
    generator without ever advancing it, so the generator's `finally: db.close()` never
    ran and every proctoring write leaked a pooled connection. With violations and score
    updates firing every few seconds per session, the 20+30 pool drained within minutes;
    checkout then blocked for pool_timeout on whatever thread asked next, which is what
    made the live face-match feed stall progressively during a test.
    """
    from src.config.database_config_mysql import db_manager
    db = db_manager.get_db_session()
    try:
        yield db
    finally:
        try:
            db.close()
        except Exception:
            pass


def _imread_stored(path: str) -> Optional[np.ndarray]:
    """Load an image that may live on local disk OR in the GCS bucket.

    `path` is a storage key (e.g. "uploads/photos/x.jpg"). On the local backend
    it's a real path; on GCS it's an object name. `as_local_path` downloads GCS
    objects to a temp file so cv2.imread (which needs a real path) works either way.
    """
    if not path:
        return None
    from src.services.storage import storage, as_local_path
    try:
        if not (os.path.exists(path) or storage.exists(path)):
            _log(f"[PHOTO] Not found on local disk or storage backend: {path}")
            return None
        with as_local_path(path) as local_path:
            return cv2.imread(local_path)
    except Exception as e:
        _log(f"[PHOTO] read error for {path}: {e}")
        return None


def _load_candidate_photo(candidate_id: str, db, interview_id: int = None) -> Optional[np.ndarray]:
    """Load the candidate's registration photo from DB path and return as BGR frame.

    Uses interview_id to find the exact candidate record (specific job application)
    so the correct photo is matched even if the user applied to multiple jobs.
    Falls back to searching all candidate records by user_id.
    """
    from src.models.models import Candidate, Interview

    # Try to get the exact candidate record via interview → candidate_id
    if interview_id:
        try:
            interview = db.query(Interview).filter(Interview.id == interview_id).first()
            _log(f"[PHOTO] Interview lookup: id={interview_id}, found={interview is not None}")
            if interview:
                _log(f"[PHOTO] Interview candidate_id={interview.candidate_id}, job_id={interview.job_id}")
            if interview and interview.candidate_id:
                cand = db.query(Candidate).filter(Candidate.id == interview.candidate_id).first()
                if cand:
                    _log(f"[PHOTO] Candidate found: id={cand.id}, photo_path={cand.photo_path}")
                    photo = cand.photo_path.replace("\\", "/") if cand.photo_path else None
                    if photo:
                        img = _imread_stored(photo)
                        if img is not None:
                            _log(f"[PHOTO] Loaded via interview: shape={img.shape}")
                            return img
                        else:
                            _log(f"[PHOTO] Could not load photo at {photo}")
                else:
                    _log(f"[PHOTO] No candidate record for candidate_id={interview.candidate_id}")
        except Exception as e:
            _log(f"[PHOTO] Interview lookup error: {e}")

    # Fallback: search all candidate records for this user, prefer most recent with photo
    candidates = db.query(Candidate).filter(
        Candidate.user_id == candidate_id,
        Candidate.photo_path.isnot(None)
    ).order_by(Candidate.created_at.desc()).all()
    _log(f"[PHOTO] Fallback: found {len(candidates)} candidate records with photos for user {candidate_id}")
    for cand in candidates:
        photo = cand.photo_path.replace("\\", "/") if cand.photo_path else None
        if photo:
            _log(f"[PHOTO] Checking: {photo}")
            img = _imread_stored(photo)
            if img is not None:
                _log(f"[PHOTO] Loaded via fallback: shape={img.shape}")
                return img
    _log(f"[PHOTO] WARNING: No reference photo found for candidate {candidate_id}")
    return None


def _persist_violation(db_session_id: str, violation_type: str, severity: str,
                       module: str, score_impact: float, details: dict = None,
                       evidence_path: str = None):
    """Write a violation record to the database."""
    try:
        with _db_session() as db:
            from .models import ProctoringViolation, ProctoringSession
            sess = db.query(ProctoringSession).filter(ProctoringSession.id == db_session_id).first()
            violation = ProctoringViolation(
                company_id=(sess.company_id if sess is not None else None),
                session_id=db_session_id,
                violation_type=violation_type,
                severity=severity,
                module=module,
                score_impact=score_impact,
                details=details,
                evidence_path=evidence_path,
            )
            db.add(violation)
            db.commit()
    except Exception as e:
        logger.error("Failed to persist violation: %s", e)


# Keeps strong refs to in-flight background writes so they aren't garbage-collected
# mid-flight (asyncio only holds a weak reference to running tasks).
_bg_writes: set = set()


def _fire_and_forget(fn, *fargs, **fkwargs):
    """
    Run a blocking DB/storage write off the event loop.

    These writes are audit-trail only — nothing in the frame response depends on them.
    Calling them inline blocked the loop for the duration of a MySQL commit (and, on the
    GCS backend, a whole network upload for the evidence JPEG), which stalled frame
    analysis for every session on the worker. Worst of all it fired hardest during a
    sustained IDENTITY_MISMATCH, i.e. exactly when the live face-match readout needs to
    stay responsive.
    """
    async def _runner():
        try:
            await asyncio.to_thread(fn, *fargs, **fkwargs)
        except Exception as e:
            logger.error("Background proctor write failed (%s): %s", getattr(fn, "__name__", fn), e)

    task = asyncio.create_task(_runner())
    _bg_writes.add(task)
    task.add_done_callback(_bg_writes.discard)


def _approved_accommodations_for(interview_id) -> dict:
    """Approved accommodations for this attempt, or {} (none, or not found)."""
    try:
        from src.services.assessment_fairness import approved_accommodations
        with _db_session() as db:
            from src.models.models import Interview
            iv = db.query(Interview).filter(Interview.id == int(interview_id)).first()
            return approved_accommodations(iv)
    except Exception as e:
        logger.error("Could not read accommodations for interview %s: %s", interview_id, e)
        return {}


def _save_evidence(frame, evidence_path: str):
    """Encode + upload an evidence frame. Runs in a worker thread."""
    try:
        from src.services.storage import storage
        ok, buf = cv2.imencode(".jpg", frame)
        if not ok:
            # The violation row is written with this evidence_path regardless of
            # whether the upload succeeded, so a silent failure here leaves HR
            # looking at a broken image with nothing explaining it.
            logger.error(
                "Evidence NOT saved (cv2.imencode returned False) for %s — the "
                "violation row will reference a file that does not exist",
                evidence_path,
            )
            return
        storage.save_bytes(buf.tobytes(), evidence_path)
    except Exception as ev_err:
        logger.error("Failed to save face evidence %s: %s", evidence_path, ev_err)


def _update_session_scores(db_session_id: str, result):
    """Update live scores on the proctoring session record."""
    try:
        with _db_session() as db:
            from .models import ProctoringSession
            session = db.query(ProctoringSession).filter(ProctoringSession.id == db_session_id).first()
            if session:
                session.cheating_score = result.cheating_score
                session.face_score = result.module_scores.get("face", 0)
                session.object_score = result.module_scores.get("object", 0)
                session.audio_score = result.module_scores.get("audio", 0)
                session.pose_score = result.module_scores.get("pose", 0)
                session.behavioral_score = result.module_scores.get("behavioral", 0)
                db.commit()
    except Exception as e:
        logger.error("Failed to update session scores: %s", e)


# Rows written to proctoring_violations that are NOT flags: they record facts about the
# session (how identity was established, that the candidate consented). Excluded from
# every flag count and from the event timeline.
from src.models.models import PROCTORING_MARKER_TYPES as MARKER_VIOLATION_TYPES  # noqa: E402

# Severity mapping for flags
FLAG_SEVERITY = {
    "NO_FACE": "HIGH", "MULTIPLE_FACES": "CRITICAL", "IDENTITY_MISMATCH": "CRITICAL",
    "HEAD_TURNED": "MEDIUM", "SUSPICIOUS_GAZE": "LOW",
    "PHONE_DETECTED": "CRITICAL", "PHONE_GESTURE": "HIGH",
    # MULTIPLE_VOICES is MEDIUM, not HIGH: the diarizer that raises it is new and
    # unproven against real rooms, and HIGH would put it in the warn-and-terminate path.
    # Recorded for HR with an evidence frame; promote once the false-positive rate is known.
    "MULTIPLE_VOICES": "MEDIUM", "FLAGGED_SPEECH": "MEDIUM", "MIC_MUTED": "LOW",
    "BACKGROUND_NOISE": "MEDIUM",
    "CANDIDATE_LEFT": "CRITICAL", "LEANING_FORWARD": "LOW", "HEAD_DOWN": "LOW",
    "LEANING_SIDEWAYS": "LOW",
    "BEHAVIORAL_ANOMALY": "MEDIUM",
    # MEDIUM: recorded for the reviewer, never a strike (see the termination policy below).
    "TAB_SWITCH": "MEDIUM",
    # Leaving fullscreen is the same act as switching tabs, reached by a different key,
    # so it carries the same weight. FULLSCREEN_UNAVAILABLE is not a violation at all -
    # it records that the browser had no fullscreen API (iOS Safari has none), so HR can
    # tell an unmonitored run from a clean one rather than reading silence as a pass.
    "FULLSCREEN_EXIT": "MEDIUM",
    "FULLSCREEN_UNAVAILABLE": "LOW",
    # Objects YOLO can see besides a phone. MEDIUM, not HIGH: they are worth showing HR
    # with the annotated frame, but they are single-model calls with no second
    # confirmation, so they must not feed the warn-and-terminate policy.
    # PERSON_DETECTED especially — it is the *unconfirmed* signal; MULTIPLE_FACES is the
    # face-confirmed one that carries the strike.
    "PERSON_DETECTED": "MEDIUM", "BOOK_DETECTED": "MEDIUM",
    "TABLET_DETECTED": "MEDIUM", "REMOTE_DETECTED": "MEDIUM",
}

FLAG_MODULE = {
    "NO_FACE": "face", "MULTIPLE_FACES": "face", "IDENTITY_MISMATCH": "face",
    "HEAD_TURNED": "face", "SUSPICIOUS_GAZE": "face",
    "PHONE_DETECTED": "object", "PHONE_GESTURE": "pose",
    "MULTIPLE_VOICES": "audio", "FLAGGED_SPEECH": "audio", "MIC_MUTED": "audio",
    "BACKGROUND_NOISE": "audio",
    "CANDIDATE_LEFT": "pose", "LEANING_FORWARD": "pose", "HEAD_DOWN": "pose",
    "LEANING_SIDEWAYS": "pose",
    "BEHAVIORAL_ANOMALY": "behavioral",
    "TAB_SWITCH": "behavioral",
    "FULLSCREEN_EXIT": "behavioral",
    "FULLSCREEN_UNAVAILABLE": "behavioral",
    "PERSON_DETECTED": "object", "BOOK_DETECTED": "object",
    "TABLET_DETECTED": "object", "REMOTE_DETECTED": "object",
}


def _flag_severity(flag: str) -> str:
    """Severity for a flag, defaulting unknown object detections to MEDIUM.

    ObjectConfig.targets is a config dict anyone can extend, and the detector derives its
    flag name from the COCO label (f"{label.upper()}_DETECTED"). Without this fallback a
    newly added target would be detected, scored, warned about — and then silently
    dropped from HR's report for want of a FLAG_SEVERITY entry.
    """
    if flag in FLAG_SEVERITY:
        return FLAG_SEVERITY[flag]
    return "MEDIUM" if flag.endswith("_DETECTED") else "LOW"


def _flag_module(flag: str) -> str:
    return FLAG_MODULE.get(flag, "object" if flag.endswith("_DETECTED") else "unknown")


def _observed_measurements(module: str, result, face_data: dict) -> dict:
    """Raw readings from the module that raised the flag, as distinct from the flag itself.

    The flag name, severity and score are the pipeline's interpretation; these are what
    the sensor actually measured on that frame. HR's report shows the two side by side so
    a reviewer can judge "2 faces at 0.91 similarity" instead of taking "MULTIPLE_FACES"
    on trust.
    """
    if module == "face":
        return {
            "face_detected": face_data.get("face_detected"),
            "face_count": face_data.get("face_count"),
            "yaw_deg": round(face_data.get("yaw") or 0.0, 1),
            "pitch_deg": round(face_data.get("pitch") or 0.0, 1),
            "gaze_direction": face_data.get("gaze_direction"),
            "identity_match": face_data.get("identity_match"),
            "has_reference": face_data.get("has_reference"),
        }
    if module == "object":
        obj = result.object_result or {}
        return {
            "objects": [
                {"label": o.get("label"), "confidence": o.get("confidence")}
                for o in obj.get("objects") or []
            ],
            "person_count": obj.get("person_count"),
        }
    if module == "pose":
        pose = result.pose_result or {}
        return {
            "phone_gesture_confidence": round(pose.get("phone_gesture_confidence") or 0.0, 3),
            "visibility_score": round(pose.get("visibility_score") or 0.0, 3),
            "posture_deviation": round(pose.get("posture_deviation") or 0.0, 3),
            "person_count": pose.get("person_count"),
        }
    if module == "behavioral":
        beh = result.behavioral_result or {}
        return {
            "anomaly_score": round(beh.get("anomaly_score") or 0.0, 3),
            "top_contributing_features": beh.get("top_contributing_features") or [],
        }
    return {}


def _violation_details(flag: str, frame_number: int, result, face_data: dict) -> dict:
    """What HR sees under a violation row. Audio flags carry their measurements."""
    details = {
        "frame": frame_number,
        "cheating_score": result.cheating_score,
        "similarity": face_data.get("similarity_score"),
    }
    observed = _observed_measurements(_flag_module(flag), result, face_data)
    if observed:
        details["observed"] = observed
    if _flag_module(flag) == "audio":
        audio = result.audio_result or {}
        details.update({
            # dBFS (relative to digital full scale), NOT calibrated acoustic dB SPL.
            "spl_dbfs": round(audio.get("spl_dbfs", -120.0), 1),
            "peak_dbfs": round(audio.get("peak_dbfs", -120.0), 1),
            "noise_floor_dbfs": round(audio.get("noise_floor_dbfs", -120.0), 1),
            "voice_count": audio.get("voice_count_estimate", 0),
            "speaker_distances": audio.get("speaker_distances", []),
            "speaker_enrolled": audio.get("speaker_enrolled", False),
        })
    return details

# MEDIUM flags recur every frame while they last (a head-turn easily spans minutes), so
# they are recorded on a clock rather than per episode — enough for HR to see the pattern
# without writing a row per frame.
_MEDIUM_PERSIST_INTERVAL_SEC = 10.0

# Termination policy: a test ends automatically ONLY on strong evidence that someone else
# is involved — a second face or a phone in view (each after TERMINATE_STRIKES warnings),
# or a face that keeps not matching the reference for IDENTITY_MISMATCH_STRIKE_SEC.
#
# Everything else — leaving the frame (NO_FACE, CANDIDATE_LEFT), phone-like gestures from
# the pose model, gaze and head turns, background audio, tab switches, leaving fullscreen —
# is recorded for a human reviewer and warned about, but never ends the test. Those are
# single-model guesses or ordinary behaviour (a candidate glancing at notes they are
# allowed, a sneeze, a notification that stole focus), and ending a hiring test on them
# is an automated adverse decision with no one checking it.
#
# These flags strike once per continuous EPISODE (rising edge); IDENTITY_MISMATCH is
# duration-based below.
_TERMINATING_EPISODE_FLAGS = {"MULTIPLE_FACES", "PHONE_DETECTED"}
_TERMINATING_FLAGS = _TERMINATING_EPISODE_FLAGS | {"IDENTITY_MISMATCH"}

# Candidate-facing reason text shown when the test auto-terminates.
_TERMINATE_MESSAGES = {
    "IDENTITY_MISMATCH": "The person on camera did not match the registered candidate.",
    "MULTIPLE_FACES": "More than one person was detected repeatedly.",
    "PHONE_DETECTED": "A phone was detected repeatedly.",
}


def _mark_session_terminated(entry: dict, reason: str) -> bool:
    """Mark a session terminated in memory + DB (sync). Returns False if already terminated.

    Terminated is a STATUS, not a verdict: it records that the test ended early, and the
    session goes to a human reviewer. No decision is written here — the system used to
    stamp decision="CHEATING" on every terminated session, a conclusion nobody had drawn.
    """
    if entry.get("terminated"):
        return False
    entry["terminated"] = True
    db_session_id = entry.get("db_session_id")
    if db_session_id:
        _persist_violation(
            db_session_id, "TEST_TERMINATED", "CRITICAL", "proctor", 100,
            details={"reason": reason, "strikes": dict(entry.get("strikes", {}))},
        )
        try:
            with _db_session() as db:
                from .models import ProctoringSession
                s = db.query(ProctoringSession).filter(ProctoringSession.id == db_session_id).first()
                if s:
                    s.status = "terminated"
                    s.ended_at = datetime.utcnow()
                    db.commit()
        except Exception as e:
            logger.error("Failed to mark session terminated: %s", e)
    return True


async def _terminate_session(session_id: str, entry: dict, ws, reason: str):
    """End a proctored test due to repeated serious violations (auto-submit happens client-side)."""
    if not _mark_session_terminated(entry, reason):
        return
    try:
        await ws.send_json({
            "event": "terminate", "reason": reason,
            "message": _TERMINATE_MESSAGES.get(reason, "Proctoring violation limit reached."),
        })
    except Exception:
        pass
    logger.warning("Proctoring session %s… TERMINATED: %s", _log_sid(session_id), reason)


async def _evaluate_strikes(session_id: str, entry: dict, result, ws):
    """Warnings for every flag; termination only for _TERMINATING_FLAGS (see above).

    Terminating flags get numbered strike warnings (3 warnings, then the test ends).
    All other flags get a throttled, un-numbered warning — they are recorded for review
    and never count toward ending the test.
    """
    cfg = entry["pipeline"].cfg
    now = time.time()
    # Grace window at session start (camera warm-up / live enrollment).
    if now - entry.get("session_started_at", now) < cfg.TERMINATION_GRACE_SEC:
        return

    flags = set(result.flags or [])
    strikes = entry["strikes"]
    active = entry["active_serious"]

    async def _strike(flag: str):
        n = strikes.get(flag, 0) + 1
        strikes[flag] = n
        if n > cfg.TERMINATE_STRIKES:
            await _terminate_session(session_id, entry, ws, flag)
        else:
            try:
                await ws.send_json({"event": "warning", "severity": "serious",
                                    "flag": flag, "strike": n, "max": cfg.TERMINATE_STRIKES})
            except Exception:
                pass

    # ── Duration-based: a face that keeps not matching the reference ──
    # (NO_FACE used to strike here too. Leaving the frame is now a warning only.)
    if "IDENTITY_MISMATCH" in flags:
        entry["mismatch_since"] = entry["mismatch_since"] or now
        if now - entry["mismatch_since"] >= cfg.IDENTITY_MISMATCH_STRIKE_SEC:
            entry["mismatch_since"] = None
            await _strike("IDENTITY_MISMATCH")
            if entry["terminated"]:
                return
    else:
        entry["mismatch_since"] = None

    # ── Episode (rising-edge) terminating flags ──
    for flag in _TERMINATING_EPISODE_FLAGS:
        if flag in flags and flag not in active:
            active.add(flag)
            await _strike(flag)
            if entry["terminated"]:
                return
        elif flag not in flags:
            active.discard(flag)

    # ── Everything else: warning only, throttled ~1/sec, never a strike ──
    minor = [f for f in flags if f not in _TERMINATING_FLAGS]
    if minor and now - entry.get("last_minor_warn", 0.0) >= 1.0:
        entry["last_minor_warn"] = now
        try:
            await ws.send_json({"event": "warning", "severity": "minor", "flags": minor})
        except Exception:
            pass


# ---------- Access ----------
#
# A session belongs to the candidate who started it. Every per-session endpoint and
# WebSocket checks that, so knowing a session id is not enough to stop someone's test,
# inject violations into their report, enroll a different face or read their logs. The
# WebSockets can't carry the Bearer header from a browser, so start_session hands the
# owner a random per-session token inside ws_video_url / ws_audio_url.

_CANDIDATE = require_role(["candidate"])
_STAFF_READ_ROLES = ("hr", "admin", "interviewer")


def _log_sid(session_id: str) -> str:
    """Enough of a session id to correlate log lines, never enough to use one."""
    return str(session_id)[:8]


def _owned_entry(session_id: str, user) -> dict:
    """The live session if `user` started it; 404 otherwise (same as not existing)."""
    entry = _sessions.get(session_id)
    owner = entry.get("owner_user_id") if entry else None
    if not owner or str(owner) != str(getattr(user, "id", "")):
        raise HTTPException(404, "Session not found")
    return entry


def _readable_entry(session_id: str, user) -> dict:
    """The live session for its owner, or for staff of the interview's company."""
    entry = _sessions.get(session_id)
    if entry:
        if entry.get("owner_user_id") and str(entry["owner_user_id"]) == str(user.id):
            return entry
        role = (getattr(user, "role", "") or "").lower()
        if role == "superadmin":
            return entry
        if (role in _STAFF_READ_ROLES and entry.get("company_id")
                and str(entry["company_id"]) == str(user.company_id)):
            return entry
    raise HTTPException(404, "Session not found")


def _ws_authorized(ws: WebSocket, entry: dict) -> bool:
    expected = entry.get("ws_token")
    supplied = ws.query_params.get("token") or ""
    return bool(expected) and hmac.compare_digest(str(supplied), str(expected))


def _owned_interview_company(user, interview_id: int):
    """(found, company_id) for an interview this candidate owns. Blocking."""
    from src.services.interview_access import candidate_owns_interview
    with _db_session() as db:
        iv = candidate_owns_interview(db, user, interview_id)
        return (iv is not None, iv.company_id if iv is not None else None)


# ---------- REST Endpoints ----------

@router.post("/warmup")
async def warmup(current_user=Depends(get_current_user)):
    """
    Build the shared inference models ahead of time.

    Called from the pre-test instructions screen: the candidate spends 10-30s reading the
    consent text, which is more than enough to build the model sets (~0.6s each once the
    imports are warm, much longer if they are still cold). By the time they click
    "Start test" the pool is ready and session start is a cheap object construction.

    Returns immediately — `ensure_pool` only submits to the builder thread. The response
    doubles as the capacity readout: `capacity`, `in_use`, `peak_in_use` and `waits` are
    what to size PROCTOR_INFERENCE_SLOTS and PROCTOR_MAX_SESSIONS against.
    """
    from .warmup import ensure_pool, pool_stats
    ensure_pool()
    stats = pool_stats()
    stats["active_sessions"] = len(_sessions)
    stats["max_sessions"] = MAX_SESSIONS
    return stats


def _create_db_session(req: "StartSessionRequest") -> Optional[int]:
    """Insert the ProctoringSession row. Blocking — call via asyncio.to_thread.

    Derives the tenant (company_id) from the interview (or the candidate user): the
    session is TenantScoped and HR/interviewer review is company-filtered, so without
    it the session (and its evidence frames) is invisible to reviewers.
    """
    try:
        with _db_session() as db:
            from .models import ProctoringSession
            from src.models.models import Interview, User
            company_id = None
            iv = db.query(Interview).filter(Interview.id == req.test_id_int).first()
            if iv is not None:
                company_id = getattr(iv, "company_id", None)
            if not company_id:
                u = db.query(User).filter(User.id == req.candidate_id).first()
                if u is not None:
                    company_id = getattr(u, "company_id", None)
            db_record = ProctoringSession(
                company_id=company_id,
                candidate_id=req.candidate_id,
                test_id=req.test_id_int,
                status="active",
                face_registered=False,  # set later by _register_reference_face
            )
            db.add(db_record)
            db.commit()
            db.refresh(db_record)
            return db_record.id
    except Exception as e:
        logger.error("Failed to create DB session: %s", e)
        return None


def _register_reference_face(session_id: str, candidate_id: str, interview_id: Optional[int]) -> None:
    """
    Load the candidate's stored photo and register it as the identity baseline.

    Runs in a worker thread *after* start_session has responded. The WS loop reads
    `entry["face_registered"]` live on every frame and pushes the transition to the
    client, so finishing late is fine — the readout updates without a reload.

    `enrollment_pending` is cleared in `finally` no matter what: the frontend only
    falls back to live self-enrollment once pending clears, so leaving it set would
    strand a photo-less candidate with no identity check at all.
    """
    entry = _sessions.get(session_id)
    if not entry:
        return
    try:
        with _db_session() as db:
            _log(f"[PROCTOR] Loading photo for candidate_id={candidate_id}, interview_id={interview_id}")
            ref_photo = _load_candidate_photo(candidate_id, db, interview_id=interview_id)

        if ref_photo is None:
            _log(f"[PROCTOR] WARNING: No reference photo found for candidate {candidate_id}")
            return

        _log(f"[PROCTOR] Reference photo loaded: shape={ref_photo.shape}")
        registered = bool(entry["pipeline"].register_face(ref_photo))
        entry["face_registered"] = registered
        _log(f"[PROCTOR] Face registered: {registered}")

        if registered and entry.get("db_session_id"):
            with _db_session() as db:
                from .models import ProctoringSession
                sess = db.query(ProctoringSession).filter(
                    ProctoringSession.id == entry["db_session_id"]
                ).first()
                if sess:
                    sess.face_registered = True
                    db.commit()
    except Exception as e:
        _log(f"[PROCTOR] ERROR registering reference photo: {e}")
    finally:
        entry["enrollment_pending"] = False


@router.post("/sessions/start", response_model=StartSessionResponse)
async def start_session(req: StartSessionRequest, current_user=Depends(_CANDIDATE)):
    """Start a new proctoring session.

    Everything expensive is either pre-warmed (the pipeline, see warmup.py) or moved off
    the response path (reference-photo registration). Building the pipeline here used to
    cost ~107s on a cold worker — *on the event loop* — which froze every other session
    and meant the candidate's face-match readout only appeared after a page reload.

    The critical path is now one pool pop plus one INSERT.
    """
    from .warmup import acquire

    # The session is the caller's, for an interview that is theirs. Both used to come
    # from the request body, unauthenticated: anyone could plant sessions and violations
    # in any company's report, or probe a candidate's application photo via verify-face.
    try:
        test_id = req.test_id_int
    except (TypeError, ValueError):
        raise HTTPException(422, "test_id must be an interview id")
    if req.interview_id is not None and req.interview_id != test_id:
        raise HTTPException(422, "test_id and interview_id disagree")
    found, company_id = await asyncio.to_thread(_owned_interview_company, current_user, test_id)
    if not found:
        raise HTTPException(404, "Interview not found")
    req.candidate_id = str(current_user.id)
    req.interview_id = test_id

    # One live session per candidate. A reload or a second tab replaces the old
    # session rather than adding one; without this a single candidate could call
    # start MAX_SESSIONS times and hold every slot (503 for everyone) until the
    # idle reaper freed them 15 minutes later.
    for old_sid, old in list(_sessions.items()):
        if old.get("owner_user_id") == str(current_user.id):
            stale = _sessions.pop(old_sid, None)
            if stale:
                try:
                    stale["pipeline"].shutdown()
                except Exception:
                    pass
                logger.info("Replaced proctoring session %s… for the same candidate", old_sid[:8])

    # Refuse cleanly at capacity instead of accepting everyone and degrading every
    # session at once. An over-subscribed box drops frames for candidates already
    # mid-test, which is worse than telling the next one to retry — and a silent
    # slowdown looks like a working proctor while catching nothing.
    if len(_sessions) >= MAX_SESSIONS:
        logger.warning("Proctoring at capacity: %d/%d sessions", len(_sessions), MAX_SESSIONS)
        raise HTTPException(
            503,
            "Proctoring is at capacity — please retry in a moment",
            headers={"Retry-After": "30"},
        )

    # The full uuid4: an 8-character prefix was 32 bits, and it was logged.
    session_id = str(uuid.uuid4())
    ws_token = secrets.token_urlsafe(32)

    # Cheap now: the analyzers hold counters, and the models come from the shared
    # pool. Still off the loop, because the very first session may build a model set.
    try:
        pipeline = await asyncio.to_thread(acquire)
    except Exception as e:
        logger.error("Cannot start proctoring session: %s", e)
        raise HTTPException(503, "Proctoring is unavailable — the analysis pipeline failed to initialise")

    _sessions[session_id] = {
        "pipeline": pipeline,
        "db_session_id": None,
        "candidate_id": req.candidate_id,
        "owner_user_id": str(current_user.id),
        "company_id": company_id,
        "interview_id": test_id,
        "ws_token": ws_token,
        # Registration now completes asynchronously; `enrollment_pending` tells the client
        # "a reference photo may still be loading — do NOT self-enroll yet".
        "face_registered": False,
        "enrollment_pending": True,
        "frame_count": 0,
        # ── Auto-termination strike tracking ──
        "strikes": {},                  # serious flag -> warning/strike count
        "mismatch_since": None,         # timestamp IDENTITY_MISMATCH started
        "active_serious": set(),        # serious flags currently active (episode edge detection)
        "last_minor_warn": 0.0,         # throttle for minor (LOW/MEDIUM) warnings
        "terminated": False,
        "session_started_at": time.time(),
        "last_seen": time.time(),
    }

    # Needed by the WS handler, and a fast local INSERT — keep it on the critical path,
    # but still off the loop.
    _sessions[session_id]["db_session_id"] = await asyncio.to_thread(_create_db_session, req)

    # Accommodations HR approved for this attempt, read from the database — the browser
    # cannot switch monitoring off for itself.
    acc = await asyncio.to_thread(_approved_accommodations_for, req.interview_id or req.test_id_int)
    pipeline.gaze_flags_off = bool(acc.get("gaze_flags_off"))
    pipeline.audio_monitoring_off = bool(acc.get("audio_monitoring_off"))

    # Keep the candidate's consent with the record it covers, as a marker row (like
    # LIVE_ENROLLED) — the report shows it in the session header, never as a flag.
    if req.monitoring_consent and _sessions[session_id]["db_session_id"]:
        _fire_and_forget(
            _persist_violation, _sessions[session_id]["db_session_id"],
            "MONITORING_CONSENT", "LOW", "proctor", 0,
            details=req.monitoring_consent.model_dump(),
        )

    # Reference-photo load + SFace embedding happen after we respond. The session entry
    # must already exist: the helper mutates it.
    _fire_and_forget(_register_reference_face, session_id, req.candidate_id, req.interview_id)

    logger.info("Proctoring session started: %s… (test=%s, enrollment pending)",
                _log_sid(session_id), test_id)

    return StartSessionResponse(
        session_id=session_id,
        ws_video_url=f"/api/v1/proctor/ws/video/{session_id}?token={ws_token}",
        ws_audio_url=f"/api/v1/proctor/ws/audio/{session_id}?token={ws_token}",
        status="active",
        face_registered=False,
        enrollment_pending=True,
        audio_monitoring_off=pipeline.audio_monitoring_off,
    )


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, current_user=Depends(_CANDIDATE)):
    """Stop a proctoring session and return summary."""
    _owned_entry(session_id, current_user)
    entry = _sessions.pop(session_id, None)
    if not entry:
        raise HTTPException(404, "Session not found")

    pipeline = entry["pipeline"]
    summary = pipeline.get_summary()
    pipeline.shutdown()

    # Update DB session
    if entry.get("db_session_id"):
        try:
            with _db_session() as db:
                from .models import ProctoringSession
                db_session = db.query(ProctoringSession).filter(
                    ProctoringSession.id == entry["db_session_id"]
                ).first()
                if db_session:
                    # The client stops every session on submit — including one the server
                    # just terminated. Overwriting "terminated" here erased the only record
                    # that the test ended early, so the report showed a normal completion.
                    if db_session.status != "terminated":
                        db_session.status = "completed"
                    db_session.ended_at = db_session.ended_at or datetime.utcnow()
                    db_session.cheating_score = summary["avg_cheating_score"]
                    db.commit()
        except Exception as e:
            logger.error("Failed to finalize DB session: %s", e)

    status = "terminated" if entry.get("terminated") else "completed"
    return {"session_id": session_id, "status": status, **summary}


@router.get("/sessions/{session_id}/status", response_model=SessionStatusResponse)
async def session_status(session_id: str, current_user=Depends(get_current_user)):
    """Get live status of a proctoring session."""
    entry = _readable_entry(session_id, current_user)

    pipeline = entry["pipeline"]
    summary = pipeline.get_summary()
    last = pipeline._last_frame_result

    return SessionStatusResponse(
        session_id=session_id,
        status="active",
        cheating_score=summary["avg_cheating_score"],
        frames_processed=summary["frames_processed"],
        avg_cheating_score=summary["avg_cheating_score"],
        max_cheating_score=summary["max_cheating_score"],
        face_match_score=last.face_result.get("similarity_score", 0.0) if last and last.face_result else 0.0,
        flags=last.flags if last else [],
        degraded_modules=summary.get("degraded_modules", []),
        face_engine=summary.get("face_engine", "unknown"),
    )


@router.post("/sessions/{session_id}/register-face")
async def register_face_endpoint(session_id: str, current_user=Depends(_CANDIDATE)):
    """Register face from next video frame (fallback if no stored photo)."""
    entry = _owned_entry(session_id, current_user)
    return {
        "session_id": session_id,
        "face_registered": entry["face_registered"],
        "message": "Face was pre-registered from candidate photo" if entry["face_registered"]
                   else "Send first video frame via WebSocket to register face",
    }


class EnrollFaceRequest(BaseModel):
    image_base64: str  # base64-encoded JPEG snapshot captured at test start


@router.post("/sessions/{session_id}/enroll-face")
async def enroll_face_endpoint(session_id: str, req: EnrollFaceRequest,
                               current_user=Depends(_CANDIDATE)):
    """One-time LIVE enrollment for candidates with no application photo.

    Registers the supplied snapshot as this session's identity baseline so face
    matching can run. This is weaker than a vetted application photo (it trusts
    whoever is present at enrollment), so the session is flagged LIVE_ENROLLED for
    HR review. Used only when start_session found no stored reference photo.
    """
    entry = _owned_entry(session_id, current_user)

    if entry.get("face_registered"):
        return {"session_id": session_id, "face_registered": True, "live_enrolled": False,
                "message": "A reference face is already registered"}

    if entry.get("enrollment_pending"):
        # The stored application photo is still being registered. Enrolling the live
        # frame now would overwrite a vetted reference with a self-captured one — the
        # candidate would then match themselves no matter who they are. Tell the client
        # to retry; its existing retry loop waits this out.
        return {"session_id": session_id, "face_registered": False, "live_enrolled": False,
                "retry": True,
                "message": "Reference photo is still loading — retry shortly"}

    try:
        img_bytes = base64.b64decode(req.image_base64)
        frame = cv2.imdecode(np.frombuffer(img_bytes, np.uint8), cv2.IMREAD_COLOR)
        if frame is None:
            raise HTTPException(400, "Invalid image data")

        # Quality gate BEFORE registering. The baseline is set once and every later frame
        # is scored against it, so enrolling from a dark or distant frame poisons the whole
        # session — the candidate then sits at a few percent similarity and is flagged for
        # mismatching their own face. Rejecting here lets the client's retry loop wait for
        # a usable frame (e.g. once webcam auto-exposure has settled).
        face = getattr(entry["pipeline"], "_face", None)
        if face is not None and hasattr(face, "enrollment_quality"):
            reason = await asyncio.to_thread(face.enrollment_quality, frame)
            if reason:
                return {"session_id": session_id, "face_registered": False,
                        "live_enrolled": False, "retry": True,
                        "message": f"Cannot enrol yet: {reason}"}

        # Off the event loop: register_face now blocks on the FaceAnalyzer lock while a
        # frame is being analysed, and holding the loop for that would stall every other
        # session on this worker.
        registered = await asyncio.to_thread(entry["pipeline"].register_face, frame)
        if not registered:
            # No detectable face in the snapshot — caller may retry with a clearer frame.
            return {"session_id": session_id, "face_registered": False, "live_enrolled": False,
                    "retry": True,
                    "message": "No face detected in snapshot — please face the camera and retry"}

        entry["face_registered"] = True

        # Persist the enrollment snapshot + flag the session as live-enrolled for HR.
        db_session_id = entry.get("db_session_id")
        photo_path = None
        try:
            ts = int(time.time())
            photo_path = f"{entry['pipeline'].cfg.evidence_dir}/{session_id}/enrollment_{session_id}_{ts}.jpg"
            from src.services.storage import storage
            ok, buf = cv2.imencode(".jpg", frame)
            if ok:
                storage.save_bytes(buf.tobytes(), photo_path)
        except Exception as ev_err:
            logger.error("Failed to save enrollment snapshot: %s", ev_err)

        if db_session_id:
            try:
                with _db_session() as db:
                    from .models import ProctoringSession
                    sess = db.query(ProctoringSession).filter(ProctoringSession.id == db_session_id).first()
                    if sess:
                        sess.face_registered = True
                        sess.face_photo_path = photo_path
                        db.commit()
            except Exception as e:
                logger.error("Failed to update session after enrollment: %s", e)
            # Low-severity marker so HR knows identity was self-enrolled, not vetted.
            _persist_violation(
                db_session_id, "LIVE_ENROLLED", "LOW", "face", 0,
                details={"note": "Identity baseline captured live at test start (no application photo)"},
                evidence_path=photo_path,
            )

        return {"session_id": session_id, "face_registered": True, "live_enrolled": True,
                "message": "Live enrollment successful — identity verification active (unvetted baseline)"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Live enrollment failed: %s", e)
        raise HTTPException(500, f"Live enrollment failed: {str(e)}")


# ---------- HR / Interviewer review (DB-backed, persists after the test) ----------

_REVIEW_ROLES = require_role(["hr", "admin", "interviewer", "superadmin"])


@router.get("/results/{interview_id}")
async def get_proctoring_results(
    interview_id: int,
    db: Session = Depends(get_db),
    current_user=Depends(_REVIEW_ROLES),
):
    """Proctoring report for an interview/test — for HR/interviewer review.

    Unlike /sessions/{id}/logs (which needs the in-memory session to still be
    alive), this reads persisted ProctoringSession + ProctoringViolation rows, so
    it works after the test has ended. Tenant-scoped to the caller's company.
    """
    from src.models.models import (
        AssessmentExplanation, Interview, ProctoringSession, ProctoringViolation,
        ProctoringReview, User,
    )

    # The interview first, scoped to the caller's company: everything below hangs off
    # it. Sessions with no company_id are not "probably this tenant's" — a NULL row
    # belongs to no tenant, and scripts/backfill_orphan_company_ids.py is the fix.
    iv_q = db.query(Interview).filter(Interview.id == interview_id)
    if current_user.role != "superadmin":
        iv_q = iv_q.filter(Interview.company_id == current_user.company_id)
    iv = iv_q.first()
    if iv is None:
        raise HTTPException(404, "Interview not found")
    from src.services.audit_log import require_support_access
    require_support_access(db, None, current_user, iv.company_id,
                           f"proctoring results for interview {interview_id}")

    sessions = db.query(ProctoringSession).filter(
        ProctoringSession.test_id == interview_id,
        ProctoringSession.company_id == iv.company_id,
    ).order_by(ProctoringSession.started_at.desc()).all()

    out_sessions = []
    for s in sessions:
        violations = db.query(ProctoringViolation).filter(
            ProctoringViolation.session_id == s.id
        ).order_by(ProctoringViolation.timestamp.desc()).all()

        reviews = db.query(ProctoringReview).filter(
            ProctoringReview.session_id == s.id
        ).order_by(ProctoringReview.created_at.desc()).all()
        reviewer_names = _user_names(db, User, {r.reviewer_id for r in reviews})
        latest_review = {}                       # violation_id (None = session) -> newest row
        for r in reviews:
            latest_review.setdefault(r.violation_id, r)

        live_enrolled = any(v.violation_type == "LIVE_ENROLLED" for v in violations)
        consent = next((v for v in violations if v.violation_type == "MONITORING_CONSENT"), None)
        flagged = [v for v in violations if v.violation_type not in MARKER_VIOLATION_TYPES]
        # What face matching compared against. It is never an identity-document check:
        # the reference is the photo the candidate uploaded, or a snapshot they took at
        # the start of the test when there was none.
        reference = ("live_snapshot" if live_enrolled
                     else "application_photo" if s.face_registered else "none")
        out_sessions.append({
            "session_id": s.id,
            "candidate_id": s.candidate_id,
            "status": s.status,
            "started_at": s.started_at.isoformat() if s.started_at else None,
            "ended_at": s.ended_at.isoformat() if s.ended_at else None,
            "cheating_score": s.cheating_score,
            "max_cheating_score": s.max_cheating_score,
            "decision": s.decision or ProctorConfig().score_to_decision(s.cheating_score or 0),
            "module_scores": {
                "face": s.face_score, "object": s.object_score, "audio": s.audio_score,
                "pose": s.pose_score, "behavioral": s.behavioral_score,
            },
            "face_registered": s.face_registered,
            "identity_verified": bool(s.face_registered) and not live_enrolled,   # legacy; use `reference`
            "live_enrolled": live_enrolled,
            "reference": reference,
            "monitoring_consent": (
                {**(consent.details or {}), "recorded_at": consent.timestamp.isoformat() if consent.timestamp else None}
                if consent else None
            ),
            "total_violations": len(flagged),
            "unreviewed_count": sum(
                1 for v in flagged
                if v.violation_type not in UNREVIEWABLE_TYPES and v.id not in latest_review
            ),
            "review": _review_dict(latest_review.get(None), reviewer_names),
            "review_history": [_review_dict(r, reviewer_names) for r in reviews],
            "violations": [
                {
                    "id": v.id,
                    "type": v.violation_type,
                    "severity": v.severity,
                    "module": v.module,
                    "score_impact": v.score_impact,
                    "timestamp": v.timestamp.isoformat() if v.timestamp else None,
                    "details": v.details,
                    "evidence_url": (
                        f"/api/v1/proctor/evidence/{s.id}/{os.path.basename(v.evidence_path)}"
                        if v.evidence_path else None
                    ),
                    "review": _review_dict(latest_review.get(v.id), reviewer_names),
                    "reviewable": v.violation_type not in UNREVIEWABLE_TYPES,
                }
                for v in flagged
            ],
        })

    # The attempt itself: which try this is, the adjustments it ran with, and the
    # candidate's own explanation — context a reviewer needs next to the flags.
    from src.services.assessment_fairness import approved_accommodations, describe
    explanations = db.query(AssessmentExplanation).filter(
        AssessmentExplanation.interview_id == iv.id,
        AssessmentExplanation.company_id == iv.company_id,
    ).order_by(AssessmentExplanation.created_at).all()

    return {
        "interview_id": interview_id,
        "attempt_number": (iv.attempt_number or 1) if iv else 1,
        "previous_attempt_id": iv.previous_attempt_id if iv else None,
        "accommodations_summary": describe(approved_accommodations(iv)) if iv else [],
        "candidate_explanations": [
            {"id": e.id, "text": e.text, "created_at": e.created_at.isoformat() if e.created_at else None}
            for e in explanations
        ],
        "session_count": len(out_sessions),
        "sessions": out_sessions,
    }


REVIEW_OUTCOMES = ("confirmed", "dismissed", "inconclusive")

# Timeline rows that record a fact rather than an allegation, so there is nothing to
# confirm or dismiss. They stay visible; they just never count as "waiting for review".
from src.models.models import PROCTORING_UNREVIEWABLE_TYPES as UNREVIEWABLE_TYPES  # noqa: E402


def _user_names(db, User, ids) -> dict:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    return {u.id: f"{u.first_name} {u.last_name}".strip()
            for u in db.query(User).filter(User.id.in_(ids)).all()}


def _review_dict(r, names: dict):
    if r is None:
        return None
    return {
        "id": r.id,
        "violation_id": r.violation_id,
        "outcome": r.outcome,
        "remarks": r.remarks,
        "reviewer_id": r.reviewer_id,
        "reviewer_name": names.get(r.reviewer_id),
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


class ReviewRequest(BaseModel):
    session_id: str                          # ProctoringSession.id (the DB id in the report)
    violation_ids: Optional[List[str]] = None  # None/empty = a verdict on the whole session
    outcome: str
    remarks: Optional[str] = None


@router.post("/reviews")
async def record_review(
    req: ReviewRequest,
    db: Session = Depends(get_db),
    current_user=Depends(_REVIEW_ROLES),
):
    """Record a reviewer's outcome — Confirmed, Dismissed or Inconclusive — for flagged
    events, or for the whole session.

    Append-only: changing your mind adds a new row, and the report shows the latest one
    with its history, so there is always a record of who concluded what and when.
    Several violation ids can be reviewed at once because the report folds repeated
    readings of one flag into a single timeline entry, and one decision covers them all.
    A confirmed outcome needs remarks: it is the one that can count against a candidate,
    so it must say why.
    """
    from src.models.models import ProctoringSession, ProctoringViolation, ProctoringReview, User

    outcome = (req.outcome or "").strip().lower()
    if outcome not in REVIEW_OUTCOMES:
        raise HTTPException(422, f"outcome must be one of {', '.join(REVIEW_OUTCOMES)}")
    remarks = (req.remarks or "").strip() or None
    if remarks and len(remarks) > 2000:
        raise HTTPException(422, "Remarks are limited to 2000 characters")
    if outcome == "confirmed" and not remarks:
        raise HTTPException(422, "Say what you saw when confirming a flag")

    session = db.query(ProctoringSession).filter(ProctoringSession.id == req.session_id).first()
    if not session or not session.company_id or (
            current_user.role != "superadmin" and session.company_id != current_user.company_id):
        raise HTTPException(404, "Proctoring session not found")
    # A superadmin's outcome can count against a candidate, so it needs the same
    # support-access grant as reading the report, and it is audited.
    from src.services.audit_log import require_support_access
    require_support_access(db, None, current_user, session.company_id,
                           f"proctoring_review:{session.id}")

    ids = list(dict.fromkeys(req.violation_ids or []))   # de-duplicated, order kept
    if ids:
        found = {v.id: v for v in db.query(ProctoringViolation).filter(
            ProctoringViolation.id.in_(ids),
            ProctoringViolation.session_id == session.id,
        ).all()}
        missing = [i for i in ids if i not in found]
        if missing:
            raise HTTPException(404, "Some of those events are not part of this session")
        if any(found[i].violation_type in MARKER_VIOLATION_TYPES | UNREVIEWABLE_TYPES for i in ids):
            raise HTTPException(422, "Those entries record facts, not flags, and have nothing to review")

    rows = [
        ProctoringReview(
            company_id=session.company_id or current_user.company_id,
            session_id=session.id,
            violation_id=vid,
            outcome=outcome,
            remarks=remarks,
            reviewer_id=current_user.id,
            created_at=datetime.utcnow(),
        )
        for vid in (ids or [None])
    ]
    db.add_all(rows)
    db.commit()
    names = _user_names(db, User, {current_user.id})
    return {"reviews": [_review_dict(r, names) for r in rows]}


@router.get("/evidence/{session_id}/{filename}")
async def get_evidence_image(
    session_id: str,
    filename: str,
    db: Session = Depends(get_db),
    current_user=Depends(_REVIEW_ROLES),
):
    """Stream an evidence/enrollment frame for a violation. Tenant-scoped + path-safe."""
    from src.models.models import ProctoringSession, ProctoringViolation

    # Confirm the session belongs to the caller's company before serving any bytes.
    sess_q = db.query(ProctoringSession).filter(ProctoringSession.id == session_id)
    if current_user.role != "superadmin":
        sess_q = sess_q.filter(ProctoringSession.company_id == current_user.company_id)
    _sess = sess_q.first()
    if not _sess:
        raise HTTPException(404, "Session not found")
    from src.services.audit_log import require_support_access
    require_support_access(db, None, current_user, _sess.company_id,
                           f"proctoring evidence {session_id}/{filename}")

    # Reject path traversal — only a bare filename under this session's evidence dir.
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(400, "Invalid filename")

    # Resolve the real storage key from the violation row. Evidence is written under
    # the ephemeral runtime session id (e.g. "85ddfb25"), which differs from this DB
    # session id, so reconstructing the path from session_id would miss the file.
    # evidence_path holds the true key; fall back to the old scheme for stray files.
    key = None
    for v in db.query(ProctoringViolation).filter(
        ProctoringViolation.session_id == session_id,
        ProctoringViolation.evidence_path.isnot(None),
    ).all():
        if os.path.basename(v.evidence_path) == filename:
            key = v.evidence_path
            break
    if key is None:
        key = f"{ProctorConfig().evidence_dir}/{session_id}/{filename}"
    from src.services.storage import storage
    if not storage.exists(key):
        raise HTTPException(404, "Evidence not found")
    try:
        data = storage.read_bytes(key)
    except Exception as e:
        logger.error("Failed to read evidence %s: %s", key, e)
        raise HTTPException(500, "Failed to read evidence")
    return Response(content=data, media_type="image/jpeg", headers={
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    })


class VerifyFaceRequest(BaseModel):
    image_base64: str  # base64-encoded JPEG


@router.post("/sessions/{session_id}/verify-face")
async def verify_face_endpoint(session_id: str, req: VerifyFaceRequest,
                               current_user=Depends(_CANDIDATE)):
    """Compare a live frame to the registered face encoding. Returns match result."""
    entry = _owned_entry(session_id, current_user)

    pipeline = entry["pipeline"]
    if not entry["face_registered"]:
        raise HTTPException(400, "No face registered for this session")

    try:
        img_bytes = base64.b64decode(req.image_base64)
        nparr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            raise HTTPException(400, "Invalid image data")

        result = await pipeline.process_frame(frame, session_id)
        face_data = result.face_result or {}
        return {
            "session_id": session_id,
            "identity_match": face_data.get("identity_match", False),
            "similarity_score": round(face_data.get("similarity_score", 0.0), 3),
            "face_detected": face_data.get("face_detected", False),
            "face_count": face_data.get("face_count", 0),
            "gaze_direction": face_data.get("gaze_direction", "UNKNOWN"),
            "head_turned": face_data.get("head_turned", False),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Face verification failed: %s", e)
        raise HTTPException(500, f"Face verification failed: {str(e)}")


class BehaviorCheckRequest(BaseModel):
    keystrokes: Optional[list] = None
    mouse_positions: Optional[list] = None
    # Lists of events, not counts. BehavioralEngine.extract_features derives
    # copy_paste_count/tab_switch_count from len(deque), and ingest_signals appends
    # a non-list value as a single item — so typing these as int made the deque
    # count *requests* rather than events. The WS path always sent lists.
    copy_paste: Optional[list] = None
    tab_switches: Optional[list] = None
    question_times: Optional[list] = None


@router.post("/sessions/{session_id}/behavior-check")
async def behavior_check(session_id: str, req: BehaviorCheckRequest,
                         current_user=Depends(_CANDIDATE)):
    """Receive behavioral feature vector and run anomaly detection."""
    entry = _owned_entry(session_id, current_user)

    pipeline = entry["pipeline"]
    payload = {
        "keystrokes": req.keystrokes or [],
        "mouse_positions": req.mouse_positions or [],
        "copy_paste": req.copy_paste or [],
        "tab_switches": req.tab_switches or [],
        "question_times": req.question_times or [],
    }
    pipeline.ingest_behavioral_signals(payload)

    return {
        "session_id": session_id,
        "status": "received",
        "signals_count": len(req.keystrokes or []) + len(req.mouse_positions or []),
    }


@router.get("/sessions/{session_id}/logs")
async def get_session_logs(session_id: str, current_user=Depends(get_current_user)):
    """Fetch cheating violation logs for a proctoring session."""
    entry = _readable_entry(session_id, current_user)
    db_session_id = entry.get("db_session_id")

    if not db_session_id:
        raise HTTPException(404, "Session not found or no DB record")

    try:
        with _db_session() as db:
            from .models import ProctoringViolation
            violations = db.query(ProctoringViolation).filter(
                ProctoringViolation.session_id == db_session_id
            ).order_by(ProctoringViolation.timestamp.desc()).all()

            return {
                "session_id": session_id,
                "db_session_id": db_session_id,
                "violation_count": len(violations),
                "violations": [
                    {
                        "id": v.id,
                        "type": v.violation_type,
                        "severity": v.severity,
                        "module": v.module,
                        "score_impact": v.score_impact,
                        "timestamp": v.timestamp.isoformat() if v.timestamp else None,
                        "details": v.details,
                    }
                    for v in violations
                ],
            }
    except Exception as e:
        logger.error("Failed to fetch logs: %s", e)
        raise HTTPException(500, f"Failed to fetch logs: {str(e)}")


class TabSwitchRequest(BaseModel):
    count: int
    timestamp: Optional[float] = None


@router.post("/sessions/{session_id}/tab-switch")
async def record_tab_switch(session_id: str, req: TabSwitchRequest,
                            current_user=Depends(_CANDIDATE)):
    """Record a tab switch for the reviewer. Warns the candidate; never ends the test.

    Tab switches used to count as strikes (4th ended the test). A notification that steals
    focus, a password manager, an accessibility tool — all look identical from here, so the
    event is evidence for a person to weigh, not grounds to stop the assessment.
    """
    entry = _owned_entry(session_id, current_user)

    n = entry["strikes"].get("TAB_SWITCH", 0) + 1   # a running count, not a strike toward ending
    entry["strikes"]["TAB_SWITCH"] = n

    db_session_id = entry.get("db_session_id")
    if db_session_id:
        _persist_violation(
            db_session_id, "TAB_SWITCH", "MEDIUM", "behavioral", score_impact=10,
            details={"tab_switch_count": req.count, "occurrence": n, "timestamp": req.timestamp},
        )

    logger.info("Tab switch #%d recorded for session %s…", n, _log_sid(session_id))
    return {
        "session_id": session_id,
        "tab_switch_count": req.count,
        "occurrence": n,
        "warning": True,
        "terminate": False,
    }


class FullscreenExitRequest(BaseModel):
    count: int
    timestamp: Optional[float] = None


class FullscreenUnavailableRequest(BaseModel):
    reason: Optional[str] = None      # what the browser said, or "unsupported"


@router.post("/sessions/{session_id}/fullscreen-exit")
async def record_fullscreen_exit(session_id: str, req: FullscreenExitRequest,
                                 current_user=Depends(_CANDIDATE)):
    """Record a fullscreen exit for the reviewer. Warns the candidate; never ends the test.

    Deliberately its own flag rather than reusing TAB_SWITCH: HR's report should be able
    to distinguish "pressed Escape" from "switched tab", and the two fire from different
    browser events, so sharing a counter would double-count a single act on the browsers
    where leaving fullscreen also backgrounds the tab.
    """
    entry = _owned_entry(session_id, current_user)

    n = entry["strikes"].get("FULLSCREEN_EXIT", 0) + 1
    entry["strikes"]["FULLSCREEN_EXIT"] = n

    db_session_id = entry.get("db_session_id")
    if db_session_id:
        _persist_violation(
            db_session_id, "FULLSCREEN_EXIT", "MEDIUM", "behavioral", score_impact=10,
            details={"exit_count": req.count, "occurrence": n, "timestamp": req.timestamp},
        )

    logger.info("Fullscreen exit #%d recorded for session %s…", n, _log_sid(session_id))
    return {
        "session_id": session_id,
        "exit_count": req.count,
        "occurrence": n,
        "warning": True,
        "terminate": False,
    }


@router.post("/sessions/{session_id}/fullscreen-unavailable")
async def record_fullscreen_unavailable(session_id: str, req: FullscreenUnavailableRequest,
                                        current_user=Depends(_CANDIDATE)):
    """Note that this browser could not go fullscreen. Never a strike.

    iOS Safari has no Element.requestFullscreen at all, and a candidate should not lose a
    job application over their phone. Recorded with zero score impact purely so the
    report says "this run was not fullscreen-monitored" instead of looking identical to
    one that was.
    """
    entry = _owned_entry(session_id, current_user)

    db_session_id = entry.get("db_session_id")
    if db_session_id:
        _persist_violation(
            db_session_id, "FULLSCREEN_UNAVAILABLE", "LOW", "behavioral",
            score_impact=0,
            details={"reason": req.reason or "unsupported"},
        )
    logger.info("Fullscreen unavailable for session %s…: %s", _log_sid(session_id), req.reason)
    return {"session_id": session_id, "recorded": True}


@router.get("/health")
async def proctor_health():
    """Health check for the proctoring module."""
    return {
        "status": "healthy",
        "active_sessions": len(_sessions),
        "module": "ai_proctoring",
    }


# ---------- WebSocket Endpoints ----------

# Pre-test connectivity probe limits. The probe carries one frame-sized payload, so the
# cap only has to exceed a proctoring frame (~50 KB of base64 JPEG) with room to spare.
_PING_MAX_BYTES = 256 * 1024
_PING_MAX_MESSAGES = 10
_PING_IDLE_SEC = 15.0


@router.websocket("/ws/ping")
async def ws_ping(ws: WebSocket):
    """Pre-test check that proctoring WebSockets can reach this service.

    The candidate's system check (TestInstructions.jsx) connects here before the test.
    It travels the exact route the real sockets use — the /api/v1/proctor/ws/ proxy rule
    to the single-worker proctor service — so a proxy that downgrades the Upgrade
    request, a corporate firewall or a VPN that blocks WebSockets fails HERE, where the
    candidate can still fix it, instead of silently mid-test (see deploy/cpanel/
    hireflow.conf: a broken WS route once left every session "clean" with zero frames).

    Replies with the size of each message so the client can time an upload of a
    frame-sized payload. Nothing is stored and no session is needed. Bounded in message
    count, size and idle time so it cannot be held open or used as a bulk sink.
    """
    await ws.accept()
    try:
        for _ in range(_PING_MAX_MESSAGES):
            data = await asyncio.wait_for(ws.receive_text(), timeout=_PING_IDLE_SEC)
            if len(data) > _PING_MAX_BYTES:
                await ws.close(code=1009, reason="Probe payload too large")
                return
            await ws.send_json({"event": "pong", "bytes": len(data)})
        await ws.close(code=1000)
    except (WebSocketDisconnect, asyncio.TimeoutError):
        try:
            await ws.close(code=1000)
        except Exception:
            pass

@router.websocket("/ws/video/{session_id}")
async def ws_video(ws: WebSocket, session_id: str):
    """
    Video frame ingestion via WebSocket.
    Client sends: base64-encoded JPEG frames.
    Server responds: JSON with cheating score, face match result, flags.

    Face is pre-registered from the candidate's stored photo at session start.
    If no stored photo exists, the first frame is used to register the face.
    """
    await ws.accept()
    entry = _sessions.get(session_id)
    if not entry:
        # Logged deliberately: a 4004 is the signature of a ROUTING problem, not a
        # client error. _sessions is process-local, so this means the socket reached
        # a different process than the one that ran /sessions/start — more than one
        # proctor worker, or a proxy sending WS and HTTP to different upstreams.
        # This used to close silently, leaving the client on "Checking..." with no
        # server-side evidence whatsoever.
        logger.warning(
            "Proctor WS 4004 on %s: session=%s not in THIS process (pid=%s, %d live "
            "session(s)). Proctoring must run as ONE worker — check WEB_CONCURRENCY "
            "in hireflow-proctor.service and the proxy's WebSocket route.",
            "/".join(str(getattr(getattr(ws, "url", None), "path", "?")).split("/")[:-1]),
            _log_sid(session_id), os.getpid(), len(_sessions),
        )
        await ws.close(code=4004, reason="Session not found")
        return
    if not _ws_authorized(ws, entry):
        # 4401: authenticated with the session's token, or not at all.
        await ws.close(code=4401, reason="Unauthorized")
        return

    entry["last_seen"] = time.time()
    pipeline = entry["pipeline"]
    db_session_id = entry.get("db_session_id")
    # Serious flags currently inside a persisted episode. One row is written on the rising
    # edge and nothing more until the flag clears, so a two-second MULTIPLE_FACES leaves
    # exactly one record instead of depending on which 30-frame bucket it landed in.
    episode_persisted = set()
    medium_last_persist = {}  # MEDIUM flag -> wall-clock of its last persisted row
    prev_flags = set()        # flags on the previous frame, to spot the start of an episode
    last_score_persist = 0.0  # wall-clock of the last _update_session_scores call
    # Tracks what we last told the client about enrollment, so a live enrollment that
    # completes *after* this socket opened is pushed instead of waiting for a reload.
    announced_registered = entry.get("face_registered")
    decode_failures = 0       # counted so the log line below can be rate-limited

    logger.info("Video WS connected: session=%s (face_pre_registered=%s)",
                _log_sid(session_id), announced_registered)

    try:
        while True:
            data = await ws.receive_text()
            try:
                # Decode base64 JPEG → numpy BGR frame
                img_bytes = base64.b64decode(data)
                nparr = np.frombuffer(img_bytes, np.uint8)
                frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if frame is None:
                    # Log it — rate-limited. A run of silent decode failures used to
                    # leave NO trace anywhere: frame_count never increments, the
                    # client stays on "Checking..." because `skipped` does not count
                    # as an analysis, and the session ends looking clean. That is
                    # indistinguishable from "no frames were ever sent" unless this
                    # line exists.
                    decode_failures += 1
                    if decode_failures == 1 or decode_failures % 50 == 0:
                        logger.warning(
                            "Video WS decode failed: session=%s count=%d payload=%dB "
                            "b64_len=%d (a binary frame or a truncated data URL?)",
                            session_id, decode_failures, len(img_bytes), len(data),
                        )
                    # Ack anyway: the client paces on replies, so silently dropping a bad
                    # frame would stall its pump until the watchdog fires.
                    await ws.send_json({"event": "skipped", "reason": "decode_failed"})
                    continue

                # Once the test has been auto-terminated, stop analyzing — the client
                # is submitting and tearing down.
                if entry.get("terminated"):
                    continue

                # Process frame through the full pipeline ALWAYS — even with no reference
                # photo. Multi-face, head-turn, gaze, object (phone/book) and pose detection
                # do NOT need a registered face, so they must keep working. We never register
                # from a live frame (that would defeat identity verification — any person would
                # "match" themselves); instead we run everything and report identity as
                # *unverified* (not a mismatch) when no reference exists.
                result = await pipeline.process_frame(frame, session_id)
                entry["frame_count"] += 1
                # Liveness stamp for the idle reaper. A candidate who closes the tab never
                # hits /sessions/stop, so without this the entry — and its MediaPipe graph,
                # YOLO model and 4-thread executor — would be held forever.
                entry["last_seen"] = time.time()

                # Read enrollment state live off the session entry — /enroll-face mutates it
                # ~600ms *after* this socket opened, so a snapshot taken at connect would
                # stay stale (and keep flagging NO_REFERENCE_PHOTO) for the whole session.
                face_registered = entry.get("face_registered")
                enrollment_pending = bool(entry.get("enrollment_pending"))

                # Surface an informational flag so the UI / HR knows identity couldn't be
                # checked. This is NOT scored as cheating (see pipeline._calc_face_score).
                # Suppressed while enrollment is pending: at that point we don't yet know
                # whether a reference photo exists, and flagging it would be wrong (and
                # would tell the client to self-enroll before the real photo lands).
                if not face_registered and not enrollment_pending and "NO_REFERENCE_PHOTO" not in result.flags:
                    result.flags.append("NO_REFERENCE_PHOTO")

                # Push the transition so the client updates without a reload.
                if face_registered and not announced_registered:
                    announced_registered = True
                    await ws.send_json({"event": "face_registered", "success": True})

                # Build face match details
                face_data = result.face_result or {}

                # Debug: log identity match status every 25 frames
                if entry["frame_count"] % 25 == 1:
                    _log(f"[MATCH] session={_log_sid(session_id)}… frame={entry['frame_count']} identity_match={face_data.get('identity_match', 'N/A')} similarity={face_data.get('similarity_score', 'N/A')} flags={result.flags}")
                response = {
                    "event": "analysis",
                    # Lets the client know when the stored-photo lookup has resolved, so it
                    # can fall back to live enrollment only if there genuinely is no photo.
                    "enrollment_pending": enrollment_pending,
                    "cheating_score": result.cheating_score,
                    "decision": pipeline.cfg.score_to_decision(result.cheating_score),
                    "flags": result.flags,
                    "module_scores": result.module_scores,
                    "face": {
                        "detected": face_data.get("face_detected", False),
                        "count": face_data.get("face_count", 0),
                        "identity_match": face_data.get("identity_match", False),
                        "has_reference": face_data.get("has_reference", False),
                        "similarity_score": round(face_data.get("similarity_score", 0.0), 3),
                        "head_turned": face_data.get("head_turned", False),
                        "gaze_direction": face_data.get("gaze_direction", "UNKNOWN"),
                        "gaze_deviation": face_data.get("gaze_deviation_score", 0),
                        "yaw": face_data.get("yaw", 0),
                        "pitch": face_data.get("pitch", 0),
                        "lip_moving": face_data.get("lip_moving", False),
                        "mouth_aspect_ratio": face_data.get("mouth_aspect_ratio", 0),
                        "blink_detected": face_data.get("blink_detected", False),
                        "eye_aspect_ratio": face_data.get("eye_aspect_ratio", 0),
                        "face_bbox": face_data.get("face_bbox"),
                        "all_face_bboxes": face_data.get("all_face_bboxes"),
                        "landmarks": face_data.get("landmarks"),
                        "eye_boxes": face_data.get("eye_boxes"),
                    },
                    "processing_ms": round(result.processing_time_ms, 1),
                    "timestamp": result.timestamp,
                    "frame_number": entry["frame_count"],
                    # dBFS, not acoustic dB SPL — see AudioAnalysisResult.
                    "audio": {
                        "spl_dbfs": round((result.audio_result or {}).get("spl_dbfs", -120.0), 1),
                        "peak_dbfs": round((result.audio_result or {}).get("peak_dbfs", -120.0), 1),
                        "noise_floor_dbfs": round((result.audio_result or {}).get("noise_floor_dbfs", -120.0), 1),
                        "is_speaking": (result.audio_result or {}).get("is_speaking", False),
                        "voice_count": (result.audio_result or {}).get("voice_count_estimate", 0),
                        "speaker_enrolled": (result.audio_result or {}).get("speaker_enrolled", False),
                        "mic_muted": (result.audio_result or {}).get("mic_muted", True),
                    } if result.audio_result else None,
                }
                await ws.send_json(response)

                # Persist violations to DB. MEDIUM is included: HR's red-flag report used to
                # show only HIGH/CRITICAL, so head-turns and the rest were warned about live
                # and then lost. LOW stays out — it is noise, not evidence.
                if db_session_id:
                    current_flags = set(result.flags or ())
                    starting_flags = current_flags - prev_flags
                    now_flag = time.time()
                    for flag in current_flags:
                        severity = _flag_severity(flag)
                        if severity == "MEDIUM":
                            # Time-throttled, not once-per-episode: a head-turn can persist
                            # for minutes and one row per episode would understate it.
                            if now_flag - medium_last_persist.get(flag, 0.0) < _MEDIUM_PERSIST_INTERVAL_SEC:
                                continue
                            medium_last_persist[flag] = now_flag
                        elif severity in ("HIGH", "CRITICAL"):
                            if flag in episode_persisted:
                                continue
                            episode_persisted.add(flag)
                        else:
                            continue
                        score_impact = pipeline.cfg.TRIGGER_SCORES.get(flag, 10)
                        # Evidence frame for EVERY flag, not just the three face ones —
                        # a PHONE_DETECTED row with no picture is the one HR most needs to
                        # see and can't act on. Audio flags get a frame too: what the
                        # camera saw while a second voice was heard is the context.
                        # The only rows without one are MEDIUM continuations: those repeat
                        # every 10s for as long as the flag lasts, and the frame that
                        # opened the episode already shows it.
                        # Encode + upload happen off-loop; `frame` is never mutated in
                        # place, so the worker keeps a valid reference to this exact frame.
                        evidence_path = None
                        if severity != "MEDIUM" or flag in starting_flags:
                            # The object detector has already written an annotated frame for
                            # what it saw (box + label + confidence). Point at that instead of
                            # saving a second, unmarked copy of the same moment.
                            detector_evidence = (result.object_result or {}).get("evidence_path")
                            if _flag_module(flag) == "object" and detector_evidence:
                                evidence_path = detector_evidence
                            else:
                                ts = int(time.time())
                                evidence_path = f"{pipeline.cfg.evidence_dir}/{session_id}/evidence_{session_id}_{ts}_{flag}.jpg"
                                _fire_and_forget(_save_evidence, frame, evidence_path)
                        _fire_and_forget(
                            _persist_violation,
                            db_session_id, flag, severity,
                            _flag_module(flag),
                            score_impact,
                            details=_violation_details(flag, entry["frame_count"], result, face_data),
                            evidence_path=evidence_path,
                        )
                    # Episode over — re-arm so the next occurrence is recorded as its own event.
                    episode_persisted &= current_flags
                    prev_flags = current_flags

                # Update DB session scores every ~2s. Time-based, not frame-based: the client
                # now paces frames to the server's throughput, so a frame counter would
                # stretch this cadence on slower machines. Written off-loop like the rest.
                now_persist = time.time()
                if db_session_id and now_persist - last_score_persist >= 2.0:
                    last_score_persist = now_persist
                    _fire_and_forget(_update_session_scores, db_session_id, result)

                # Warning/strike policy — may send 'warning' or 'terminate' events.
                await _evaluate_strikes(session_id, entry, result, ws)

            except Exception as e:
                logger.error("Frame processing error: %s", e)
                await ws.send_json({"event": "error", "message": str(e)})

    except WebSocketDisconnect:
        logger.info("Video WS disconnected: session=%s (frames=%d, decode_failures=%d)",
                    _log_sid(session_id), entry.get("frame_count", 0), decode_failures)
    except Exception as e:
        # receive_text() raises here — OUTSIDE the per-frame try — if the client
        # sends a binary frame. It is not a WebSocketDisconnect, so without this
        # branch the socket simply died: no disconnect log, no error log, and a
        # session that looks like it was never used.
        logger.error("Video WS aborted: session=%s (frames=%d, decode_failures=%d) "
                     "%s: %s", session_id, entry.get("frame_count", 0),
                     decode_failures, type(e).__name__, e)
    finally:
        # Let the audit-trail writes finish. A violation raised in the last second of a
        # test is exactly the one worth keeping, and these tasks hold no reference that
        # would otherwise keep them alive once the request scope goes away.
        pending = [t for t in _bg_writes if not t.done()]
        if pending:
            done, still_pending = await asyncio.wait(pending, timeout=10.0)
            if still_pending:
                logger.warning("Video WS %s: %d proctor write(s) unfinished at teardown",
                               session_id, len(still_pending))


@router.websocket("/ws/audio/{session_id}")
async def ws_audio(ws: WebSocket, session_id: str):
    """
    Audio stream ingestion via WebSocket.
    Expects raw PCM audio bytes (16-bit mono, 16kHz).
    """
    await ws.accept()
    entry = _sessions.get(session_id)
    if not entry:
        # Logged deliberately: a 4004 is the signature of a ROUTING problem, not a
        # client error. _sessions is process-local, so this means the socket reached
        # a different process than the one that ran /sessions/start — more than one
        # proctor worker, or a proxy sending WS and HTTP to different upstreams.
        # This used to close silently, leaving the client on "Checking..." with no
        # server-side evidence whatsoever.
        logger.warning(
            "Proctor WS 4004 on %s: session=%s not in THIS process (pid=%s, %d live "
            "session(s)). Proctoring must run as ONE worker — check WEB_CONCURRENCY "
            "in hireflow-proctor.service and the proxy's WebSocket route.",
            "/".join(str(getattr(getattr(ws, "url", None), "path", "?")).split("/")[:-1]),
            _log_sid(session_id), os.getpid(), len(_sessions),
        )
        await ws.close(code=4004, reason="Session not found")
        return
    if not _ws_authorized(ws, entry):
        # 4401: authenticated with the session's token, or not at all.
        await ws.close(code=4401, reason="Unauthorized")
        return

    pipeline = entry["pipeline"]
    logger.info("Audio WS connected: session=%s", _log_sid(session_id))

    try:
        while True:
            data = await ws.receive_bytes()
            if pipeline.audio_monitoring_off:
                continue   # accommodation: audio is not analysed, whatever the client sends
            pipeline.ingest_audio(data)
    except WebSocketDisconnect:
        logger.info("Audio WS disconnected: session=%s", _log_sid(session_id))


@router.websocket("/ws/behavioral/{session_id}")
async def ws_behavioral(ws: WebSocket, session_id: str):
    """
    Behavioral signal ingestion via WebSocket.
    Expects JSON payloads with keystroke, mouse, and event data.
    """
    await ws.accept()
    entry = _sessions.get(session_id)
    if not entry:
        # Logged deliberately: a 4004 is the signature of a ROUTING problem, not a
        # client error. _sessions is process-local, so this means the socket reached
        # a different process than the one that ran /sessions/start — more than one
        # proctor worker, or a proxy sending WS and HTTP to different upstreams.
        # This used to close silently, leaving the client on "Checking..." with no
        # server-side evidence whatsoever.
        logger.warning(
            "Proctor WS 4004 on %s: session=%s not in THIS process (pid=%s, %d live "
            "session(s)). Proctoring must run as ONE worker — check WEB_CONCURRENCY "
            "in hireflow-proctor.service and the proxy's WebSocket route.",
            "/".join(str(getattr(getattr(ws, "url", None), "path", "?")).split("/")[:-1]),
            _log_sid(session_id), os.getpid(), len(_sessions),
        )
        await ws.close(code=4004, reason="Session not found")
        return
    if not _ws_authorized(ws, entry):
        # 4401: authenticated with the session's token, or not at all.
        await ws.close(code=4401, reason="Unauthorized")
        return

    pipeline = entry["pipeline"]
    logger.info("Behavioral WS connected: session=%s", _log_sid(session_id))

    try:
        while True:
            data = await ws.receive_text()
            try:
                payload = json.loads(data)
                pipeline.ingest_behavioral_signals(payload)
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        logger.info("Behavioral WS disconnected: session=%s", _log_sid(session_id))
