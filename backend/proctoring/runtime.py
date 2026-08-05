"""
Process-wide proctoring runtime: the shared inference executor and native-thread caps.

Why this module exists
----------------------
Both settings here are about the same failure: oversubscribing the CPU.

1. `ProctorPipeline` used to own a `ThreadPoolExecutor(max_workers=4)` per session.
   At 50 concurrent candidates that is 200 threads competing for 8 vCPU, and thread
   count — not memory — is what falls over first. Inference capacity is a property
   of the machine, not of how many people happen to be taking a test, so the pool
   belongs here and is sized from the CPU.

2. OpenCV and torch each default to spawning their own internal thread pool sized
   to the core count. With K frames in flight that nests K x cores threads inside
   the K we already have. Since parallelism now comes from the executor, each
   individual inference should stay single-threaded.

Sizing: `PROCTOR_INFERENCE_SLOTS` is the one knob. It caps how many frames may be
in inference simultaneously and is what `model_registry` sizes its model pool from,
so the two can never drift apart. Default leaves 2 cores for MySQL, Apache, the
judge containers and the event loop itself.

For the cleanest result also set `OMP_NUM_THREADS=1` in the service unit —
`torch.set_num_threads()` covers torch at runtime, but OpenMP reads its env var
before any of this code runs.
"""

import atexit
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

logger = logging.getLogger(__name__)

_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()
_threads_configured = False


def inference_slots() -> int:
    """How many frames may be in inference at once (K).

    Also the size of the shared model pool — one borrower per slot — so raising
    this raises both capacity and resident model memory together.
    """
    raw = os.getenv("PROCTOR_INFERENCE_SLOTS", "").strip()
    if raw:
        try:
            return max(1, int(raw))
        except ValueError:
            logger.warning("PROCTOR_INFERENCE_SLOTS=%r is not an integer — using the default", raw)
    return max(2, (os.cpu_count() or 4) - 2)


def configure_native_threads() -> None:
    """Pin OpenCV and torch to one thread each. Idempotent.

    Must run before models are built. Called from `warm_imports()` and again,
    defensively, when a model set is constructed.
    """
    global _threads_configured
    if _threads_configured:
        return
    _threads_configured = True

    try:
        import cv2
        cv2.setNumThreads(1)
    except Exception as e:  # pragma: no cover - depends on the installed build
        logger.debug("Could not cap OpenCV threads: %s", e)

    try:
        import torch
        torch.set_num_threads(1)
    except Exception as e:  # pragma: no cover
        logger.debug("Could not cap torch threads: %s", e)

    logger.info(
        "Proctoring native threads capped to 1 (inference slots=%d, cpus=%s)",
        inference_slots(), os.cpu_count(),
    )


def get_executor() -> ThreadPoolExecutor:
    """The one executor every session's frame work runs on.

    Sized a little above the slot count: vision work blocks on a model slot, but
    audio and behavioral analysis also run here and need no slot, so a couple of
    spare threads keep them from queueing behind vision.
    """
    global _executor
    if _executor is None:
        with _executor_lock:
            if _executor is None:
                workers = inference_slots() + 2
                _executor = ThreadPoolExecutor(
                    max_workers=workers, thread_name_prefix="proctor",
                )
                logger.info("Proctoring executor started with %d threads", workers)
    return _executor


def shutdown_runtime() -> None:
    """Release the shared executor. Called from the app lifespan on shutdown."""
    global _executor
    with _executor_lock:
        ex, _executor = _executor, None
    if ex is not None:
        ex.shutdown(wait=False)
        logger.info("Proctoring executor shut down")


atexit.register(shutdown_runtime)
