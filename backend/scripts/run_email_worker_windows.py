"""Windows-compatible RQ email worker.

Same SIGALRM workaround as the judge worker: run a ``SimpleWorker`` wired to
``TimerDeathPenalty`` (thread-timer based) so it works on Windows. Runs WITH the
scheduler so deferred sends (rate-limited / retry-backoff) actually fire.

Run from the repo root with Redis up:
    python scripts/run_email_worker_windows.py
Env: EMAIL_REDIS_URL, or REDIS_URL + EMAIL_REDIS_DB (default db 1) — resolved
     exactly as the API does (src/mailq/queue.resolve_redis_url);
     EMAIL_QUEUE (default email).

Jobs are JSON and only the send job runs (src/mailq/safety.py), as in production.
"""
import os
import sys

# Ensure the repo root is importable so jobs can resolve "src.mailq.worker...".
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import redis
from rq import Queue, SimpleWorker
from rq.timeouts import TimerDeathPenalty

from src.mailq.queue import resolve_redis_url
from src.mailq.safety import SERIALIZER, SafeEmailJob

REDIS_URL = resolve_redis_url()
QUEUE = os.getenv("EMAIL_QUEUE", "email")


class WindowsEmailWorker(SimpleWorker):
    death_penalty_class = TimerDeathPenalty


def main() -> None:
    conn = redis.from_url(REDIS_URL)
    worker = WindowsEmailWorker(
        [Queue(QUEUE, connection=conn, serializer=SERIALIZER)],
        connection=conn, serializer=SERIALIZER, job_class=SafeEmailJob,
    )
    print(f"[email-worker] listening on '{QUEUE}' via {REDIS_URL} "
          f"(SimpleWorker + TimerDeathPenalty, scheduler on)")
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
