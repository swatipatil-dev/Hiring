"""
Module 4 — Pose & Movement Detection: Phone gesture, posture, out-of-frame, second person.
Uses MediaPipe PoseLandmarker (Tasks API) with behavioral baseline capture.
"""
import os
import time
import math
import logging
import threading
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

import numpy as np
import cv2

from .. import model_registry

logger = logging.getLogger(__name__)

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
_POSE_MODEL = os.path.join(_MODEL_DIR, "pose_landmarker.task")


@dataclass
class PoseAnalysisResult:
    # Phone gesture
    phone_gesture_detected: bool = False
    phone_gesture_confidence: float = 0.0
    # Posture
    leaning_forward: bool = False
    leaning_sideways: bool = False
    head_down: bool = False
    posture_deviation: float = 0.0
    # Out-of-frame
    candidate_left_frame: bool = False
    visibility_score: float = 1.0
    # Second person
    person_count: int = 0
    new_person_detected: bool = False
    # Meta
    processing_time_ms: float = 0.0
    flags: List[str] = field(default_factory=list)


class BaselineCapturer:
    """Captures first N seconds of pose data to establish behavioral baseline."""

    def __init__(self, duration_seconds: float = 30.0, fps: int = 15):
        self.target_frames = int(duration_seconds * fps)
        self.shoulder_angles: List[float] = []
        self.nose_shoulder_vectors: List[np.ndarray] = []
        self.wrist_y_positions: List[float] = []
        self.complete = False

    def add_sample(self, shoulder_angle: float, nose_shoulder_vec: np.ndarray, wrist_y: float):
        if self.complete:
            return
        self.shoulder_angles.append(shoulder_angle)
        self.nose_shoulder_vectors.append(nose_shoulder_vec)
        self.wrist_y_positions.append(wrist_y)
        if len(self.shoulder_angles) >= self.target_frames:
            self.complete = True
            logger.info("Pose baseline captured (%d frames)", len(self.shoulder_angles))

    @property
    def mean_shoulder_angle(self) -> float:
        return float(np.mean(self.shoulder_angles)) if self.shoulder_angles else 0.0

    @property
    def std_shoulder_angle(self) -> float:
        return float(np.std(self.shoulder_angles)) if self.shoulder_angles else 10.0

    @property
    def mean_wrist_y(self) -> float:
        return float(np.mean(self.wrist_y_positions)) if self.wrist_y_positions else 0.5


