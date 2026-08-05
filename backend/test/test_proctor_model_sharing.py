"""
Regression tests for the shared proctoring model pool.

Models are shared across sessions (proctoring/model_registry.py) so that memory
scales with the CPU rather than with the number of candidates. The risk that
buys is identity leaking between candidates — which is precisely what the old
"one full pipeline per session" design existed to prevent. These tests pin that
property down, plus the pool's capacity behaviour.

Skipped wholesale when the proctoring extras (cv2 / mediapipe / ultralytics) or
the model files are not installed, so a slim CI image does not fail on them.
"""
import os
import threading

import pytest

pytestmark = pytest.mark.proctor

cv2 = pytest.importorskip("cv2", reason="proctoring extras not installed")
np = pytest.importorskip("numpy")

_MODEL_DIR = os.path.join(os.path.dirname(__file__), "..", "proctoring", "models")
_SFACE = os.path.join(_MODEL_DIR, "face_recognition_sface_2021dec.onnx")

if not os.path.exists(_SFACE):
    pytest.skip("proctoring model files not present", allow_module_level=True)


def _synthetic_face(seed: int) -> "np.ndarray":
    """A deterministic, detectable, per-seed distinguishable face-like image."""
    rng = np.random.RandomState(seed)
    img = np.full((480, 640, 3), 120, dtype=np.uint8)
    cv2.ellipse(img, (320, 240), (110, 145), 0, 0, 360, (200, 180, 170), -1)
    for dx in (-40, 40):
        cv2.circle(img, (320 + dx, 200), 14, (40, 40, 40), -1)
    cv2.ellipse(img, (320, 295), (45, 22), 0, 0, 180, (60, 40, 40), 3)
    noise = rng.randint(-12, 12, img.shape, dtype=np.int16)
    return np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)


@pytest.fixture(scope="module")
def registry():
    os.environ.setdefault("PROCTOR_INFERENCE_SLOTS", "2")
    from proctoring import model_registry
    model_registry.warm(1)
    yield model_registry
    model_registry.shutdown()


@pytest.fixture
def face_cfg():
    from proctoring.config import ProctorConfig
    return ProctorConfig().face


def _analyzer(face_cfg):
    from proctoring.core.face_analyzer import FaceAnalyzer
    return FaceAnalyzer(face_cfg)


# ── the property that matters ────────────────────────────────────────────────

def test_enrollments_stay_isolated_across_shared_models(registry, face_cfg):
    """Two candidates analysed through the SAME pooled models keep separate identities."""
    a, b = _analyzer(face_cfg), _analyzer(face_cfg)
    face_a, face_b = _synthetic_face(1), _synthetic_face(99)

    assert a.register_face(face_a)
    assert b.register_face(face_b)

    emb_a, emb_b = a._registered_sface_embedding, b._registered_sface_embedding
    if emb_a is None or emb_b is None:
        pytest.skip("SFace enrollment unavailable in this environment")

    assert emb_a is not emb_b
    assert not np.array_equal(emb_a, emb_b)
    # Distinct buffers, so neither can be rewritten through the other.
    assert (emb_a.__array_interface__["data"][0]
            != emb_b.__array_interface__["data"][0])

    snap_a, snap_b = emb_a.copy(), emb_b.copy()
    for _ in range(3):
        a.analyze(face_a)
        b.analyze(face_b)

    # cv2's FaceRecognizerSF.match() L2-normalises its arguments IN PLACE, so
    # _match_face passes a copy. If that copy is ever dropped, these two fail.
    assert np.array_equal(a._registered_sface_embedding, snap_a)
    assert np.array_equal(b._registered_sface_embedding, snap_b)
    assert not np.array_equal(a._registered_sface_embedding,
                              b._registered_sface_embedding)


def test_release_clears_identity_but_keeps_models_usable(registry, face_cfg):
    """A session releasing must not close models other sessions are still using."""
    a, b = _analyzer(face_cfg), _analyzer(face_cfg)
    a.register_face(_synthetic_face(2))
    b.register_face(_synthetic_face(3))

    a.release()
    assert a._registered_sface_embedding is None

    # b must still work: release() must not have closed the shared handles.
    assert b.analyze(_synthetic_face(3)) is not None


def test_analyzer_holds_no_models_outside_a_borrow(face_cfg):
    """Session objects must stay cheap — that is the whole point of pooling."""
    a = _analyzer(face_cfg)
    assert a._landmarker is None
    assert a._sface_recognizer is None
    assert a._yunet_detector is None
    assert a._models is None


def test_concurrent_sessions_do_not_error(registry, face_cfg):
    errors, results = [], []

    def run(idx):
        try:
            an = _analyzer(face_cfg)
            img = _synthetic_face(idx)
            an.register_face(img)
            results.append(an.analyze(img))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(180)

    assert not errors, errors
    assert len(results) == 6


# ── pool mechanics ───────────────────────────────────────────────────────────

def test_pool_reuses_a_set_for_sequential_borrows(registry):
    seen = set()
    for _ in range(4):
        with registry.borrow() as ms:
            seen.add(id(ms))
    assert len(seen) == 1, "sequential borrows should not build extra sets"


def test_pool_returns_slot_after_use(registry):
    before = registry.stats()["in_use"]
    with registry.borrow():
        assert registry.stats()["in_use"] == before + 1
    assert registry.stats()["in_use"] == before


def test_pool_never_exceeds_capacity_and_times_out(registry):
    capacity = registry.stats()["capacity"]
    release = threading.Event()
    holding = []

    def hold():
        with registry.borrow(timeout=30):
            holding.append(1)
            release.wait(30)

    threads = [threading.Thread(target=hold, daemon=True) for _ in range(capacity)]
    for t in threads:
        t.start()
    deadline = threading.Event()
    while len(holding) < capacity and not deadline.wait(0.05):
        if registry.stats()["built"] > capacity:
            break

    try:
        assert registry.stats()["built"] <= capacity
        with pytest.raises(TimeoutError):
            with registry.borrow(timeout=1):
                pass
    finally:
        release.set()
        for t in threads:
            t.join(30)
