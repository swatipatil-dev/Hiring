"""A program that floods its output cannot exhaust the judge worker's memory.

The worker used to read a container's entire stdout/stderr and slice it afterwards,
so one submission printing gigabytes could push the worker past its MemoryMax and
kill every queued run with it. Output is now read in chunks and anything past the
cap is discarded as it arrives.

These drive ``sandbox._execute`` (the part of run_in_sandbox that talks to the
child process) with a plain Python child, so no Docker is needed.
"""
import sys
import tracemalloc

from src.judge import sandbox

FLOOD_MB = 64
_FLOOD = f"import sys\nchunk = b'x' * 1048576\nfor _ in range({FLOOD_MB}): sys.stdout.buffer.write(chunk)\n" \
         "sys.stderr.write('done')\n"


def test_flood_is_capped_while_reading():
    cap = 256 * 1024
    tracemalloc.start()
    try:
        code, out, err, timed_out, truncated = sandbox._execute(
            [sys.executable, "-c", _FLOOD], b"", timeout_s=60, cap=cap)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert code == 0 and not timed_out
    assert len(out) == cap and truncated
    assert err == b"done"
    # 64 MB went through the pipe; the worker held a small fraction of it.
    assert peak < 8 * 1024 * 1024, peak


def test_small_output_is_untouched():
    code, out, err, timed_out, truncated = sandbox._execute(
        [sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"],
        b"hello", timeout_s=30)
    assert (code, out.strip(), timed_out, truncated) == (0, b"HELLO", False, False)


def test_timeout_kills_and_reports():
    killed = []
    code, out, _, timed_out, _ = sandbox._execute(
        [sys.executable, "-c", "import time, sys; print('started', flush=True); time.sleep(30)"],
        b"", timeout_s=1, on_timeout=lambda: killed.append(True))
    assert timed_out and code == 124 and killed == [True]
    assert out.strip() == b"started"


def test_program_that_ignores_its_input_does_not_hang():
    # 8 MB of stdin to a program that never reads it: the writer must not block.
    code, _, _, timed_out, _ = sandbox._execute(
        [sys.executable, "-c", "print('ok')"], b"x" * (8 * 1024 * 1024), timeout_s=30)
    assert code == 0 and not timed_out
