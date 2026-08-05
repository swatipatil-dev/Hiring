"""
Windows-compatible RQ judge worker.

RQ's default job-timeout mechanism uses ``signal.SIGALRM`` (Unix only), so the
stock ``rq worker`` — and even ``SimpleWorker`` — crash every job on Windows with
``AttributeError: module 'signal' has no attribute 'SIGALRM'``. This launcher runs
a ``SimpleWorker`` (no fork) wired to ``TimerDeathPenalty`` (thread-timer based),
which works on Windows.

Run from the repo root with Redis up:
    python scripts/run_judge_worker_windows.py
Env: JUDGE_REDIS_URL (default redis://localhost:6379/0), JUDGE_QUEUE (default judge).
"""
import os
import sys

# Ensure the repo root is importable so jobs can resolve "src.judge.worker...".
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from rq import Queue, SimpleWorker
from rq.timeouts import TimerDeathPenalty

REDIS_URL = os.getenv("JUDGE_REDIS_URL", os.getenv("REDIS_URL", "redis://localhost:6379/0"))
QUEUE = os.getenv("JUDGE_QUEUE", "judge")


class WindowsJudgeWorker(SimpleWorker):
    # Thread-timer based timeout — no SIGALRM, so it runs on Windows.
    death_penalty_class = TimerDeathPenalty


def main() -> None:
    conn = redis.from_url(REDIS_URL)
    worker = WindowsJudgeWorker([Queue(QUEUE, connection=conn)], connection=conn)
    print(f"[judge-worker] listening on '{QUEUE}' via {REDIS_URL} "
          f"(SimpleWorker + TimerDeathPenalty)")
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
