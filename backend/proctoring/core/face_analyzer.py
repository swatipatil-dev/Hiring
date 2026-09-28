"""
Module 1 — Face Detection, Head Pose, Gaze Analysis, Face Matching.
Uses MediaPipe FaceLandmarker (Tasks API, 478 landmarks) with OpenCV Haar cascade fallback.
Face matching uses OpenCV DNN SFace model (128-dim embeddings) when available,
falling back to multi-method histogram/LBP/structural matching.
Includes lip movement (MAR) and blink detection (EAR) for liveness.

Stability features:
- Moving-average smoothing on yaw/pitch/roll (N=5 frames)
- Multi-face requires 5 consecutive frames to confirm (kills Haar false positives)
- Head-turn requires 3 consecutive frames to flag (kills jitter)
- Haar cascade disabled for multi-face count (MediaPipe-only = no shadow ghosts)
"""
import contextlib
import os
import time
import logging
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import numpy as np
import cv2

from .. import model_registry

logger = logging.getLogger(__name__)

# Model file paths
_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "models")
_FACE_LANDMARKER_MODEL = os.path.join(_MODEL_DIR, "face_landmarker.task")
_YUNET_MODEL = os.path.join(_MODEL_DIR, "face_detection_yunet_2023mar.onnx")
_SFACE_MODEL = os.path.join(_MODEL_DIR, "face_recognition_sface_2021dec.onnx")

# NOTE: model loading (FaceLandmarker, SFace, YuNet, cascades) moved to
# proctoring/model_registry.py so the weights are shared across sessions. The
# availability probe that used to live here went with it — capability is now
# reported by model_registry.capabilities().


@dataclass
class FaceAnalysisResult:
    face_detected: bool = False
    face_count: int = 0
    detection_confidence: float = 0.0

    gaze_direction: str = "CENTER"
    gaze_deviation_score: float = 0.0

    pitch: float = 0.0
    yaw: float = 0.0
    roll: float = 0.0
    head_turned: bool = False

    identity_match: bool = False
    similarity_score: float = 0.0
    has_reference: bool = False  # True only if a reference face was registered (photo enrolled)

    lip_moving: bool = False
    mouth_aspect_ratio: float = 0.0

    blink_detected: bool = False
    eye_aspect_ratio: float = 0.0

    face_bbox: Optional[List[float]] = None
    all_face_bboxes: Optional[List[List[float]]] = None
    landmarks: Optional[List[List[float]]] = None
    eye_boxes: Optional[List[List[float]]] = None

    processing_time_ms: float = 0.0
    flags: List[str] = field(default_factory=list)


# 3D model points for head pose (solvePnP)
MODEL_POINTS_3D = np.array([
    (0.0, 0.0, 0.0),          # Nose tip
    (0.0, -330.0, -65.0),     # Chin
    (-225.0, 170.0, -135.0),  # Left eye left corner
    (225.0, 170.0, -135.0),   # Right eye right corner
    (-150.0, -150.0, -125.0), # Left mouth corner
    (150.0, -150.0, -125.0),  # Right mouth corner
], dtype=np.float64)


