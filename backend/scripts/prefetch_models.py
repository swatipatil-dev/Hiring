#!/usr/bin/env python
"""
Download and cache every model the app loads at runtime. Run once per deploy,
BEFORE starting the services.

Why this exists
---------------
`src/services/enhanced_ats_service.py` builds a SentenceTransformer when each API
worker starts. On a fresh server that triggers a HuggingFace download at startup,
as whichever user owns the service process, into whatever $HOME resolves to.
That is slow, repeats in every worker, and silently leaves the cache somewhere
different from where the service will look for it next time.

Running this as part of deployment makes the download explicit, puts it in a
known place (HF_HOME), and surfaces a failure at deploy time rather than when the
first candidate uploads a resume.

Usage
-----
    export HF_HOME=/opt/hireflow/hf-cache
    /opt/hireflow/venv/bin/python scripts/prefetch_models.py

    # ATS model only (CI stage 1, which installs no proctoring extras):
    python scripts/prefetch_models.py --ats-only

Exits non-zero if a REQUIRED model could not be fetched, so a deploy script can
fail fast. Optional components (proctoring extras) only warn — the app degrades
cleanly without them.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

failures = []
warnings = []


def _report(label, ok, detail="", required=True):
    mark = "ok  " if ok else ("FAIL" if required else "warn")
    print(f"  [{mark}] {label}{(' — ' + detail) if detail else ''}")
    if not ok:
        (failures if required else warnings).append(f"{label}: {detail}")


def prefetch_sentence_transformer():
    """all-MiniLM-L6-v2 — semantic ATS scoring. Falls back to TF-IDF if absent."""
    print("\nSentence-transformers (semantic ATS scoring)")
    print(f"  HF_HOME = {os.getenv('HF_HOME') or '(unset — will use ~/.cache/huggingface)'}")
    try:
        t0 = time.perf_counter()
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer("all-MiniLM-L6-v2")
        vec = model.encode("warm the tokenizer and the ONNX/torch graph")
        _report("all-MiniLM-L6-v2", True,
                f"{len(vec)}-dim, {time.perf_counter() - t0:.1f}s")
    except ImportError:
        _report("all-MiniLM-L6-v2", False,
                "sentence-transformers not installed — ATS will use the TF-IDF "
                "fallback (lower quality, but functional)", required=False)
    except Exception as e:
        _report("all-MiniLM-L6-v2", False, f"{type(e).__name__}: {e}")


def check_proctoring_models():
    """Proctoring weights ship in the repo — verify rather than download."""
    print("\nProctoring models (shipped in proctoring/models/)")
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_dir = os.path.join(here, "proctoring", "models")
    expected = {
        "face_landmarker.task": 3_000_000,
        "pose_landmarker.task": 5_000_000,
        "face_detection_yunet_2023mar.onnx": 200_000,
        "face_recognition_sface_2021dec.onnx": 38_000_000,
    }
    for name, min_size in expected.items():
        path = os.path.join(model_dir, name)
        if not os.path.exists(path):
            _report(name, False, "missing — proctoring will not start")
            continue
        size = os.path.getsize(path)
        _report(name, size >= min_size,
                f"{size / 1_000_000:.1f} MB" if size >= min_size
                else f"only {size} bytes — truncated checkout?")

    yolo = os.path.join(here, "yolov8n.pt")
    _report("yolov8n.pt", os.path.exists(yolo),
            "missing — object detection disabled" if not os.path.exists(yolo)
            else f"{os.path.getsize(yolo) / 1_000_000:.1f} MB",
            required=False)


def warm_model_pool():
    """Build one ModelSet so a bad install fails here, not at session start.

    Each engine is reported on its own line. Summarising them as a dict inside an
    "ok" result hides a disabled one: proctoring still starts without YOLO or
    pose, it just silently stops detecting whole categories of cheating.
    """
    print("\nProctoring model pool (builds one set to prove it loads)")
    try:
        t0 = time.perf_counter()
        from proctoring import model_registry
        built = model_registry.warm(1)
        caps = model_registry.capabilities()
        model_registry.shutdown()
    except ImportError as e:
        _report("ModelSet build", False,
                f"proctoring extras not installed ({e}) — proctoring disabled",
                required=False)
        return
    except Exception as e:
        _report("ModelSet build", False, f"{type(e).__name__}: {e}")
        return

    _report("ModelSet build", bool(built), f"{time.perf_counter() - t0:.1f}s")

    # At least one face engine is mandatory — without it the pipeline reports
    # clean frames forever and a test only looks proctored.
    face_ok = caps.get("landmarker") or caps.get("sface")
    _report("face engine (landmarker or sface)", face_ok,
            "" if face_ok else "NEITHER loaded — proctoring cannot verify identity")

    for engine, what_is_lost in (
        ("landmarker", "head pose and gaze fall back to the reduced Haar path"),
        ("sface",      "identity matching falls back to histogram comparison"),
        ("pose",       "no posture or phone-gesture detection"),
        ("yolo",       "NO OBJECT DETECTION — phones, books and second people "
                       "go unflagged"),
    ):
        on = bool(caps.get(engine))
        _report(f"  engine: {engine}", on, "" if on else what_is_lost,
                required=False)


def check_spacy():
    """en_core_web_trf is referenced but never pinned — NER no-ops without it."""
    print("\nspaCy NER model (optional)")
    try:
        import spacy  # noqa: F401
    except ImportError:
        _report("spaCy", False, "not installed — NER skill extraction disabled",
                required=False)
        return
    try:
        import spacy
        spacy.load("en_core_web_trf")
        _report("en_core_web_trf", True)
    except Exception:
        _report("en_core_web_trf", False,
                "not installed — NER skill extraction silently disabled. "
                "Install with: python -m spacy download en_core_web_trf (~450 MB)",
                required=False)


def main():
    # Without the proctoring extras, model_registry still imports and builds a set
    # with no engines, which fails warm_model_pool()'s required face-engine check.
    ats_only = "--ats-only" in sys.argv[1:]

    print("=" * 68)
    print("HireFlow model prefetch" + (" (ATS model only)" if ats_only else ""))
    print("=" * 68)

    prefetch_sentence_transformer()
    if not ats_only:
        check_proctoring_models()
        warm_model_pool()
        check_spacy()

    print("\n" + "=" * 68)
    if warnings:
        print(f"{len(warnings)} optional component(s) unavailable:")
        for w in warnings:
            print(f"  - {w}")
    if failures:
        print(f"\n{len(failures)} REQUIRED component(s) failed:")
        for f in failures:
            print(f"  - {f}")
        print("\nDeployment should not proceed until these are resolved.")
        return 1
    print("All required models present and loadable.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
