"""Identity-mismatch false alarms (proctoring/core/face_analyzer.py, pipeline.py).

Face matching runs every `skip_frames` frames and skip frames reuse the previous
answer. The reuse used to copy the previous *frame's* result — and a frame that never
ran a match (frame 1, any no-face frame, anything before enrollment) holds the default
identity_match=False, similarity 0.0. So nearly every session opened with an
IDENTITY_MISMATCH at similarity 0.00, and the pipeline separately charged the +85
"impostor" weight on every no-face frame.

_match_face is stubbed: these pin the carry-forward and scoring rules, not the
matcher, so no models are needed.
"""
import pytest

# Runs in CI stage 2 (testing branch), which installs the proctoring extras.
pytestmark = pytest.mark.proctor

pytest.importorskip("cv2", reason="proctoring extras not installed")
np = pytest.importorskip("numpy")

from proctoring.config import ProctorConfig                             # noqa: E402
from proctoring.core.face_analyzer import FaceAnalysisResult, FaceAnalyzer  # noqa: E402
from proctoring.pipeline import ProctorPipeline                         # noqa: E402

FRAME = np.zeros((480, 640, 3), dtype=np.uint8)


class _Matcher:
    """Stands in for _match_face; records calls and returns a scripted answer."""

    def __init__(self, answer=(True, 0.82)):
        self.answer = answer
        self.calls = 0

    def __call__(self, frame, x, y, w, h):
        self.calls += 1
        return self.answer


@pytest.fixture
def analyzer(monkeypatch):
    cfg = ProctorConfig().face
    cfg.skip_frames = 2
    a = FaceAnalyzer(cfg)
    a._registered_sface_embedding = np.ones((1, 128), dtype=np.float32)  # "enrolled"
    a.matcher = _Matcher()
    monkeypatch.setattr(a, "_match_face", a.matcher)
    return a


def _check(a, frame_number):
    a._frame_count = frame_number
    r = FaceAnalysisResult(face_detected=True, face_count=1)
    a._apply_identity_check(r, FRAME, 10, 10, 100, 100)
    return r


def test_first_frame_is_compared_not_assumed_a_mismatch(analyzer):
    # Frame 1 is a skip frame (1 % 2 != 0) with no previous match. It used to copy the
    # default FaceAnalysisResult and flag IDENTITY_MISMATCH at 0.00.
    r = _check(analyzer, 1)
    assert analyzer.matcher.calls == 1
    assert r.identity_match is True and r.similarity_score == 0.82
    assert "IDENTITY_MISMATCH" not in r.flags


def test_skip_frame_reuses_the_last_real_match(analyzer):
    _check(analyzer, 2)                      # due: runs the match
    r = _check(analyzer, 3)                  # skip: reuses it
    assert analyzer.matcher.calls == 1
    assert r.identity_match is True and r.similarity_score == 0.82


def test_no_face_frame_in_between_does_not_poison_the_next_skip_frame(analyzer):
    _check(analyzer, 2)
    # A no-face frame returns early and becomes _last_result with identity_match=False.
    analyzer._last_result = FaceAnalysisResult(face_detected=False)
    r = _check(analyzer, 3)
    assert "IDENTITY_MISMATCH" not in r.flags
    assert r.similarity_score == 0.82


def test_a_real_mismatch_is_still_flagged_and_carried(analyzer):
    analyzer.matcher.answer = (False, 0.12)
    r1 = _check(analyzer, 1)
    r2 = _check(analyzer, 2)
    r3 = _check(analyzer, 3)
    for r in (r1, r2, r3):
        assert "IDENTITY_MISMATCH" in r.flags and r.similarity_score == 0.12


def test_without_enrollment_identity_is_not_judged(analyzer):
    analyzer._registered_sface_embedding = None
    r = _check(analyzer, 1)
    assert analyzer.matcher.calls == 0
    assert "IDENTITY_MISMATCH" not in r.flags


def test_release_forgets_the_cached_match(analyzer):
    _check(analyzer, 2)
    analyzer.release()
    assert analyzer._last_identity is None


# ── through analyze(), with the real models ──────────────────────────────────

def test_real_models_first_frame_and_return_after_look_away(monkeypatch):
    import os
    sface = os.path.join(os.path.dirname(__file__), "..", "proctoring", "models",
                         "face_recognition_sface_2021dec.onnx")
    if not os.path.exists(sface):
        pytest.skip("proctoring model files not present")
    from test.test_proctor_model_sharing import _synthetic_face
    from proctoring import model_registry

    monkeypatch.setenv("PROCTOR_INFERENCE_SLOTS", "1")
    model_registry.warm(1)
    try:
        a = FaceAnalyzer(ProctorConfig().face)
        face = _synthetic_face(1)
        assert a.register_face(face)
        real_match = a._match_face
        calls = []
        monkeypatch.setattr(a, "_match_face", lambda *args: calls.append(1) or real_match(*args))

        first = a.analyze(face)
        assert first.identity_match and "IDENTITY_MISMATCH" not in first.flags

        blank = np.full_like(face, 120)
        for _ in range(3):
            a.analyze(blank)
        calls.clear()
        back = a.analyze(face)
        assert calls, "the face that returns must be compared, not waved through"
        assert back.identity_match and "IDENTITY_MISMATCH" not in back.flags
    finally:
        model_registry.shutdown()


# ── pipeline scoring ─────────────────────────────────────────────────────────

def _face_score(r):
    from types import SimpleNamespace
    return ProctorPipeline._calc_face_score(SimpleNamespace(gaze_flags_off=False), r)


def test_no_face_frame_is_not_also_scored_as_an_impostor():
    r = FaceAnalysisResult(face_detected=False, has_reference=True, flags=["NO_FACE"])
    assert _face_score(r) == 40


def test_confirmed_mismatch_still_carries_its_weight():
    r = FaceAnalysisResult(face_detected=True, face_count=1, has_reference=True,
                           identity_match=False, flags=["IDENTITY_MISMATCH"])
    assert _face_score(r) == 85
