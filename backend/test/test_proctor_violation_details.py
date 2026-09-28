"""What a persisted violation records (proctoring/routes.py::_violation_details).

HR's report shows each event as two things side by side: what the sensor measured
("Detected") and what the pipeline concluded ("AI interpretation"). The flag name,
severity and composite score are the interpretation; `details["observed"]` is the
measurement. These pin that each module's flag carries its own raw readings, so the
report has something factual to show beyond the flag name.
"""
from types import SimpleNamespace

import pytest

# Same guard as test_proctor_fullscreen.py: routes.py imports cv2 at module scope and the
# CI image carries no ML extras.
pytest.importorskip("cv2", reason="proctoring extras not installed")

from proctoring.routes import _violation_details   # noqa: E402  (must follow the guard above)


def _result(**kw):
    base = dict(cheating_score=42.0, object_result=None, pose_result=None,
                behavioral_result=None, audio_result=None)
    base.update(kw)
    return SimpleNamespace(**base)


FACE = {"face_detected": True, "face_count": 2, "yaw": 31.46, "pitch": -4.04,
        "gaze_direction": "LEFT", "identity_match": True, "has_reference": True,
        "similarity_score": 0.91}


def test_face_flag_records_face_measurements():
    d = _violation_details("MULTIPLE_FACES", 120, _result(), FACE)
    assert d["frame"] == 120
    assert d["cheating_score"] == 42.0
    assert d["observed"] == {
        "face_detected": True, "face_count": 2, "yaw_deg": 31.5, "pitch_deg": -4.0,
        "gaze_direction": "LEFT", "identity_match": True, "has_reference": True,
    }


def test_object_flag_records_labels_and_confidence_only():
    obj = {"objects": [{"label": "cell phone", "confidence": 0.873, "severity": "CRITICAL",
                        "bbox": (1, 2, 3, 4)}], "person_count": 1}
    d = _violation_details("PHONE_DETECTED", 5, _result(object_result=obj), {})
    assert d["observed"] == {"objects": [{"label": "cell phone", "confidence": 0.873}],
                             "person_count": 1}


def test_pose_flag_records_pose_measurements():
    pose = {"phone_gesture_confidence": 0.66666, "visibility_score": 0.2,
            "posture_deviation": 0.12345, "person_count": 1}
    d = _violation_details("PHONE_GESTURE", 5, _result(pose_result=pose), {})
    assert d["observed"] == {"phone_gesture_confidence": 0.667, "visibility_score": 0.2,
                             "posture_deviation": 0.123, "person_count": 1}


def test_missing_module_result_does_not_raise():
    # A module can be disabled or fail on a frame; the row must still be written.
    d = _violation_details("CANDIDATE_LEFT", 5, _result(), {})
    assert d["observed"]["visibility_score"] == 0.0


def test_audio_flag_keeps_its_existing_measurements():
    audio = {"spl_dbfs": -20.04, "peak_dbfs": -3.0, "noise_floor_dbfs": -60.0,
             "voice_count_estimate": 2, "speaker_distances": [], "speaker_enrolled": True}
    d = _violation_details("MULTIPLE_VOICES", 5, _result(audio_result=audio), {})
    assert "observed" not in d
    assert d["voice_count"] == 2 and d["spl_dbfs"] == -20.0
