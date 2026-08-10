"""
Shared inference models, pooled.

The problem this solves
-----------------------
Every proctoring session used to construct its own copy of every model:
SFace (38.7 MB), PoseLandmarker (5.8 MB), YOLOv8n (6.5 MB), FaceLandmarker
(3.8 MB), YuNet and three Haar cascades. Capacity was therefore
O(sessions x model_size) — 50 candidates meant ~3 GB of identical weights.

But a session's *state* is tiny: some counters, a few short deques and one face
embedding, all measured in kilobytes. Only the weights are expensive, and
weights are exactly the part that does not differ between candidates.

So capacity becomes O(slots x model_size) + O(sessions x KB), where `slots` is
sized from the CPU rather than from how many people are taking a test.

Why a pool and not one shared instance
--------------------------------------
MediaPipe Tasks graphs, `cv2.FaceDetectorYN` (whose `setInputSize()` mutates the
detector before every `detect()`) and ultralytics' internal predictor are all
call-stateful and not safe for concurrent use. A single shared instance behind
one mutex would therefore serialise every session's inference globally — which is
the objection recorded in warmup.py, and it is correct as far as it goes.

A bounded pool avoids both extremes: K independent model sets, each borrowed
exclusively for the duration of a call, so K frames run genuinely in parallel
while sessions above K queue briefly. Since frames are sampled at a few fps,
that queuing is invisible. Exclusive ownership while borrowed is also why a
ModelSet needs no internal lock — the pool is the mutex.

Deadlock note: callers that also hold a per-session lock must take THAT lock
first and borrow second. Consistent ordering is what keeps a thread from holding
a slot while waiting for a session lock held by a thread waiting for a slot.
"""

import contextlib
import logging
import os
import threading
import time
from typing import List, Optional

from .runtime import configure_native_threads, inference_slots

logger = logging.getLogger(__name__)

# The backend/ directory. YOLO weights are configured as a bare filename
# ("yolov8n.pt") and live here, so they must be resolved against this rather than
# the process working directory — see _load_yolo.
_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
_FACE_LANDMARKER_MODEL = os.path.join(_MODEL_DIR, "face_landmarker.task")
_POSE_LANDMARKER_MODEL = os.path.join(_MODEL_DIR, "pose_landmarker.task")
_YUNET_MODEL = os.path.join(_MODEL_DIR, "face_detection_yunet_2023mar.onnx")
_SFACE_MODEL = os.path.join(_MODEL_DIR, "face_recognition_sface_2021dec.onnx")

# How long a frame will wait for a free slot before giving up. Long enough to
# absorb a burst, short enough that a wedged slot surfaces as an error instead of
# hanging the WebSocket forever.
BORROW_TIMEOUT_S = float(os.getenv("PROCTOR_BORROW_TIMEOUT_S", "30"))


