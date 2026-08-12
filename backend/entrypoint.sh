#!/usr/bin/env sh
# Container entrypoint. ROLE selects what this instance runs:
#   web    (default) → FastAPI app via gunicorn/uvicorn, binds $PORT (Cloud Run)
#   worker           → coding-judge RQ worker (run on a host that HAS Docker)
#   email-worker     → outbound-email RQ worker (rate-limited; needs the scheduler)
set -e

ROLE="${ROLE:-web}"

if [ "$ROLE" = "worker" ]; then
    # A worker POOL, not a single worker. The API no longer executes sandboxes
    # itself — it enqueues here and waits for the result inside the HTTP request
    # (see src/judge/queue.py run_in_worker). With one worker, two candidates
    # pressing "Run Code" would serialise, so concurrency has to live here.
    #
    # Each concurrent job still runs its own container with its own
    # --memory/--cpus/--pids-limit caps from src/judge/sandbox.py, so raising
    # JUDGE_WORKERS raises real resource use. Keep it at or below the CPU count.
    echo "Starting coding-judge RQ worker pool (${JUDGE_WORKERS:-3} workers)..."
    exec rq worker-pool -n "${JUDGE_WORKERS:-3}" "${JUDGE_QUEUE:-judge}" \
        --url "${JUDGE_REDIS_URL:-${REDIS_URL:-redis://localhost:6379/0}}"
fi

if [ "$ROLE" = "email-worker" ]; then
    echo "Starting email RQ worker (with scheduler)..."
    # --with-scheduler is required so deferred (rate-limited/retry) sends fire.
    exec rq worker "${EMAIL_QUEUE:-email}" --with-scheduler \
        --url "${EMAIL_REDIS_URL:-${JUDGE_REDIS_URL:-${REDIS_URL:-redis://localhost:6379/0}}}"
fi

# Web role. Worker count = WEB_CONCURRENCY (default 2 — never 1 for the API: with
# blocking DB/Gemini calls a single worker serializes every request, which is what
# caused the QueuePool-exhaustion outage).
#
# EXCEPTION: the proctoring service MUST run WEB_CONCURRENCY=1. Its session state
# is a process-local dict of live MediaPipe handles, so a session's WebSocket has
# to reach the same process that created it. See proctoring/routes.py and
# deploy/cpanel/hireflow-proctor.service.
#
# BIND_HOST defaults to 127.0.0.1 so the app is reachable only through the Apache
# reverse proxy. Set BIND_HOST=0.0.0.0 only where the port itself is protected.
#
# --timeout 0 disables gunicorn's worker timeout so long-running generation and
# proctoring WebSockets aren't killed.
echo "Starting HireFlow on ${BIND_HOST:-127.0.0.1}:${PORT:-8000} with ${WEB_CONCURRENCY:-2} worker(s)..."
exec gunicorn main:app \
    -k uvicorn.workers.UvicornWorker \
    -w "${WEB_CONCURRENCY:-2}" \
    -b "${BIND_HOST:-127.0.0.1}:${PORT:-8000}" \
    --timeout 0 \
    --access-logfile - \
    --error-logfile -