class FaceAnalyzer:

    # Key landmark indices (478 total with iris)
    _NOSE_TIP = 1
    _CHIN = 152
    _LEFT_EYE_OUTER = 33
    _RIGHT_EYE_OUTER = 263
    _LEFT_MOUTH = 61
    _RIGHT_MOUTH = 291
    _LEFT_EYE_INNER = 133
    _RIGHT_EYE_INNER = 362
    _LEFT_IRIS_CENTER = 468
    _RIGHT_IRIS_CENTER = 473
    _LEFT_EYE_IDX = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246]
    _RIGHT_EYE_IDX = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398]

    # MAR landmarks
    _UPPER_LIP = 13
    _LOWER_LIP = 14
    _LEFT_LIP = 61
    _RIGHT_LIP = 291

    # EAR landmarks
    _LEFT_EYE_EAR = [33, 160, 158, 133, 153, 144]
    _RIGHT_EYE_EAR = [362, 385, 387, 263, 373, 380]

    # ── Stability thresholds ──
    _SMOOTHING_WINDOW = 2        # minimal smoothing — fast response
    _MULTI_FACE_CONFIRM = 3      # consecutive frames before flagging multi-face
    _HEAD_TURN_CONFIRM = 1       # instant detection — 1 frame
    _HEAD_TURN_RELEASE = 1       # instant release — 1 OK frame clears flag
    _NO_FACE_CONFIRM = 2         # consecutive frames before flagging no-face

    # ── Head pose thresholds (degrees) ──
    # Wide enough to allow looking at screen corners + keyboard
    _YAW_THRESHOLD = 35.0        # ±35° left/right (allows screen corner glances)
    _PITCH_THRESHOLD = 25.0      # ±25° up/down (allows looking at keyboard)
    _ROLL_THRESHOLD = 20.0       # ±20° tilt

    def __init__(self, config=None):
        from ..config import FaceConfig
        self.cfg = config or FaceConfig()
        self._frame_count = 0
        self._last_result = FaceAnalysisResult()
        self._consecutive_no_face = 0

        # ── Smoothing buffers ──
        self._yaw_history = deque(maxlen=self._SMOOTHING_WINDOW)
        self._pitch_history = deque(maxlen=self._SMOOTHING_WINDOW)
        self._roll_history = deque(maxlen=self._SMOOTHING_WINDOW)
        self._ear_history = deque(maxlen=10)
        self._blink_counter = 0

        # ── Consecutive-frame counters for stability ──
        self._consecutive_turn_frames = 0
        self._consecutive_ok_frames = 0
        self._consecutive_multi_face_frames = 0
        self._head_turn_locked = False  # sticky flag

        # Serialises every use of the native MediaPipe / OpenCV DNN handles below.
        #
        # These are NOT thread-safe, and they are genuinely reached from different
        # threads: analyze() runs in ProctorPipeline's ThreadPoolExecutor, while
        # register_face() is driven by enrollment (the /enroll-face request and the
        # background reference-photo load). Overlapping the two crashed the face module
        # outright — "Module face failed: exception: access violation reading 0x...",
        # after which the session produced no further face results and the client sat on
        # "Checking…" forever.
        #
        # RLock because _match_face and friends are called from inside the already-locked
        # analyze() path.
        self._lock = threading.RLock()

        # ── Face matching state (per-session — this is the part that must NOT be shared) ──
        self._registered_face_gray = None
        self._registered_histogram = None
        self._registered_lbp = None
        self._registered_geometry = None
        self._registered_sface_embedding = None
        # (identity_match, similarity) from the last frame that actually ran _match_face
        # against the CURRENT baseline; None until one has. Skip frames reuse this, never
        # _last_result — see _apply_identity_check.
        self._last_identity: Optional[Tuple[bool, float]] = None

        # The models themselves are NOT owned here any more. They live in
        # proctoring/model_registry.py and are borrowed for the duration of each
        # public call; `_models` holds the borrowed set meanwhile, and the
        # properties below keep every existing `self._landmarker` / `self._sface_recognizer`
        # / cascade reference working unchanged.
        #
        # This is what decouples memory from concurrency: an analyzer instance is
        # now a few kilobytes of counters, so 50 sessions cost 50 x KB instead of
        # 50 x ~55 MB of duplicated weights.
        self._models = None

    # ══════════════════════════════════════════════════
    #  BORROWED MODELS
    # ══════════════════════════════════════════════════
    #
    # Read-only views onto the currently-borrowed ModelSet. They exist so the
    # analysis code below can go on saying `self._landmarker` and `self._sface_recognizer`
    # while the objects behind those names are shared and pooled. Outside a
    # borrow they read as None/False, which is the same "module unavailable"
    # state the code already handled.

    @contextlib.contextmanager
    def _borrowed(self):
        """Hold a model slot for this call.

        Callers MUST already hold `self._lock` — locking before borrowing (never
        the reverse) is what prevents a thread from holding a slot while waiting
        on a session lock owned by a thread waiting for a slot.
        """
        if self._models is not None:      # already inside an outer borrow
            yield self._models
            return
        with model_registry.borrow() as models:
            self._models = models
            try:
                yield models
            finally:
                self._models = None

    @property
    def _landmarker(self):
        return self._models.face_landmarker if self._models else None

    @property
    def _sface_recognizer(self):
        return self._models.sface if self._models else None

    @property
    def _yunet_detector(self):
        return self._models.yunet if self._models else None

    @property
    def _face_cascade(self):
        return self._models.face_cascade if self._models else None

    @property
    def _eye_cascade(self):
        return self._models.eye_cascade if self._models else None

    @property
    def _profile_cascade(self):
        return self._models.profile_cascade if self._models else None

    @property
    def _use_landmarker(self) -> bool:
        return bool(self._models and self._models.use_landmarker)

    @property
    def _use_sface(self) -> bool:
        return bool(self._models and self._models.use_sface)

    def _has_registration(self) -> bool:
        """True if an identity baseline was enrolled for this session.

        Set on `result.has_reference` at the TOP of both analysis paths, before
        any early return. It used to be assigned only in the matching block, which
        every no-face frame returns before reaching — so a session with a perfectly
        good reference photo reported has_reference=False whenever the candidate
        looked away. The frontend keys its live self-enrollment on that field
        (OnlineTest.jsx), so it could retry enrollment mid-test.
        """
        return (self._registered_face_gray is not None
                or self._registered_sface_embedding is not None)

    # ══════════════════════════════════════════════════
    #  FACE REGISTRATION (untouched per user request)
    # ══════════════════════════════════════════════════

    def register_face(self, frame: np.ndarray) -> bool:
        """Register the identity baseline. Serialised against analyze() — see _lock."""
        with self._lock:
            with self._borrowed():
                # A match against the previous baseline says nothing about the new one.
                self._last_identity = None
                return self._register_face_unlocked(frame)

    # Enrollment quality floors. A baseline is registered ONCE and every later frame is
    # judged against it, so accepting a bad one poisons the whole session — the candidate
    # then sits at a few percent similarity and gets flagged for identity mismatch against
    # their own face. Live enrollment fires ~600ms after the camera opens, which is often
    # before auto-exposure has settled, so a near-black frame is the common failure.
    _ENROLL_MIN_FACE_RATIO = 0.12   # face width as a fraction of frame width
    _ENROLL_MIN_BRIGHTNESS = 45.0   # mean luma of the face crop (0-255)

    def enrollment_quality(self, frame: np.ndarray) -> Optional[str]:
        """Return None if the frame is fit to enroll from, else a human-readable reason."""
        if frame is None or frame.size == 0:
            return "no image"
        with self._lock:
            with self._borrowed():
                return self._enrollment_quality_unlocked(frame)

    def _enrollment_quality_unlocked(self, frame: np.ndarray) -> Optional[str]:
        h_img, w_img = frame.shape[:2]
        box = None
        if self._use_sface and self._yunet_detector is not None:
            try:
                self._yunet_detector.setInputSize((w_img, h_img))
                _, faces = self._yunet_detector.detect(frame)
                if faces is not None and len(faces) > 0:
                    box = max(faces, key=lambda f: f[2] * f[3])[:4]
            except Exception:
                box = None
        if box is None:
            return "no face detected"

        x, y, w, h = [int(v) for v in box]
        if w < self._ENROLL_MIN_FACE_RATIO * w_img:
            return "face too small — please move closer to the camera"

        x, y = max(0, x), max(0, y)
        crop = frame[y:y + max(1, h), x:x + max(1, w)]
        if crop.size == 0:
            return "face outside frame"
        brightness = float(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).mean())
        if brightness < self._ENROLL_MIN_BRIGHTNESS:
            return "too dark — please turn on a light facing you"
        return None

    def _register_face_unlocked(self, frame: np.ndarray) -> bool:
        if self._use_sface:
            try:
                h_img, w_img = frame.shape[:2]
                self._yunet_detector.setInputSize((w_img, h_img))
                _, yunet_faces = self._yunet_detector.detect(frame)
                if yunet_faces is not None and len(yunet_faces) > 0:
                    best = max(yunet_faces, key=lambda f: f[2] * f[3])
                    aligned = self._sface_recognizer.alignCrop(frame, best)
                    # .copy() so this session owns the buffer outright — the
                    # recognizer is shared across sessions now, and an enrollment
                    # is the one piece of state that must not be aliased.
                    self._registered_sface_embedding = self._sface_recognizer.feature(aligned).copy()
                    logger.info("Face registered via SFace DNN")
            except Exception as e:
                logger.warning("SFace registration failed: %s", e)

            # If SFace is the active matcher, an enrollment that produced no embedding is a
            # FAILED enrollment — do not let the Haar path below report success. Otherwise
            # _match_face (which requires a non-None embedding) silently drops to the
            # histogram/LBP fallback, whose composite score is compared against the SFace
            # cosine threshold of 0.363. That mismatch of scales is what showed a candidate
            # "Face Mismatch! 3%" against a baseline supposedly registered from their own
            # face. Returning False here makes the client retry with a better frame.
            if self._registered_sface_embedding is None:
                logger.warning("Enrollment produced no SFace embedding — reporting failure "
                               "so the caller retries rather than falling back to histogram "
                               "matching against an SFace threshold")
                return False

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        eq_gray = cv2.equalizeHist(gray)
        faces = self._face_cascade.detectMultiScale(eq_gray, 1.2, 4, minSize=(40, 40))
        if len(faces) == 0 and self._registered_sface_embedding is None:
            return False

        if len(faces) > 0:
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            face_roi = frame[y:y+h, x:x+w]
            face_resized = cv2.resize(face_roi, (100, 120))
            self._registered_face_gray = cv2.equalizeHist(
                cv2.cvtColor(face_resized, cv2.COLOR_BGR2GRAY)
            )
            hsv = cv2.cvtColor(face_roi, cv2.COLOR_BGR2HSV)
            self._registered_histogram = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
            cv2.normalize(self._registered_histogram, self._registered_histogram, 0, 255, cv2.NORM_MINMAX)
            self._registered_lbp = self._compute_lbp_histogram(self._registered_face_gray)
            face_gray_roi = eq_gray[y:y+h, x:x+w]
            eyes = self._eye_cascade.detectMultiScale(face_gray_roi, 1.1, 5, minSize=(15, 15))
            self._registered_geometry = self._compute_face_geometry(eyes, w, h)
        return True

    # ══════════════════════════════════════════════════
    #  MAIN ANALYSIS
    # ══════════════════════════════════════════════════

    def analyze(self, frame: np.ndarray) -> FaceAnalysisResult:
        with self._lock:
            with self._borrowed():
                if self._use_landmarker and self._landmarker is not None:
                    return self._analyze_landmarker(frame)
                return self._analyze_haar(frame)

    def _analyze_landmarker(self, frame: np.ndarray) -> FaceAnalysisResult:
        import mediapipe as mp

        t0 = time.perf_counter()
        self._frame_count += 1
        result = FaceAnalysisResult()
        result.has_reference = self._has_registration()
        h, w = frame.shape[:2]

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        detection = self._landmarker.detect(mp_image)

        # ──────────── NO FACE DETECTED ────────────
        if not detection.face_landmarks:
            self._consecutive_no_face += 1
            # Whoever comes back into frame gets compared afresh, not waved through on
            # the match of the person who left.
            self._last_identity = None
            self._yaw_history.clear()
            self._pitch_history.clear()
            self._roll_history.clear()

            if self._consecutive_no_face >= self._NO_FACE_CONFIRM:
                result.face_detected = False
                result.flags.append("NO_FACE")

                # Check for profile (side face) via Haar
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                profiles = self._profile_cascade.detectMultiScale(
                    cv2.equalizeHist(gray), 1.2, 4, minSize=(60, 60)
                )
                if len(profiles) > 0:
                    result.face_detected = True
                    result.face_count = 1
                    result.head_turned = True
                    result.yaw = 60.0
                    result.flags.append("HEAD_TURNED")
                    px, py, pw, ph = max(profiles, key=lambda f: f[2] * f[3])
                    result.face_bbox = [round(px/w, 4), round(py/h, 4), round(pw/w, 4), round(ph/h, 4)]
            else:
                # Grace period — use last result
                result.face_detected = self._last_result.face_detected
                result.face_count = self._last_result.face_count

            result.processing_time_ms = (time.perf_counter() - t0) * 1000
            self._last_result = result
            return result

        # ──────────── FACE(S) DETECTED ────────────
        result.face_detected = True
        self._consecutive_no_face = 0
        landmarker_count = len(detection.face_landmarks)

        # ── Face count: ONLY from MediaPipe (no Haar ghost faces) ──
        # MediaPipe is a real face detector — Haar picks up shadows/monitors
        face_bboxes = []
        for face_lms in detection.face_landmarks:
            fxs = [lm.x for lm in face_lms]
            fys = [lm.y for lm in face_lms]
            fx0, fx1 = min(fxs), max(fxs)
            fy0, fy1 = min(fys), max(fys)
            # Minimum face size filter — ignore tiny detections (< 5% of frame)
            fw_n = fx1 - fx0
            fh_n = fy1 - fy0
            if fw_n * fh_n > 0.0025:  # at least 5% x 5% of frame
                face_bboxes.append([round(fx0, 4), round(fy0, 4), round(fw_n, 4), round(fh_n, 4)])

        result.face_count = len(face_bboxes)
        result.all_face_bboxes = face_bboxes

        # ── Multiple-face: require N consecutive frames ──
        if result.face_count > 1:
            self._consecutive_multi_face_frames += 1
            if self._consecutive_multi_face_frames >= self._MULTI_FACE_CONFIRM:
                result.flags.append("MULTIPLE_FACES")
                print(f"[FACE] MULTIPLE_FACES confirmed: {result.face_count} faces for {self._consecutive_multi_face_frames} frames")
        else:
            self._consecutive_multi_face_frames = max(0, self._consecutive_multi_face_frames - 1)

        # ── Primary face = largest ──
        primary_idx = 0
        if len(face_bboxes) > 1:
            areas = [bb[2] * bb[3] for bb in face_bboxes]
            primary_idx = int(np.argmax(areas))

        lm_idx = min(primary_idx, landmarker_count - 1)
        lms = detection.face_landmarks[lm_idx]
        num_lms = len(lms)

        # ── Face bbox ──
        xs = [lm.x for lm in lms]
        ys = [lm.y for lm in lms]
        x_min, x_max = min(xs), max(xs)
        y_min, y_max = min(ys), max(ys)
        fw_n, fh_n = x_max - x_min, y_max - y_min
        result.face_bbox = [round(x_min, 4), round(y_min, 4), round(fw_n, 4), round(fh_n, 4)]
        result.detection_confidence = min(fw_n * fh_n * 20, 1.0)

        fx_px, fy_px = int(x_min * w), int(y_min * h)
        fw_px, fh_px = int(fw_n * w), int(fh_n * h)

        # ── All 478 landmarks for frontend overlay ──
        result.landmarks = [[round(lm.x, 4), round(lm.y, 4)] for lm in lms]

        # ══════════════════════════════════════════
        #  HEAD POSE via solvePnP + smoothing
        # ══════════════════════════════════════════
        raw_pitch, raw_yaw, raw_roll = 0.0, 0.0, 0.0
        if num_lms > max(self._NOSE_TIP, self._CHIN, self._LEFT_EYE_OUTER,
                         self._RIGHT_EYE_OUTER, self._LEFT_MOUTH, self._RIGHT_MOUTH):
            landmarks_2d = np.array([
                [lms[self._NOSE_TIP].x * w, lms[self._NOSE_TIP].y * h],
                [lms[self._CHIN].x * w, lms[self._CHIN].y * h],
                [lms[self._LEFT_EYE_OUTER].x * w, lms[self._LEFT_EYE_OUTER].y * h],
                [lms[self._RIGHT_EYE_OUTER].x * w, lms[self._RIGHT_EYE_OUTER].y * h],
                [lms[self._LEFT_MOUTH].x * w, lms[self._LEFT_MOUTH].y * h],
                [lms[self._RIGHT_MOUTH].x * w, lms[self._RIGHT_MOUTH].y * h],
            ], dtype=np.float64)
            raw_pitch, raw_yaw, raw_roll = self._calc_head_pose(landmarks_2d, w, h)

        # ── Moving-average smoothing ──
        self._yaw_history.append(raw_yaw)
        self._pitch_history.append(raw_pitch)
        self._roll_history.append(raw_roll)

        result.yaw = round(float(np.mean(self._yaw_history)), 1)
        result.pitch = round(float(np.mean(self._pitch_history)), 1)
        result.roll = round(float(np.mean(self._roll_history)), 1)

        # ══════════════════════════════════════════
        #  HEAD TURN DETECTION — solvePnP only (smoothed)
        #  No backup heuristics — they cause false positives
        # ══════════════════════════════════════════
        is_turned = (
            abs(result.yaw) > self._YAW_THRESHOLD or      # >30°
            abs(result.pitch) > self._PITCH_THRESHOLD      # >25°
        )
        # Roll ignored — tilting head isn't cheating

        # ── Consecutive-frame confirmation ──
        if is_turned:
            self._consecutive_turn_frames += 1
            self._consecutive_ok_frames = 0
        else:
            self._consecutive_ok_frames += 1
            if self._consecutive_ok_frames >= self._HEAD_TURN_RELEASE:
                self._consecutive_turn_frames = 0
                self._head_turn_locked = False

        if self._consecutive_turn_frames >= self._HEAD_TURN_CONFIRM:
            self._head_turn_locked = True

        result.head_turned = self._head_turn_locked
        if result.head_turned:
            result.flags.append("HEAD_TURNED")

        # Debug (every 25 frames)
        if self._frame_count % 25 == 0:
            print(
                f"[FACE] f={self._frame_count} yaw={result.yaw:.1f} pitch={result.pitch:.1f} "
                f"turned={result.head_turned} consec_t={self._consecutive_turn_frames} "
                f"consec_ok={self._consecutive_ok_frames} faces={result.face_count}"
            )

        # ══════════════════════════════════════════
        #  EYE BOXES + GAZE + MAR + EAR
        # ══════════════════════════════════════════

        # Eye boxes
        if num_lms > 473:
            left_eye_pts = [(lms[i].x, lms[i].y) for i in self._LEFT_EYE_IDX if i < num_lms]
            right_eye_pts = [(lms[i].x, lms[i].y) for i in self._RIGHT_EYE_IDX if i < num_lms]
            if left_eye_pts and right_eye_pts:
                result.eye_boxes = [
                    self._landmarks_to_box(left_eye_pts),
                    self._landmarks_to_box(right_eye_pts),
                ]

        # Gaze
        if num_lms > 473:
            result.gaze_direction, result.gaze_deviation_score = self._estimate_gaze_landmarks(lms)
        else:
            result.gaze_direction, result.gaze_deviation_score = "CENTER", 0.0
        if result.gaze_deviation_score > self.cfg.gaze_deviation_threshold:
            result.flags.append("SUSPICIOUS_GAZE")

        # Lip movement (MAR)
        result.mouth_aspect_ratio = self._compute_mar(lms)
        result.lip_moving = result.mouth_aspect_ratio > self.cfg.mar_threshold

        # Blink detection (EAR)
        ear = self._compute_ear(lms)
        result.eye_aspect_ratio = round(ear, 3)
        self._ear_history.append(ear)
        if len(self._ear_history) >= 3:
            prev_avg = np.mean(list(self._ear_history)[-3:-1]) if len(self._ear_history) > 2 else 0.3
            if ear < 0.2 and prev_avg > 0.22:
                result.blink_detected = True
                self._blink_counter += 1

        # ══════════════════════════════════════════
        #  FACE MATCHING (untouched per user request)
        # ══════════════════════════════════════════
        self._apply_identity_check(result, frame, fx_px, fy_px, fw_px, fh_px)

        result.processing_time_ms = (time.perf_counter() - t0) * 1000
        self._last_result = result
        return result

    def _apply_identity_check(self, result: FaceAnalysisResult, frame: np.ndarray,
                              x: int, y: int, w: int, h: int) -> None:
        """Set identity_match / similarity_score / IDENTITY_MISMATCH on a face frame.

        _match_face runs every `skip_frames` frames; in between, the last real match is
        reused. That reuse used to copy `_last_result`, which is not the same thing:
        `_last_result` is simply the previous frame, and any frame that never ran a match
        (the very first frame of a session, a no-face frame, a frame from before the
        baseline was registered) carries the dataclass default of identity_match=False,
        similarity 0.0. Copying it raised IDENTITY_MISMATCH at similarity 0.00 against a
        candidate who was never compared at all — on frame 1 of nearly every session, and
        again on the first skipped frame after every look-away.

        Now a skip frame reuses only a match that actually ran against the current
        baseline during the current stretch of the face being in view, and runs one
        immediately if there is none. The cache is dropped on enrollment and whenever the
        face leaves the frame.
        """
        if not self._has_registration():
            return
        due = self._frame_count % max(self.cfg.skip_frames, 1) == 0
        if due or self._last_identity is None:
            self._last_identity = self._match_face(frame, x, y, w, h)
        result.identity_match, result.similarity_score = self._last_identity
        if not result.identity_match:
            result.flags.append("IDENTITY_MISMATCH")

    # ══════════════════════════════════════════════════
    #  HELPER METHODS
    # ══════════════════════════════════════════════════

    def _landmarks_to_box(self, pts) -> List[float]:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0, x1 = min(xs), max(xs)
        y0, y1 = min(ys), max(ys)
        return [round(x0, 4), round(y0, 4), round(x1 - x0, 4), round(y1 - y0, 4)]

    def _estimate_gaze_landmarks(self, lms) -> Tuple[str, float]:
        try:
            l_iris = np.array([lms[self._LEFT_IRIS_CENTER].x, lms[self._LEFT_IRIS_CENTER].y])
            l_inner = np.array([lms[self._LEFT_EYE_INNER].x, lms[self._LEFT_EYE_INNER].y])
            l_outer = np.array([lms[self._LEFT_EYE_OUTER].x, lms[self._LEFT_EYE_OUTER].y])
            r_iris = np.array([lms[self._RIGHT_IRIS_CENTER].x, lms[self._RIGHT_IRIS_CENTER].y])
            r_inner = np.array([lms[self._RIGHT_EYE_INNER].x, lms[self._RIGHT_EYE_INNER].y])
            r_outer = np.array([lms[self._RIGHT_EYE_OUTER].x, lms[self._RIGHT_EYE_OUTER].y])

            l_eye_w = np.linalg.norm(l_inner - l_outer)
            r_eye_w = np.linalg.norm(r_inner - r_outer)
            if l_eye_w < 1e-6 or r_eye_w < 1e-6:
                return "UNKNOWN", 0.3

            l_ratio = np.linalg.norm(l_iris - l_outer) / l_eye_w
            r_ratio = np.linalg.norm(r_iris - r_outer) / r_eye_w
            avg_ratio = (l_ratio + r_ratio) / 2
            deviation = min(abs(avg_ratio - 0.5) * 2, 1.0)

            if deviation < 0.2:
                direction = "CENTER"
            elif avg_ratio < 0.4:
                direction = "LEFT"
            elif avg_ratio > 0.6:
                direction = "RIGHT"
            else:
                direction = "CENTER"
            return direction, round(deviation, 3)
        except (IndexError, AttributeError):
            return "UNKNOWN", 0.3

    def _compute_mar(self, lms) -> float:
        try:
            upper = np.array([lms[self._UPPER_LIP].x, lms[self._UPPER_LIP].y])
            lower = np.array([lms[self._LOWER_LIP].x, lms[self._LOWER_LIP].y])
            left = np.array([lms[self._LEFT_LIP].x, lms[self._LEFT_LIP].y])
            right = np.array([lms[self._RIGHT_LIP].x, lms[self._RIGHT_LIP].y])
            vertical = np.linalg.norm(upper - lower)
            horizontal = np.linalg.norm(left - right)
            return float(vertical / horizontal) if horizontal > 1e-6 else 0.0
        except (IndexError, AttributeError):
            return 0.0

    def _compute_ear(self, lms) -> float:
        try:
            def ear_one(indices):
                p = [np.array([lms[i].x, lms[i].y]) for i in indices]
                v1 = np.linalg.norm(p[1] - p[5])
                v2 = np.linalg.norm(p[2] - p[4])
                h = np.linalg.norm(p[0] - p[3])
                return (v1 + v2) / (2.0 * h) if h > 1e-6 else 0.3
            return float((ear_one(self._LEFT_EYE_EAR) + ear_one(self._RIGHT_EYE_EAR)) / 2.0)
        except (IndexError, AttributeError):
            return 0.3

    def _calc_head_pose(self, landmarks_2d, w, h):
        try:
            focal_length = w
            center = (w / 2, h / 2)
            cam = np.array([
                [focal_length, 0, center[0]],
                [0, focal_length, center[1]],
                [0, 0, 1],
            ], dtype=np.float64)
            dist = np.zeros((4, 1))
            ok, rvec, tvec = cv2.solvePnP(
                MODEL_POINTS_3D, landmarks_2d, cam, dist, flags=cv2.SOLVEPNP_ITERATIVE
            )
            if not ok:
                return 0.0, 0.0, 0.0
            rmat, _ = cv2.Rodrigues(rvec)
            angles, _, _, _, _, _ = cv2.RQDecomp3x3(rmat)
            pitch, yaw, roll = float(angles[0]), float(angles[1]), float(angles[2])

            # Normalize angles to [-90, 90] range
            # RQDecomp3x3 can return angles near ±180 when face is roughly frontal
            if abs(pitch) > 90:
                pitch = pitch - 180 if pitch > 0 else pitch + 180
            if abs(yaw) > 90:
                yaw = yaw - 180 if yaw > 0 else yaw + 180
            if abs(roll) > 90:
                roll = roll - 180 if roll > 0 else roll + 180

            return round(pitch, 1), round(yaw, 1), round(roll, 1)
        except Exception:
            return 0.0, 0.0, 0.0

    def _compute_lbp_histogram(self, gray_img: np.ndarray) -> np.ndarray:
        h, w = gray_img.shape
        lbp = np.zeros_like(gray_img)
        for i in range(1, h - 1):
            for j in range(1, w - 1):
                center = gray_img[i, j]
                code = 0
                code |= (1 << 7) if gray_img[i-1, j-1] >= center else 0
                code |= (1 << 6) if gray_img[i-1, j  ] >= center else 0
                code |= (1 << 5) if gray_img[i-1, j+1] >= center else 0
                code |= (1 << 4) if gray_img[i,   j+1] >= center else 0
                code |= (1 << 3) if gray_img[i+1, j+1] >= center else 0
                code |= (1 << 2) if gray_img[i+1, j  ] >= center else 0
                code |= (1 << 1) if gray_img[i+1, j-1] >= center else 0
                code |= (1 << 0) if gray_img[i,   j-1] >= center else 0
                lbp[i, j] = code
        hist, _ = np.histogram(lbp.ravel(), bins=256, range=(0, 256))
        hist = hist.astype(np.float32)
        hist /= (hist.sum() + 1e-7)
        return hist

    def _compute_face_geometry(self, eyes, face_w, face_h) -> Optional[dict]:
        if len(eyes) < 2:
            return None
        sorted_eyes = sorted(eyes, key=lambda e: e[0])
        le, re = sorted_eyes[0], sorted_eyes[-1]
        eye_dist = abs((re[0] + re[2] / 2) - (le[0] + le[2] / 2))
        return {
            "eye_dist_ratio": eye_dist / face_w if face_w > 0 else 0,
            "eye_size_ratio": (le[2] + re[2]) / (2 * face_w) if face_w > 0 else 0,
            "face_aspect": face_h / face_w if face_w > 0 else 0,
        }

    # ══════════════════════════════════════════════════
    #  HAAR CASCADE FALLBACK (kept for non-MediaPipe)
    # ══════════════════════════════════════════════════

    def _analyze_haar(self, frame: np.ndarray) -> FaceAnalysisResult:
        t0 = time.perf_counter()
        self._frame_count += 1
        result = FaceAnalysisResult()
        result.has_reference = self._has_registration()
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)

        faces = self._face_cascade.detectMultiScale(gray, 1.15, 5, minSize=(60, 60))
        profiles = self._profile_cascade.detectMultiScale(gray, 1.15, 5, minSize=(60, 60))

        all_faces = list(faces)
        for pf in profiles:
            if not any(self._iou(pf, ff) > 0.3 for ff in faces):
                all_faces.append(pf)

        # Drop tiny detections (< 5% x 5% of the frame) before counting — the same floor
        # _analyze_landmarker applies, so a face on a poster across the room cannot count
        # as a second person. The 60x60 minSize above already covers this at 480p/720p;
        # this is what keeps the two engines equivalent at higher resolutions, where 60px
        # is a smaller share of the frame.
        min_face_area = 0.0025 * w * h
        all_faces = [f for f in all_faces if f[2] * f[3] >= min_face_area]

        result.face_count = len(all_faces)

        if result.face_count == 0:
            self._consecutive_no_face += 1
            self._last_identity = None  # see _analyze_landmarker
            if self._consecutive_no_face >= self._NO_FACE_CONFIRM:
                result.face_detected = False
                result.flags.append("NO_FACE")
            result.processing_time_ms = (time.perf_counter() - t0) * 1000
            self._last_result = result
            return result

        if len(faces) == 0 and len(profiles) > 0:
            result.face_detected = True
            result.head_turned = True
            result.flags.append("HEAD_TURNED")
            result.yaw = 60.0

        result.face_detected = True
        self._consecutive_no_face = 0
        result.all_face_bboxes = [
            [round(bx/w, 4), round(by/h, 4), round(bw/w, 4), round(bh/h, 4)]
            for bx, by, bw, bh in all_faces
        ]

        # ── Multiple-face: require N consecutive frames ──
        # Mirrors _analyze_landmarker (see the matching block there). Without this the
        # fallback silently proctored without multi-face detection whenever MediaPipe was
        # unavailable, while identity/no-face kept working — so nothing looked broken.
        if result.face_count > 1:
            self._consecutive_multi_face_frames += 1
            if self._consecutive_multi_face_frames >= self._MULTI_FACE_CONFIRM:
                result.flags.append("MULTIPLE_FACES")
                print(f"[FACE] MULTIPLE_FACES confirmed (haar): {result.face_count} faces for {self._consecutive_multi_face_frames} frames")
        else:
            self._consecutive_multi_face_frames = max(0, self._consecutive_multi_face_frames - 1)

        if len(faces) > 0:
            fx, fy, fw, fh = max(faces, key=lambda f: f[2] * f[3])
        else:
            fx, fy, fw, fh = all_faces[0]

        result.detection_confidence = min(fw * fh / (w * h) * 20, 1.0)
        result.face_bbox = [round(fx/w, 4), round(fy/h, 4), round(fw/w, 4), round(fh/h, 4)]

        face_roi_gray = gray[fy:fy+fh, fx:fx+fw]
        eyes = self._eye_cascade.detectMultiScale(face_roi_gray, 1.1, 5, minSize=(15, 15))

        landmarks_2d = self._estimate_landmarks(fx, fy, fw, fh, eyes)
        if landmarks_2d is not None:
            raw_p, raw_y, raw_r = self._calc_head_pose(landmarks_2d, w, h)
            self._yaw_history.append(raw_y)
            self._pitch_history.append(raw_p)
            self._roll_history.append(raw_r)
            result.pitch = round(float(np.mean(self._pitch_history)), 1)
            result.yaw = round(float(np.mean(self._yaw_history)), 1)
            result.roll = round(float(np.mean(self._roll_history)), 1)
            result.landmarks = [[round(pt[0]/w, 4), round(pt[1]/h, 4)] for pt in landmarks_2d]

        is_turned = (
            abs(result.yaw) > self._YAW_THRESHOLD or
            abs(result.pitch) > self._PITCH_THRESHOLD
        )
        if len(faces) == 0 and len(profiles) > 0:
            is_turned = True

        if is_turned:
            self._consecutive_turn_frames += 1
            self._consecutive_ok_frames = 0
        else:
            self._consecutive_ok_frames += 1
            if self._consecutive_ok_frames >= self._HEAD_TURN_RELEASE:
                self._consecutive_turn_frames = 0
                self._head_turn_locked = False

        if self._consecutive_turn_frames >= self._HEAD_TURN_CONFIRM:
            self._head_turn_locked = True

        result.head_turned = self._head_turn_locked
        if result.head_turned:
            result.flags.append("HEAD_TURNED")

        if len(eyes) > 0:
            result.eye_boxes = [
                [round((fx+ex)/w, 4), round((fy+ey)/h, 4), round(ew/w, 4), round(eh/h, 4)]
                for ex, ey, ew, eh in eyes
            ]

        result.gaze_direction, result.gaze_deviation_score = self._estimate_gaze(fx, fy, fw, fh, eyes, w)
        if result.gaze_deviation_score > self.cfg.gaze_deviation_threshold:
            result.flags.append("SUSPICIOUS_GAZE")

        self._apply_identity_check(result, frame, fx, fy, fw, fh)

        result.processing_time_ms = (time.perf_counter() - t0) * 1000
        self._last_result = result
        return result

    def _estimate_landmarks(self, fx, fy, fw, fh, eyes) -> Optional[np.ndarray]:
        nose = (fx + fw // 2, fy + int(fh * 0.4))
        chin = (fx + fw // 2, fy + fh)
        left_mouth = (fx + int(fw * 0.3), fy + int(fh * 0.78))
        right_mouth = (fx + int(fw * 0.7), fy + int(fh * 0.78))
        if len(eyes) >= 2:
            sorted_eyes = sorted(eyes, key=lambda e: e[0])
            le, re = sorted_eyes[0], sorted_eyes[-1]
            left_eye = (fx + le[0] + le[2] // 2, fy + le[1] + le[3] // 2)
            right_eye = (fx + re[0] + re[2] // 2, fy + re[1] + re[3] // 2)
        else:
            left_eye = (fx + int(fw * 0.3), fy + int(fh * 0.35))
            right_eye = (fx + int(fw * 0.7), fy + int(fh * 0.35))
        return np.array([nose, chin, left_eye, right_eye, left_mouth, right_mouth], dtype=np.float64)

    def _estimate_gaze(self, fx, fy, fw, fh, eyes, frame_w):
        if len(eyes) < 1:
            return "UNKNOWN", 0.3
        eye_centers_x = [(e[0] + e[2] // 2) / fw for e in eyes]
        avg_x = np.mean(eye_centers_x)
        deviation = min(abs(avg_x - 0.5) * 2, 1.0)
        if deviation < 0.2:
            direction = "CENTER"
        elif avg_x < 0.35:
            direction = "LEFT"
        elif avg_x > 0.65:
            direction = "RIGHT"
        else:
            direction = "CENTER"
        return direction, round(deviation, 3)

    # ══════════════════════════════════════════════════
    #  FACE MATCHING (completely untouched)
    # ══════════════════════════════════════════════════

    def _match_face(self, frame, fx, fy, fw, fh) -> Tuple[bool, float]:
        h_img, w_img = frame.shape[:2]
        fx, fy = max(0, fx), max(0, fy)
        fw = min(fw, w_img - fx)
        fh = min(fh, h_img - fy)
        if fw < 10 or fh < 10:
            return False, 0.0  # face too small to verify — fail safe

        # SFace DNN matching — reliable identity verification
        if self._use_sface and self._registered_sface_embedding is not None:
            try:
                self._yunet_detector.setInputSize((w_img, h_img))
                _, yunet_faces = self._yunet_detector.detect(frame)
                if yunet_faces is not None and len(yunet_faces) > 0:
                    best_face = None
                    best_overlap = -1
                    for yf in yunet_faces:
                        ix0 = max(fx, yf[0])
                        iy0 = max(fy, yf[1])
                        ix1 = min(fx + fw, yf[0] + yf[2])
                        iy1 = min(fy + fh, yf[1] + yf[3])
                        inter = max(0, ix1 - ix0) * max(0, iy1 - iy0)
                        if inter > best_overlap:
                            best_overlap = inter
                            best_face = yf
                    if best_face is not None:
                        aligned = self._sface_recognizer.alignCrop(frame, best_face)
                        embedding = self._sface_recognizer.feature(aligned)
                        # match() L2-normalises BOTH arguments IN PLACE (verified:
                        # the stored vector's norm goes 4.798 -> 1.000 on the first
                        # call). Pass a copy so the enrollment we keep for the rest
                        # of the session is never silently rewritten by a match.
                        # Cosine scores are unaffected either way, but a mutated
                        # baseline is a trap for anything that persists or re-uses it.
                        cosine_score = self._sface_recognizer.match(
                            self._registered_sface_embedding.copy(), embedding,
                            cv2.FaceRecognizerSF_FR_COSINE
                        )
                        similarity = round(max(0.0, min(1.0, float(cosine_score))), 3)
                        match = similarity >= self.cfg.face_match_tolerance
                        logger.debug("SFace match: similarity=%.3f, threshold=%.3f, match=%s",
                                     similarity, self.cfg.face_match_tolerance, match)
                        return match, similarity
                # YuNet didn't detect a face — cannot verify identity, fail safe
                logger.debug("YuNet face detection failed during matching — treating as mismatch")
                return False, 0.0
            except Exception as e:
                logger.warning("SFace matching error: %s — treating as mismatch", e)
                return False, 0.0

        # Histogram/template fallback — only used when SFace models are unavailable
        face_roi = frame[fy:fy+fh, fx:fx+fw]
        hsv = cv2.cvtColor(face_roi, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 255, cv2.NORM_MINMAX)
        hist_score = 0.0
        if self._registered_histogram is not None:
            hist_score = cv2.compareHist(self._registered_histogram, hist, cv2.HISTCMP_CORREL)
            hist_score = max(0.0, min(1.0, hist_score))

        struct_score = 0.0
        if self._registered_face_gray is not None:
            current_gray = cv2.equalizeHist(
                cv2.cvtColor(cv2.resize(face_roi, (100, 120)), cv2.COLOR_BGR2GRAY)
            )
            res = cv2.matchTemplate(current_gray, self._registered_face_gray, cv2.TM_CCOEFF_NORMED)
            struct_score = max(0.0, min(1.0, float(res[0][0])))

        lbp_score = 0.0
        if self._registered_lbp is not None:
            current_gray = cv2.equalizeHist(
                cv2.cvtColor(cv2.resize(face_roi, (100, 120)), cv2.COLOR_BGR2GRAY)
            )
            current_lbp = self._compute_lbp_histogram(current_gray)
            lbp_score = max(0.0, min(1.0, float(np.minimum(self._registered_lbp, current_lbp).sum())))

        similarity = round(hist_score * 0.3 + struct_score * 0.4 + lbp_score * 0.3, 3)
        return similarity >= self.cfg.face_match_tolerance, similarity

    @staticmethod
    def _iou(box1, box2) -> float:
        x1, y1, w1, h1 = box1
        x2, y2, w2, h2 = box2
        xa, ya = max(x1, x2), max(y1, y2)
        xb, yb = min(x1 + w1, x2 + w2), min(y1 + h1, y2 + h2)
        inter = max(0, xb - xa) * max(0, yb - ya)
        union = w1 * h1 + w2 * h2 - inter
        return inter / union if union > 0 else 0

    def release(self):
        """Drop this session's identity state.

        Deliberately does NOT close the landmarker any more: it belongs to a
        pooled ModelSet shared with every other session, so closing it here would
        free the graph out from under other candidates mid-detect. Model handles
        are closed once, process-wide, by model_registry.shutdown().

        What must be cleared is the enrollment — the analyzer object can outlive
        the session in a traceback or a stale reference, and a face embedding is
        the one piece of state that is genuinely sensitive.
        """
        with self._lock:
            self._models = None
            self._registered_face_gray = None
            self._registered_histogram = None
            self._registered_lbp = None
            self._registered_geometry = None
            self._registered_sface_embedding = None
            self._last_identity = None
