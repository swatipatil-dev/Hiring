"""
Warm-up for proctoring: imports, then the shared model pool.

Why this exists
---------------
Importing mediapipe + ultralytics(+torch) costs ~102s cold on a dev box, and building
the models on top of that another ~4.6s. `start_session` used to do all of it inline in
an `async def`, so the first candidate to click "Start test" blocked the whole event
loop for over a minute. The fix is to pay that cost *before* the candidate clicks Start,
and never on the event loop.

What changed, and why the old rationale no longer holds
-------------------------------------------------------
This module used to pool whole `ProctorPipeline` objects, because each one owned a
private copy of every model. That was justified on two grounds, both of which turned out
to be wrong:

  * "Sharing would serialise every session behind one mutex."  True of a single shared
    instance, but not of a bounded POOL of model sets: K sets means K frames genuinely
    in parallel, with sessions above K queuing for a few milliseconds. See
    proctoring/model_registry.py.
  * "Per-session state is welded into the analyzers — CheatObjectDetector holds
    `_iou_tracker`, ..."  `_iou_tracker` was only ever read, by a method with no
    callers. The detector's real state was two cooldown timestamps. The face and pose
    state is a handful of counters and deques — kilobytes, not megabytes.

Pooling pipelines also scaled the wrong thing: memory grew with the number of
candidates rather than with the machine. 50 sessions meant ~3 GB of identical weights
and 200 executor threads.

So pipelines are no longer pooled — constructing one is now nearly free. What gets
warmed here is the model pool itself, which is sized from the CPU and shared. Sessions
still get their own analyzers, so identity isolation is unchanged.
"""

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import model_registry

logger = logging.getLogger(__name__)

# How many model sets to pre-build. Defaults to full capacity (PROCTOR_INFERENCE_SLOTS,
# see runtime.py) so no candidate pays a build cost. Set PROCTOR_PREWARM_SLOTS lower to
# trade first-session latency for startup time and idle memory.
#
# PROCTOR_POOL_SIZE is still honoured as a deprecated alias so existing deployments keep
# working; it used to mean "spare pipelines", which no longer exists as a concept.
def _prewarm_target() -> int:
    raw = (os.getenv("PROCTOR_PREWARM_SLOTS") or os.getenv("PROCTOR_POOL_SIZE") or "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("Invalid prewarm slot count %r — using full capacity", raw)
    return model_registry._pool.capacity


# Single worker on purpose: serialises builds so peak memory stays at one model set's
# worth, and two MediaPipe graph constructions never race.
_builder = ThreadPoolExecutor(max_workers=1, thread_name_prefix="proctor-warm")

_imports_warmed = False
_imports_lock = threading.Lock()


def warm_imports() -> None:
    """
    Do the expensive module imports once, off the request path.

    This is the bulk of the cold start (~102s measured). Doing it here means even a
    pool miss costs only the ~0.6s construction rather than a full cold load.
    """
    global _imports_warmed
    with _imports_lock:
        if _imports_warmed:
            return
        t0 = time.perf_counter()
        try:
            import cv2  # noqa: F401
            import mediapipe  # noqa: F401
            from ultralytics import YOLO  # noqa: F401

            # Cap OpenCV/torch internal threads before any model is built —
            # parallelism comes from the shared executor, so each inference must
            # stay single-threaded or K frames nest K x cores threads.
            from .runtime import configure_native_threads
            configure_native_threads()
        except Exception as e:
            # Don't set the flag — a later call may succeed (e.g. transient import error).
            logger.error("Proctoring import warmup failed: %s", e)
            return
        _imports_warmed = True
        logger.info("Proctoring imports warmed in %.2fs", time.perf_counter() - t0)


def _warm_slots() -> None:
    """Build the shared model sets. Runs on the builder thread."""
    warm_imports()
    target = _prewarm_target()
    if target <= 0:
        return
    built = model_registry.warm(target)
    logger.info("Proctoring model pool warmed (%d/%d sets)", built, target)


def ensure_pool() -> None:
    """
    Pre-build the shared model sets, up to the prewarm target.

    Only *submits* work to the builder thread — returns immediately, so this is safe to
    call from the event loop (e.g. the /warmup endpoint). Idempotent: model_registry
    never builds beyond its capacity.
    """
    _builder.submit(_warm_slots)


def acquire():
    """
    Build a pipeline for a new session.

    Pipelines are no longer pooled: an analyzer set is now a few kilobytes of
    counters because the models live in the shared pool, so there is nothing
    expensive left to pre-build per session. The first session still pays for
    building a model set if `ensure_pool()` has not finished.

    BLOCKING — never call this directly from the event loop; wrap it in
    `asyncio.to_thread`.
    """
    from .config import ProctorConfig
    from .pipeline import ProctorPipeline

    warm_imports()
    try:
        pipeline = ProctorPipeline(ProctorConfig())
        pipeline.initialize()
        return pipeline
    except Exception as e:
        # initialize() raises when the face models are unavailable — a pipeline that
        # cannot match faces is not proctoring, so refuse the session rather than hand
        # back something that silently reports clean frames.
        logger.error("Refusing to start a session without a working face module: %s", e)
        raise RuntimeError(
            "Proctoring pipeline could not be initialised — see earlier ERROR for the "
            "failing module. Refusing to start a session that cannot proctor."
        ) from e


def pool_stats() -> dict:
    """Model-pool depth and utilisation — the numbers to calibrate capacity against.

    `peak_in_use` vs `capacity` says whether PROCTOR_INFERENCE_SLOTS is sized right;
    a rising `waits` means frames are queueing for a slot and the box is at its
    inference ceiling.
    """
    stats = model_registry.stats()
    stats["imports_warmed"] = _imports_warmed
    stats["prewarm_target"] = _prewarm_target()
    return stats


async def reap_idle_sessions(interval_sec: int = 60, idle_sec: int = 900) -> None:
    """
    Evict sessions whose video stream went silent and never stopped cleanly.

    A candidate closing the tab (or a browser crash) never reaches /sessions/stop, and
    the WS disconnect handler deliberately does NOT drop the session — the client
    auto-reconnects and a pop would 4004 it. So the only safe cleanup is time-based.

    Sessions are far cheaper than they used to be (the models are shared now), but an
    abandoned one still holds its analyzers, its face embedding and a slot in the
    session cap — so reaping still matters.

    `idle_sec` must stay comfortably above the client's reconnect backoff (max 5 attempts
    with capped exponential delay) so a brief network drop is never reaped mid-test.
    """
    import asyncio

    from . import routes

    while True:
        try:
            await asyncio.sleep(interval_sec)
            cutoff = time.time() - idle_sec
            stale = [
                sid for sid, e in list(routes._sessions.items())
                if e.get("last_seen", e.get("session_started_at", 0)) < cutoff
            ]
            for sid in stale:
                entry = routes._sessions.pop(sid, None)
                if not entry:
                    continue
                try:
                    entry["pipeline"].shutdown()
                except Exception:
                    pass
                logger.info("Reaped idle proctoring session %s (idle > %ds, frames=%s)",
                            sid, idle_sec, entry.get("frame_count"))
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Session reaper error: %s", e)


def shutdown_pool() -> None:
    """Close the shared model sets. Called from the app lifespan on shutdown.

    This is the ONLY place native model handles get closed. Sessions must not close
    them — the sets are shared, so a session closing a landmarker would free the
    graph out from under every other candidate.
    """
    _builder.shutdown(wait=False)
    try:
        model_registry.shutdown()
    except Exception as e:
        logger.error("Model pool shutdown error: %s", e)
