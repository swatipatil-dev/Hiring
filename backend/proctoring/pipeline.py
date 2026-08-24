"""
Proctoring Pipeline — orchestrates all 5 AI modules and computes a unified cheating score.
Handles async frame processing at 15+ FPS.
"""
import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional, Dict, List

import numpy as np

from . import model_registry
from .config import ProctorConfig
from .runtime import get_executor

logger = logging.getLogger(__name__)

# Severity weights for cheating score
SEVERITY_WEIGHTS = {"CRITICAL": 25, "HIGH": 15, "MEDIUM": 8, "LOW": 3}
MODULE_WEIGHTS = {"face": 0.30, "object": 0.25, "audio": 0.20, "pose": 0.15, "behavioral": 0.10}


@dataclass
class ProctorFrame:
    """Result of processing a single frame through the full pipeline."""
    timestamp: float = 0.0
    cheating_score: float = 0.0  # 0-100
    module_scores: Dict[str, float] = field(default_factory=dict)
    flags: List[str] = field(default_factory=list)
    face_result: Optional[dict] = None
    object_result: Optional[dict] = None
    audio_result: Optional[dict] = None
    pose_result: Optional[dict] = None
    behavioral_result: Optional[dict] = None
    processing_time_ms: float = 0.0


class ProctorPipeline:
    """
    Main orchestrator. Processes video frames through all modules asynchronously.
    """

    def __init__(self, config: Optional[ProctorConfig] = None):
        self.cfg = config or ProctorConfig()
        # Frame work runs on the process-wide executor (see runtime.py). A pool
        # per session multiplied thread count by the number of candidates, which
        # is what actually broke first under load — not memory.
        self._frame_count = 0
        self._violation_count = 0
        self._score_history: List[float] = []
        self._last_frame_result: Optional[ProctorFrame] = None
        self._last_behavioral_at = 0.0

        # Lazy-init modules (avoid importing heavy libs at import time)
        self._face = None
        self._objects = None
        self._audio = None
        self._pose = None
        self._behavioral = None
        self._initialized = False
        # module name -> error string, for any non-fatal module that failed to load
        self.init_errors: Dict[str, str] = {}

    def initialize(self):
        """
        Initialize all AI modules. Call once at session start.

        Raises RuntimeError if the face module fails. Everything else degrades: a
        missing object detector still leaves useful proctoring, but without a face
        analyzer there is no identity verification, no gaze, no head pose — the
        pipeline would report clean frames forever and the test would look proctored
        when it isn't. That silent failure was the previous behaviour; it must be loud.
        """
        if self._initialized:
            return

        logger.info("Initializing proctoring pipeline...")
        t0 = time.perf_counter()

        # Constructing the analyzers is now nearly free — they hold counters, not
        # models. The expensive part is building a ModelSet, which happens once per
        # inference slot and is shared. Do it here so a machine that cannot load the
        # face models still fails at session start rather than mid-test.
        try:
            model_registry.warm(1)
            if not model_registry.capabilities().get("landmarker") and \
               not model_registry.capabilities().get("sface"):
                raise RuntimeError(
                    "neither the MediaPipe FaceLandmarker nor the SFace recognizer "
                    "could be loaded"
                )
        except Exception as e:
            self.init_errors["face"] = str(e)
            raise RuntimeError(f"Face models unavailable — cannot proctor: {e}") from e

        try:
            from .core.face_analyzer import FaceAnalyzer
            self._face = FaceAnalyzer(self.cfg.face)
        except Exception as e:
            self.init_errors["face"] = str(e)
            raise RuntimeError(f"FaceAnalyzer init failed — cannot proctor: {e}") from e

        for name, loader in (
            ("objects", self._init_objects),
            ("audio", self._init_audio),
            ("pose", self._init_pose),
            ("behavioral", self._init_behavioral),
        ):
            try:
                loader()
            except Exception as e:
                # Degraded but still useful — record it so session_status can report it
                # instead of leaving operators to guess from the logs.
                self.init_errors[name] = str(e)
                logger.error("%s init failed — proctoring degraded: %s", name, e)

        self._initialized = True
        logger.info("Proctoring pipeline ready in %.2fs (degraded: %s)",
                    time.perf_counter() - t0,
                    ", ".join(self.init_errors) or "none")

    def _init_objects(self):
        from .core.object_detector import CheatObjectDetector
        self._objects = CheatObjectDetector(self.cfg.objects)

    def _init_audio(self):
        from .core.audio_proctor import AudioProctor
        self._audio = AudioProctor(self.cfg.audio)

    def _init_pose(self):
        from .core.pose_analyzer import PoseAnalyzer
        self._pose = PoseAnalyzer(self.cfg.pose)

    def _init_behavioral(self):
        # Leaving self._behavioral as None is what keeps "behavioral" out of
        # module_scores, so _compute_cheating_score stops counting its weight.
        if not self.cfg.behavioral.enabled:
            logger.info("Behavioral engine disabled — excluded from cheating score")
            return
        from .core.behavioral_engine import BehavioralEngine
        self._behavioral = BehavioralEngine(self.cfg.behavioral)

    def register_face(self, frame: np.ndarray) -> bool:
        """Register candidate face at session start."""
        if self._face:
            return self._face.register_face(frame)
        return False

    async def process_frame(self, frame: np.ndarray, session_id: str = "default") -> ProctorFrame:
        """
        Process a single video frame through all vision modules asynchronously.
        Audio and behavioral are analyzed separately via their own streams.
        """
        t0 = time.perf_counter()
        self._frame_count += 1
        result = ProctorFrame(timestamp=time.time())

        loop = asyncio.get_event_loop()
        executor = get_executor()

        # Run vision modules in parallel using the shared thread pool
        tasks = []
        if self._face:
            tasks.append(("face", loop.run_in_executor(executor, self._face.analyze, frame)))
        if self._objects:
            tasks.append(("object", loop.run_in_executor(executor, self._objects.detect, frame, session_id)))
        if self._pose:
            tasks.append(("pose", loop.run_in_executor(executor, self._pose.analyze, frame)))

        for name, task in tasks:
            try:
                r = await task
                if name == "face":
                    result.face_result = self._result_to_dict(r)
                    result.flags.extend(getattr(r, "flags", []))
                    result.module_scores["face"] = self._calc_face_score(r)
                elif name == "object":
                    result.object_result = self._result_to_dict(r)
                    result.flags.extend(getattr(r, "flags", []))
                    result.module_scores["object"] = self._calc_object_score(r)
                elif name == "pose":
                    result.pose_result = self._result_to_dict(r)
                    result.flags.extend(getattr(r, "flags", []))
                    result.module_scores["pose"] = self._calc_pose_score(r)
            except Exception as e:
                logger.error("Module %s failed: %s", name, e)

        # Audio analysis (if buffer has data). Offloaded like the vision modules:
        # it can run a voice-embedding comparison (and, where the optional deps are
        # installed, a transcription), and doing that inline blocked the event loop
        # for EVERY session on this worker, not just this one.
        if self._audio:
            try:
                audio_r = await loop.run_in_executor(executor, self._audio.analyze)
                result.audio_result = self._result_to_dict(audio_r)
                result.flags.extend(getattr(audio_r, "flags", []))
                result.module_scores["audio"] = self._calc_audio_score(audio_r)
            except Exception as e:
                logger.error("Audio analysis failed: %s", e)

        # Behavioral analysis (periodic, not every frame). Time-based rather than
        # frame-based: frame rate now adapts to server throughput, so a frame counter
        # would silently stretch this interval on slower machines.
        # Also offloaded: analyze() fits an IsolationForest on the session's own
        # feature history, which is not something to run on the event loop.
        if self._behavioral and (result.timestamp - self._last_behavioral_at) >= 5.0:
            self._last_behavioral_at = result.timestamp
            try:
                beh_r = await loop.run_in_executor(executor, self._behavioral.analyze)
                result.behavioral_result = self._result_to_dict(beh_r)
                result.flags.extend(getattr(beh_r, "flags", []))
                result.module_scores["behavioral"] = self._calc_behavioral_score(beh_r)
            except Exception as e:
                logger.error("Behavioral analysis failed: %s", e)

        # Cross-check: if YOLO detected extra persons, compare with face count.
        # If face analyzer sees only 1 face, the extra persons are facing away from the
        # screen (180° opposite) — not a threat. Suppress the flags.
        # If face analyzer sees multiple faces, the extra persons ARE facing the screen
        # — confirm the CRITICAL flag.
        if result.object_result and result.face_result:
            yolo_person_count = result.object_result.get("person_count", 0)
            face_count = result.face_result.get("face_count", 0)
            if yolo_person_count > 1 and face_count <= 1:
                # Extra person(s) facing away — suppress the CRITICAL person flag
                result.object_result["person_count"] = 1
                result.object_result["objects"] = [
                    o for o in result.object_result.get("objects", [])
                    if (o.get("label") if isinstance(o, dict) else getattr(o, "label", None)) != "person"
                ]
                result.flags = [f for f in result.flags if f != "PERSON_DETECTED"]
                # Recalculate object score without the extra person penalty
                result.module_scores["object"] = self._calc_object_score_from_dict(result.object_result)
                logger.info("Suppressed second-person flag: extra person(s) facing away from screen (no matching face)")
            elif yolo_person_count > 1 and face_count > 1:
                # Extra persons confirmed facing the screen — escalate severity
                logger.warning("CONFIRMED extra person(s) facing screen: %d persons, %d faces", yolo_person_count, face_count)

        # Compute unified cheating score (weighted average)
        result.cheating_score = self._compute_cheating_score(result.module_scores)
        self._score_history.append(result.cheating_score)

        result.processing_time_ms = (time.perf_counter() - t0) * 1000
        self._last_frame_result = result
        return result

    def ingest_audio(self, pcm_bytes: bytes):
        """Forward audio data to audio module."""
        if self._audio:
            self._audio.ingest_audio_chunk(pcm_bytes)

    def ingest_behavioral_signals(self, payload: dict):
        """Forward behavioral signals to behavioral engine."""
        if self._behavioral:
            self._behavioral.ingest_signals(payload)

    def _compute_cheating_score(self, module_scores: Dict[str, float]) -> float:
        """Weighted average of module scores (0-100)."""
        total = 0.0
        weight_sum = 0.0
        for module, weight in MODULE_WEIGHTS.items():
            if module in module_scores:
                total += module_scores[module] * weight
                weight_sum += weight
        if weight_sum == 0:
            return 0.0
        return round(total / weight_sum, 1)

    def _calc_face_score(self, r) -> float:
        score = 0.0
        if not r.face_detected:
            score += 40
        if r.face_count > 1:
            score += 30
        if r.head_turned:
            score += 15
        if r.gaze_deviation_score > 0.5:
            score += 10 * r.gaze_deviation_score
        # Only penalize a CONFIRMED identity mismatch (a reference was registered and
        # it didn't match). Absence of a reference photo is "unknown", not an impostor —
        # so head-turn/multi-face/object scoring still works without a photo.
        if getattr(r, "has_reference", False) and not r.identity_match:
            score += 85  # different face = almost certain cheating
        return min(score, 100)

    def _calc_object_score(self, r) -> float:
        severity_scores = {"CRITICAL": 50, "HIGH": 35, "MEDIUM": 20}
        score = sum(severity_scores.get(o.severity, 5) for o in r.objects)
        return min(score, 100)

    def _calc_object_score_from_dict(self, obj_dict: dict) -> float:
        """Recalculate object score from a dict (used after filtering suppressed detections)."""
        severity_scores = {"CRITICAL": 50, "HIGH": 35, "MEDIUM": 20}
        score = sum(severity_scores.get(o.get("severity", ""), 5) for o in obj_dict.get("objects", []))
        return min(score, 100)

    def _calc_audio_score(self, r) -> float:
        score = 0.0
        # foreign_speech, not voice_count_estimate: the count is per-window, so a window
        # where only the second person talks reads as one voice while still being the
        # thing worth scoring.
        if getattr(r, "foreign_speech", False):
            score += 40
        if r.flagged_keywords:
            score += 20 * len(r.flagged_keywords)
        if r.mic_muted:
            score += 15
        if "BACKGROUND_NOISE" in getattr(r, "flags", []):
            score += 10
        return min(score, 100)

    def _calc_pose_score(self, r) -> float:
        score = 0.0
        if r.phone_gesture_detected:
            score += 35
        if r.leaning_forward or r.leaning_sideways:
            score += 15
        if r.head_down:
            score += 10
        if r.candidate_left_frame:
            score += 50
        return min(score, 100)

    def _calc_behavioral_score(self, r) -> float:
        if r.is_anomaly:
            return min(abs(r.anomaly_score) * 50 + 30, 100)
        return 0.0

    @staticmethod
    def _result_to_dict(r) -> dict:
        """Convert dataclass result to dict, excluding numpy arrays."""
        from dataclasses import fields, asdict
        d = {}
        for k, v in r.__dict__.items():
            if isinstance(v, np.ndarray):
                continue
            if hasattr(v, '__dataclass_fields__'):
                d[k] = {fk: fv for fk, fv in v.__dict__.items() if not isinstance(fv, np.ndarray)}
            elif isinstance(v, list) and v and hasattr(v[0], '__dataclass_fields__'):
                d[k] = [{fk: fv for fk, fv in item.__dict__.items() if not isinstance(fv, np.ndarray)} for item in v]
            else:
                d[k] = v
        return d

    def get_summary(self) -> dict:
        """Return session summary."""
        avg_score = float(np.mean(self._score_history)) if self._score_history else 0.0
        max_score = float(max(self._score_history)) if self._score_history else 0.0
        return {
            "frames_processed": self._frame_count,
            "avg_cheating_score": round(avg_score, 1),
            "max_cheating_score": round(max_score, 1),
            "total_flags": len(self._score_history),
            # Which modules failed to load, and whether the face module is running the
            # full landmarker or the reduced Haar fallback. Without this a degraded
            # pipeline reports clean frames and looks like a clean test.
            "degraded_modules": sorted(self.init_errors),
            # Asked of the registry, not the analyzer: the analyzer's engine flags
            # are views onto a borrowed model set and read False outside a borrow.
            "face_engine": ("none" if not self._face else
                            "landmarker" if model_registry.capabilities().get("landmarker")
                            else "haar"),
            "audio_engine": (getattr(self._audio, "diarization_engine", "none")
                             if self._audio else "none"),
        }

    def shutdown(self):
        """Release this session's resources.

        Deliberately does NOT touch the executor: it is shared process-wide, so
        shutting it down here would stop frame processing for every other active
        session. It is released from the app lifespan via runtime.shutdown_runtime().
        """
        if self._face:
            self._face.release()
        if self._pose:
            self._pose.release()
        logger.info("Proctoring pipeline shut down")
