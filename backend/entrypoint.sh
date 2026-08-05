#!/usr/bin/env sh
# Container entrypoint. ROLE selects what this instance runs:
#   web    (default) → FastAPI app via gunicorn/uvicorn, binds $PORT (Cloud Run)
#   worker           → coding-judge RQ worker (run on a host that HAS Docker)
#   email-worker     → outbound-email RQ worker (rate-limited; needs the scheduler)
set -e

ROLE="${ROLE:-web}"

if [ "$ROLE" = "worker" ]; then
    echo "Starting coding-judge RQ worker..."
    exec rq worker "${JUDGE_QUEUE:-judge}" \
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
