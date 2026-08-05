

"""
Test: persons facing away from the camera (180°) should NOT be flagged.

Verifies the cross-check between YOLO person detection and face analyzer:
- YOLO detects multiple persons, face analyzer sees only 1 face → suppress flags
- YOLO detects multiple persons, face analyzer sees multiple faces → keep flags
"""
import asyncio
import pytest
from unittest.mock import MagicMock, patch
from dataclasses import field

import numpy as np

from proctoring.core.object_detector import DetectedObject, DetectionResult
from proctoring.core.face_analyzer import FaceAnalysisResult
from proctoring.pipeline import ProctorPipeline


def _make_face_result(face_count: int) -> FaceAnalysisResult:
    """Create a FaceAnalysisResult with a given face count."""
    r = FaceAnalysisResult()
    r.face_detected = face_count > 0
    r.face_count = face_count
    return r


def _make_object_result(person_count: int) -> DetectionResult:
    """Create a DetectionResult with extra-person detections (person_count > 1)."""
    r = DetectionResult()
    r.person_count = person_count
    # Add person objects for each extra person (first person is skipped by detector)
    for i in range(person_count - 1):
        r.objects.append(DetectedObject(
            label="person", confidence=0.85, severity="CRITICAL",
            bbox=(100 * i, 50, 100 * i + 80, 200),
        ))
        r.flags.append("PERSON_DETECTED")
    r.frame_severity = "CRITICAL" if person_count > 1 else "NONE"
    r.annotated_frame = np.zeros((480, 640, 3), dtype=np.uint8)
    return r


class TestPersonFacingAway:
    """Pipeline should suppress person flags when extra persons face away from camera."""

    def _run(self, coro):
        return asyncio.run(coro)

    def _build_pipeline(self, face_result: FaceAnalysisResult, object_result: DetectionResult):
        """Set up a ProctorPipeline with mocked face and object modules."""
        pipe = ProctorPipeline()
        pipe._initialized = True

        # Mock face analyzer
        pipe._face = MagicMock()
        pipe._face.analyze.return_value = face_result

        # Mock object detector
        pipe._objects = MagicMock()
        pipe._objects.detect.return_value = object_result

        # Disable other modules
        pipe._audio = None
        pipe._pose = None
        pipe._behavioral = None

        return pipe

    def test_persons_facing_away_suppressed(self):
        """
        YOLO sees 3 persons, face analyzer sees 1 face.
        The extra persons are facing away (180°) — flags must be suppressed.
        """
        face_r = _make_face_result(face_count=1)
        obj_r = _make_object_result(person_count=3)

        pipe = self._build_pipeline(face_r, obj_r)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = self._run(pipe.process_frame(frame, "test-session"))

        # Person flags must be removed
        assert "PERSON_DETECTED" not in result.flags, \
            "Persons facing away from camera should NOT be flagged"

        # Object result should have person objects stripped
        objects = result.object_result.get("objects", [])
        person_objects = [
            o for o in objects
            if (o.get("label") if isinstance(o, dict) else getattr(o, "label", None)) == "person"
        ]
        assert len(person_objects) == 0, \
            "Person objects should be removed when they are facing away"

        # Person count should be reset to 1 (just the candidate)
        assert result.object_result["person_count"] == 1

    def test_persons_facing_screen_flagged(self):
        """
        YOLO sees 2 persons AND face analyzer sees 2 faces.
        The extra person IS facing the screen — flags must remain.
        """
        face_r = _make_face_result(face_count=2)
        obj_r = _make_object_result(person_count=2)

        pipe = self._build_pipeline(face_r, obj_r)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = self._run(pipe.process_frame(frame, "test-session"))

        # Person flags must be kept
        assert "PERSON_DETECTED" in result.flags, \
            "Persons facing the screen SHOULD be flagged"

        # Person objects must remain in the result
        objects = result.object_result.get("objects", [])
        person_objects = [
            o for o in objects
            if (o.get("label") if isinstance(o, dict) else getattr(o, "label", None)) == "person"
        ]
        assert len(person_objects) >= 1, \
            "Person objects should be kept when they are facing the screen"

    def test_single_person_no_flag(self):
        """
        YOLO sees exactly 1 person (the candidate), face sees 1 face.
        No extra-person flag at all.
        """
        face_r = _make_face_result(face_count=1)
        obj_r = _make_object_result(person_count=1)  # no extra persons

        pipe = self._build_pipeline(face_r, obj_r)
        frame = np.zeros((480, 640, 3), dtype=np.uint8)
        result = self._run(pipe.process_frame(frame, "test-session"))

        assert "PERSON_DETECTED" not in result.flags
        assert result.object_result["person_count"] == 1