class PoseAnalyzer:
    """
    Detects suspicious physical behaviors via MediaPipe Pose.
    """

    def __init__(self, config=None):
        from ..config import PoseConfig
        self.cfg = config or PoseConfig()

        # The PoseLandmarker is borrowed per call from the shared pool rather than
        # built here — see proctoring/model_registry.py. `_models` holds the
        # borrowed set for the duration of analyze(), and the properties below
        # keep the existing `self._pose` / `self._mp` references working.
        self._models = None

        # Previously absent: PoseLandmarker.detect() was called unguarded. That was
        # only safe because each pipeline owned a private graph. Now the graph is
        # shared, so a lock is mandatory — the same crash FaceAnalyzer documents
        # ("access violation") applies equally here.
        self._lock = threading.RLock()

        # ── Per-session state (never shared) ──
        self.baseline = BaselineCapturer(self.cfg.baseline_capture_seconds)
        self._absence_start: Optional[float] = None
        self._prev_wrist_y: float = 0.5
        self._wrist_velocity_history: List[float] = []

    @property
    def _pose(self):
        return self._models.pose_landmarker if self._models else None

    @property
    def _mp(self):
        return self._models.mp if self._models else None

    def analyze(self, frame: np.ndarray) -> PoseAnalysisResult:
        """Analyze a single BGR frame for pose-based cheating signals."""
        with self._lock:
            with model_registry.borrow() as models:
                self._models = models
                try:
                    return self._analyze_unlocked(frame)
                finally:
                    self._models = None

    def _analyze_unlocked(self, frame: np.ndarray) -> PoseAnalysisResult:
        t0 = time.perf_counter()
        result = PoseAnalysisResult()

        if self._pose is None:
            result.processing_time_ms = (time.perf_counter() - t0) * 1000
            return result

        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=rgb)
        pose_results = self._pose.detect(mp_image)

        if not pose_results.pose_landmarks:
            # No pose detected — candidate may have left
            if self._absence_start is None:
                self._absence_start = time.time()
            elif time.time() - self._absence_start > self.cfg.absence_duration_seconds:
                result.candidate_left_frame = True
                result.flags.append("CANDIDATE_LEFT")
            result.visibility_score = 0.0
            result.processing_time_ms = (time.perf_counter() - t0) * 1000
            return result

        self._absence_start = None
        lm = pose_results.pose_landmarks[0]  # first person's landmarks

        # Key landmark visibility
        key_vis = [lm[0].visibility, lm[11].visibility, lm[12].visibility]  # nose, l_shoulder, r_shoulder
        result.visibility_score = float(np.mean(key_vis))

        # --- Detection 1: Phone usage gesture ---
        result.phone_gesture_detected, result.phone_gesture_confidence = self._detect_phone_gesture(lm, h)

        # --- Detection 2: Suspicious posture ---
        shoulder_angle = self._calc_shoulder_angle(lm)
        nose_shoulder_vec = self._calc_nose_shoulder_vec(lm)
        wrist_y = (lm[15].y + lm[16].y) / 2

        if not self.baseline.complete:
            self.baseline.add_sample(shoulder_angle, nose_shoulder_vec, wrist_y)
        else:
            deviation = abs(shoulder_angle - self.baseline.mean_shoulder_angle)
            threshold = self.baseline.std_shoulder_angle * self.cfg.deviation_std_multiplier
            result.posture_deviation = round(deviation, 2)

            if deviation > max(threshold, self.cfg.lean_angle_threshold):
                # Determine type
                if nose_shoulder_vec[1] > 0.1:
                    result.leaning_forward = True
                    result.flags.append("LEANING_FORWARD")
                elif abs(nose_shoulder_vec[0]) > 0.1:
                    result.leaning_sideways = True
                    result.flags.append("LEANING_SIDEWAYS")

            if lm[0].y > (lm[11].y + lm[12].y) / 2:
                result.head_down = True
                result.flags.append("HEAD_DOWN")

        if result.phone_gesture_detected:
            result.flags.append("PHONE_GESTURE")

        # --- Detection 3: Out-of-frame ---
        if result.visibility_score < self.cfg.absence_visibility_threshold:
            if self._absence_start is None:
                self._absence_start = time.time()
            elif time.time() - self._absence_start > self.cfg.absence_duration_seconds:
                result.candidate_left_frame = True
                result.flags.append("CANDIDATE_LEFT")
        else:
            self._absence_start = None

        # --- Detection 4: Second person ---
        # MediaPipe Pose detects single person; use landmark count heuristic
        # For multi-person, we rely on YOLO (Module 2). Set count = 1 here.
        result.person_count = 1

        result.processing_time_ms = (time.perf_counter() - t0) * 1000
        return result

    def draw_skeleton(self, frame: np.ndarray) -> np.ndarray:
        """Draw pose skeleton on frame for evidence visualization."""
        # Simplified — Tasks API doesn't have draw_landmarks directly
        return frame.copy()

    def _detect_phone_gesture(self, lm, h: int) -> Tuple[bool, float]:
        """Detect hand-to-face phone holding gesture."""
        nose_y = lm[0].y
        l_wrist_y, r_wrist_y = lm[15].y, lm[16].y
        l_elbow_y, r_elbow_y = lm[13].y, lm[14].y

        # Wrist near face level
        threshold = self.cfg.phone_gesture_y_threshold
        l_near = abs(l_wrist_y - nose_y) < threshold and l_wrist_y < l_elbow_y
        r_near = abs(r_wrist_y - nose_y) < threshold and r_wrist_y < r_elbow_y

        if not (l_near or r_near):
            return False, 0.0

        # Check wrist velocity (distinguish typing vs holding)
        current_wrist_y = min(l_wrist_y, r_wrist_y)
        velocity = abs(current_wrist_y - self._prev_wrist_y)
        self._prev_wrist_y = current_wrist_y
        self._wrist_velocity_history.append(velocity)
        if len(self._wrist_velocity_history) > 30:
            self._wrist_velocity_history.pop(0)

        avg_velocity = np.mean(self._wrist_velocity_history) if self._wrist_velocity_history else 0
        # Low velocity = holding still (phone), high velocity = typing
        if avg_velocity < 0.02:
            confidence = min(1.0 - avg_velocity * 20, 1.0)
            return True, round(confidence, 3)

        return False, 0.0

    def _calc_shoulder_angle(self, lm) -> float:
        """Calculate angle between shoulders and horizontal."""
        ls = np.array([lm[11].x, lm[11].y])
        rs = np.array([lm[12].x, lm[12].y])
        diff = rs - ls
        angle = math.degrees(math.atan2(diff[1], diff[0]))
        return angle

    def _calc_nose_shoulder_vec(self, lm) -> np.ndarray:
        """Vector from shoulder midpoint to nose (normalized)."""
        mid = np.array([(lm[11].x + lm[12].x) / 2, (lm[11].y + lm[12].y) / 2])
        nose = np.array([lm[0].x, lm[0].y])
        return nose - mid

    def release(self):
        """Drop this session's baseline.

        Does NOT close the landmarker — it belongs to a pooled ModelSet shared
        across sessions and is closed once by model_registry.shutdown().
        """
        with self._lock:
            self._models = None
            self.baseline = BaselineCapturer(self.cfg.baseline_capture_seconds)
            self._absence_start = None
            self._wrist_velocity_history = []
