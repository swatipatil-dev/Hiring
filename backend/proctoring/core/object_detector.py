"""
Module 2 — Object Detection: YOLOv8-based prohibited object detection.
Auto-selects GPU/CPU model and captures evidence, rate-limited per session.

The YOLO model itself is shared — see proctoring/model_registry.py. This class
holds only the evidence cooldown, which is the sole piece of genuine per-session
state it has ever had.
"""
import os
import time
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from pathlib import Path

import numpy as np
import cv2

from .. import model_registry

logger = logging.getLogger(__name__)


@dataclass
class DetectedObject:
    label: str
    confidence: float
    severity: str  # HIGH, MEDIUM, CRITICAL
    bbox: Tuple[int, int, int, int]  # x1, y1, x2, y2


@dataclass
class DetectionResult:
    objects: List[DetectedObject] = field(default_factory=list)
    frame_severity: str = "NONE"  # NONE, MEDIUM, HIGH, CRITICAL
    person_count: int = 0
    annotated_frame: Optional[np.ndarray] = None
    processing_time_ms: float = 0.0
    flags: List[str] = field(default_factory=list)
    # Storage key of the annotated evidence frame saved for this detection, so the
    # violation row can point HR at the picture with the box drawn on it rather than
    # a bare frame (or, as before, nothing at all).
    evidence_path: Optional[str] = None


# Severity colors (BGR)
SEVERITY_COLORS = {
    "CRITICAL": (0, 0, 255),
    "HIGH": (0, 0, 255),
    "MEDIUM": (0, 165, 255),
    "LOW": (0, 255, 255),
}


class CheatObjectDetector:
    """
    Detects prohibited objects using YOLOv8.
    Auto-selects yolov8n (CPU) or yolov8m (GPU).
    """

    def __init__(self, config=None):
        from ..config import ObjectConfig
        self.cfg = config or ObjectConfig()

        # YOLO is borrowed per call from the shared pool (see model_registry) rather
        # than loaded here. This detector was already effectively stateless — the
        # only genuine per-session state is the evidence cooldown below, and
        # session_id is already a detect() parameter.
        self._models = None

        self._last_evidence_at = 0.0   # enforces cfg.evidence_cooldown_sec
        self._last_evidence_key = None  # reused while inside the cooldown window

    @property
    def _model(self):
        return self._models.yolo if self._models else None

    @property
    def _device(self) -> str:
        return self._models.device if self._models else "cpu"

    @property
    def _coco_names(self) -> Dict[int, str]:
        return self._models.coco_names if self._models else {}

    @property
    def _target_class_ids(self) -> set:
        return self._models.target_class_ids if self._models else set()

    def detect(self, frame: np.ndarray, session_id: str = "default") -> DetectionResult:
        """Run detection on a single BGR frame."""
        with model_registry.borrow() as models:
            self._models = models
            try:
                return self._detect_unlocked(frame, session_id)
            finally:
                self._models = None

    def _detect_unlocked(self, frame: np.ndarray, session_id: str = "default") -> DetectionResult:
        t0 = time.perf_counter()
        result = DetectionResult()

        if self._model is None:
            result.processing_time_ms = (time.perf_counter() - t0) * 1000
            return result

        # Run inference
        half = self._device == "cuda"
        results = self._model(
            frame, verbose=False, half=half,
            imgsz=self.cfg.inference_size,
            conf=0.15,  # very low pre-filter — let per-target thresholds decide
        )

        annotated = frame.copy()
        person_count = 0
        max_severity = "NONE"
        severity_rank = {"NONE": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}

        for r in results:
            for box in r.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                label = self._coco_names.get(cls_id, "unknown")

                # Find matching target config
                target_info = None
                for tgt_label, tgt_info in self.cfg.targets.items():
                    if tgt_info["coco_id"] == cls_id:
                        target_info = tgt_info
                        label = tgt_label
                        break

                if target_info is None:
                    continue

                if conf < target_info["confidence"]:
                    continue

                if label == "person":
                    person_count += 1
                    if person_count <= 1:
                        continue  # first person is the candidate
                    # Extra persons are added to results but NOT annotated on the frame.
                    # The pipeline will cross-check with face detection: if only 1 face
                    # is visible, the extra person is facing away (180°) and is not a threat.

                severity = target_info["severity"]
                x1, y1, x2, y2 = map(int, box.xyxy[0].tolist())

                # Min bbox area filter
                bbox_area = (x2 - x1) * (y2 - y1)
                if bbox_area < self.cfg.min_bbox_area:
                    continue

                obj = DetectedObject(label=label, confidence=round(conf, 3), severity=severity, bbox=(x1, y1, x2, y2))
                result.objects.append(obj)

                print(f"[OBJECT] DETECTED: {label} conf={conf:.2f} severity={severity} bbox=({x1},{y1},{x2},{y2})")

                # Skip frame annotation and severity escalation for extra persons —
                # pipeline will confirm via face count before flagging
                if label == "person":
                    result.flags.append("PERSON_DETECTED")
                    continue

                if severity_rank.get(severity, 0) > severity_rank.get(max_severity, 0):
                    max_severity = severity

                # Annotate frame (non-person objects only; persons are deferred to pipeline)
                color = SEVERITY_COLORS.get(severity, (255, 255, 255))
                cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
                text = f"{label} {conf:.0%} [{severity}]"
                cv2.putText(annotated, text, (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

                result.flags.append(f"PHONE_DETECTED" if "phone" in label else f"{label.upper()}_DETECTED")

        result.person_count = person_count
        result.frame_severity = max_severity
        result.annotated_frame = annotated

        # Auto-save evidence for HIGH/CRITICAL. Honours evidence_cooldown_sec — the
        # cooldown was configured but never applied, so a phone left on the desk wrote
        # an image on every detection frame. Within the window the last key is reused,
        # which still gives the violation row a picture of the same episode.
        if max_severity in ("HIGH", "CRITICAL"):
            now = time.time()
            if now - self._last_evidence_at >= self.cfg.evidence_cooldown_sec:
                self._last_evidence_at = now
                self._last_evidence_key = self._save_evidence(annotated, session_id, result.objects)
            result.evidence_path = self._last_evidence_key

        result.processing_time_ms = (time.perf_counter() - t0) * 1000
        return result

    # NOTE: _iou_match() and its _iou_tracker / _track_hits dicts were removed here.
    # The tracker was only ever read, never written, and _iou_match had no callers —
    # so the "objects tracked across frames" behaviour it implied did not exist.
    # It also happened to be the stated reason this detector could not be shared.

    def _save_evidence(self, frame: np.ndarray, session_id: str, objects: List[DetectedObject]) -> Optional[str]:
        """Save annotated frame as evidence. Returns its storage key, or None on failure."""
        ts = int(time.time() * 1000)
        labels = "_".join(o.label.replace(" ", "") for o in objects[:3])
        key = f"{self.cfg.evidence_dir}/{session_id}/evidence_{session_id}_{ts}_{labels}.jpg"
        try:
            from src.services.storage import storage
            ok, buf = cv2.imencode(".jpg", frame)
            if ok:
                storage.save_bytes(buf.tobytes(), key)
                logger.info(f"Evidence saved: {key}")
                return key
        except Exception as e:
            logger.error(f"Failed to save evidence: {e}")
        return None

    def model_info(self) -> dict:
        return {
            "model": self.cfg.model_gpu if self._device == "cuda" else self.cfg.model_cpu,
            "device": self._device,
            "targets": list(self.cfg.targets.keys()),
            "loaded": self._model is not None,
        }