class ModelSet:
    """One complete set of loaded weights. Holds no per-session state.

    Attribute names deliberately mirror what the analyzers used to own, so the
    analyzers can expose them as properties without touching their call sites.
    """

    __slots__ = (
        "face_landmarker", "pose_landmarker", "mp",
        "sface", "yunet",
        "face_cascade", "eye_cascade", "profile_cascade",
        "yolo", "coco_names", "target_class_ids", "device",
        "use_landmarker", "use_sface", "use_pose", "use_yolo",
    )

    def __init__(self):
        self.face_landmarker = None
        self.pose_landmarker = None
        self.mp = None
        self.sface = None
        self.yunet = None
        self.face_cascade = None
        self.eye_cascade = None
        self.profile_cascade = None
        self.yolo = None
        self.coco_names = {}
        self.target_class_ids = set()
        self.device = "cpu"
        self.use_landmarker = False
        self.use_sface = False
        self.use_pose = False
        self.use_yolo = False

    # ── construction ──────────────────────────────────────────────────────

    @classmethod
    def build(cls, face_cfg=None, pose_cfg=None, object_cfg=None) -> "ModelSet":
        """Load every model. Expensive (~0.6s warm); call off the event loop.

        Individual models degrade rather than raise — a set without YOLO still
        does face and pose work. The caller decides what counts as fatal.
        """
        configure_native_threads()
        ms = cls()
        t0 = time.perf_counter()

        ms._load_mediapipe(face_cfg, pose_cfg)
        ms._load_opencv_face()
        ms._load_cascades()
        ms._load_yolo(object_cfg)

        logger.info(
            "ModelSet built in %.2fs (landmarker=%s sface=%s pose=%s yolo=%s)",
            time.perf_counter() - t0,
            ms.use_landmarker, ms.use_sface, ms.use_pose, ms.use_yolo,
        )
        return ms

    def _load_mediapipe(self, face_cfg, pose_cfg) -> None:
        try:
            import mediapipe as mp
            self.mp = mp
        except Exception as e:
            logger.warning("mediapipe unavailable: %s — landmarker and pose disabled", e)
            return

        if os.path.exists(_FACE_LANDMARKER_MODEL):
            try:
                options = self.mp.tasks.vision.FaceLandmarkerOptions(
                    base_options=self.mp.tasks.BaseOptions(model_asset_path=_FACE_LANDMARKER_MODEL),
                    running_mode=self.mp.tasks.vision.RunningMode.IMAGE,
                    num_faces=5,
                    min_face_detection_confidence=0.5,
                    min_face_presence_confidence=0.5,
                    min_tracking_confidence=0.5,
                    output_face_blendshapes=False,
                    output_facial_transformation_matrixes=False,
                )
                self.face_landmarker = self.mp.tasks.vision.FaceLandmarker.create_from_options(options)
                self.use_landmarker = True
            except Exception as e:
                logger.warning("FaceLandmarker failed: %s — Haar fallback", e)
        else:
            logger.warning("FaceLandmarker model missing at %s — Haar fallback", _FACE_LANDMARKER_MODEL)

        if os.path.exists(_POSE_LANDMARKER_MODEL):
            try:
                options = self.mp.tasks.vision.PoseLandmarkerOptions(
                    base_options=self.mp.tasks.BaseOptions(model_asset_path=_POSE_LANDMARKER_MODEL),
                    running_mode=self.mp.tasks.vision.RunningMode.IMAGE,
                    num_poses=1,
                    min_pose_detection_confidence=0.5,
                    min_pose_presence_confidence=0.5,
                    min_tracking_confidence=0.5,
                )
                self.pose_landmarker = self.mp.tasks.vision.PoseLandmarker.create_from_options(options)
                self.use_pose = True
            except Exception as e:
                logger.warning("PoseLandmarker failed: %s — pose detection disabled", e)
        else:
            logger.warning("Pose model missing at %s — pose detection disabled", _POSE_LANDMARKER_MODEL)

    def _load_opencv_face(self) -> None:
        try:
            import cv2
            if os.path.exists(_SFACE_MODEL) and os.path.exists(_YUNET_MODEL):
                self.sface = cv2.FaceRecognizerSF.create(_SFACE_MODEL, "")
                self.yunet = cv2.FaceDetectorYN.create(
                    _YUNET_MODEL, "", (320, 320),
                    score_threshold=0.5, nms_threshold=0.3, top_k=5,
                )
                self.use_sface = True
        except Exception as e:
            logger.warning("SFace init failed: %s — falling back to histogram matching", e)

    def _load_cascades(self) -> None:
        try:
            import cv2
            base = cv2.data.haarcascades
            self.face_cascade = cv2.CascadeClassifier(base + "haarcascade_frontalface_default.xml")
            self.eye_cascade = cv2.CascadeClassifier(base + "haarcascade_eye.xml")
            self.profile_cascade = cv2.CascadeClassifier(base + "haarcascade_profileface.xml")
        except Exception as e:
            logger.warning("Haar cascade load failed: %s", e)

    def _load_yolo(self, object_cfg) -> None:
        try:
            import torch
            from ultralytics import YOLO
            from .config import ObjectConfig

            cfg = object_cfg or ObjectConfig()
            has_cuda = torch.cuda.is_available()
            self.device = "cuda" if has_cuda else "cpu"

            # The config names the weights as a bare filename. Ultralytics
            # resolves such a name against the CWD and, failing to find it,
            # tries to DOWNLOAD it there — which raises EACCES under systemd,
            # cron, or any shell started outside backend/, silently disabling
            # object detection. Resolve against backend/ where the file lives.
            weights = cfg.model_gpu if has_cuda else cfg.model_cpu
            if not os.path.isabs(weights):
                local = os.path.join(_BACKEND_DIR, weights)
                if os.path.exists(local):
                    weights = local
                else:
                    logger.warning(
                        "YOLO weights %r not found in %s — ultralytics will try to "
                        "download them, which needs a writable working directory",
                        weights, _BACKEND_DIR,
                    )

            self.yolo = YOLO(weights)
            if has_cuda:
                self.yolo.to("cuda")

            self.coco_names = self.yolo.names
            for _label, info in cfg.targets.items():
                cid = info["coco_id"]
                if cid in self.coco_names:
                    self.target_class_ids.add(cid)
            self.use_yolo = True
        except Exception as e:
            logger.warning("YOLO init failed: %s — object detection disabled", e)

    def close(self) -> None:
        for handle in (self.face_landmarker, self.pose_landmarker):
            try:
                if handle is not None:
                    handle.close()
            except Exception:
                pass
        self.face_landmarker = None
        self.pose_landmarker = None


class _ModelPool:
    """Bounded pool of ModelSets, built lazily up to `inference_slots()`."""

    def __init__(self):
        self._cv = threading.Condition(threading.Lock())
        self._free: List[ModelSet] = []
        self._built = 0
        self._waits = 0
        self._peak_in_use = 0
        # What the most recently built set could do. Lets callers report engine
        # capability (e.g. session summaries) without borrowing a slot just to ask.
        self._caps = {"landmarker": False, "sface": False, "pose": False, "yolo": False}

    def _record_caps(self, ms: ModelSet) -> None:
        self._caps = {
            "landmarker": ms.use_landmarker,
            "sface": ms.use_sface,
            "pose": ms.use_pose,
            "yolo": ms.use_yolo,
        }

    def capabilities(self) -> dict:
        """Which engines are live, without borrowing. All False before the first build."""
        return dict(self._caps)

    @property
    def capacity(self) -> int:
        return inference_slots()

    def _reserve(self, timeout: Optional[float]) -> ModelSet:
        """Return a free set, or reserve the right to build a new one."""
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cv:
            while True:
                if self._free:
                    ms = self._free.pop()
                    in_use = self._built - len(self._free)
                    self._peak_in_use = max(self._peak_in_use, in_use)
                    return ms
                if self._built < self.capacity:
                    # Reserve the slot, then build OUTSIDE the lock so a slow
                    # construction doesn't block every other borrower.
                    self._built += 1
                    break
                self._waits += 1
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(
                        f"No proctoring inference slot after {timeout}s "
                        f"(all {self.capacity} in use)"
                    )
                self._cv.wait(remaining)

        try:
            ms = ModelSet.build()
        except Exception:
            with self._cv:
                self._built -= 1
                self._cv.notify()
            raise
        self._record_caps(ms)
        return ms

    def _release(self, ms: ModelSet) -> None:
        with self._cv:
            self._free.append(ms)
            self._cv.notify()

    @contextlib.contextmanager
    def borrow(self, timeout: Optional[float] = BORROW_TIMEOUT_S):
        """Exclusively hold one ModelSet for the duration of the block.

        BLOCKING — call from a worker thread, never from the event loop.
        """
        ms = self._reserve(timeout)
        try:
            yield ms
        finally:
            self._release(ms)

    def warm(self, count: Optional[int] = None) -> int:
        """Pre-build up to `count` sets (default: full capacity). Returns the total built."""
        target = self.capacity if count is None else min(count, self.capacity)
        while True:
            with self._cv:
                if self._built >= target:
                    return self._built
                self._built += 1
            try:
                ms = ModelSet.build()
            except Exception as e:
                with self._cv:
                    self._built -= 1
                logger.error("Could not pre-build a model set: %s", e)
                return self._built
            self._record_caps(ms)
            self._release(ms)

    def stats(self) -> dict:
        with self._cv:
            return {
                "capacity": self.capacity,
                "built": self._built,
                "free": len(self._free),
                "in_use": self._built - len(self._free),
                "peak_in_use": self._peak_in_use,
                "waits": self._waits,
            }

    def shutdown(self) -> None:
        with self._cv:
            sets, self._free = list(self._free), []
            self._built -= len(sets)
        for ms in sets:
            ms.close()


_pool = _ModelPool()

borrow = _pool.borrow
warm = _pool.warm
stats = _pool.stats
shutdown = _pool.shutdown
capabilities = _pool.capabilities
