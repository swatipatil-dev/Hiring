#!/usr/bin/env sh
# Container entrypoint. ROLE selects what this instance runs:
#   web    (default) → FastAPI app via gunicorn/uvicorn, binds $PORT
#   proctor          → the same app, one worker, fixed port — see below
#   worker           → coding-judge RQ worker (run on a host that HAS Docker)
#   email-worker     → outbound-email RQ worker (rate-limited; needs the scheduler)
set -e

# Every role branch below ends in `exit 1` after its `exec`. That line is
# unreachable while exec does its job of replacing this process — it is there for
# the case where it doesn't (gunicorn or rq missing from PATH, say). Without it,
# execution would fall through to the web role at the bottom and quietly start a
# SECOND API on port 8000, colliding with the real one. That collision has already
# cost us a silent proctoring outage once; see hireflow-proctor.service.
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
    #
    # This process is root-equivalent (docker group), so it only runs what it must:
    #
    # --job-class   refuses any job that is not one of the judge's own functions;
    #               stock RQ runs whatever importable function a job names.
    # --serializer  jobs arrive as JSON, not pickle — unpickling alone can execute
    #               code. The API enqueues with the same serializer (src/judge/queue.py),
    #               so the two must be deployed together.
    #
    # And it holds no secret it doesn't use. systemd hands it the whole .env; the
    # worker needs DATABASE_URL (standalone submissions), REDIS_URL and the Gemini
    # settings (hidden test generation). The rest is dropped before Python starts,
    # and it cannot re-read .env: that file is 0600 and owned by the API user.
    unset SECRET_KEY EMAIL_ENCRYPTION_KEY JUDGE_REGRADE_TOKEN \
          SMTP_USER SMTP_PASS INNOMAX_HR_USER INNOMAX_HR_PASS \
          INNOMAX_OFFICIAL_USER INNOMAX_OFFICIAL_PASS \
          SUPERADMIN_PASSWORD PLATFORM_ADMIN_PASSWORD
    echo "Starting coding-judge RQ worker pool (${JUDGE_WORKERS:-3} workers)..."
    exec rq worker-pool -n "${JUDGE_WORKERS:-3}" "${JUDGE_QUEUE:-judge}" \
        --job-class src.judge.rq_safety.SafeJob \
        --serializer rq.serializers.JSONSerializer \
        --url "${JUDGE_REDIS_URL:-${REDIS_URL:-redis://localhost:6379/0}}"
    exit 1   # see the note above: a role branch must never fall through
fi

if [ "$ROLE" = "proctor" ]; then
    # Proctoring, and ONLY proctoring. Two values are hardcoded here on purpose,
    # because both are correctness constraints rather than tuning:
    #
    # -w 1  proctoring/routes.py keeps sessions in a process-local dict of live
    #       MediaPipe handles, which cannot be serialised or shared. A session's
    #       WebSocket must reach the process that created it; with N workers it
    #       does so 1/N of the time and closes 4004 otherwise.
    #
    # 8001  a distinct port from the API's 8000, matched by the ws:// and http://
    #       ProxyPass entries in deploy/cpanel/hireflow.conf. Change one, change
    #       the other.
    #
    # Neither reads WEB_CONCURRENCY or PORT, and that is the point. Both services
    # share one .env, and systemd applies EnvironmentFile= AFTER Environment=, so
    # a PORT=8000 in .env silently beat the Environment=PORT=8001 pinned in
    # hireflow-proctor.service: proctoring bound 8000, won the race, the API
    # crash-looped on "Connection in use", and the site kept serving from the
    # proctor process while proctoring itself was unreachable. Capacity is tuned
    # with PROCTOR_INFERENCE_SLOTS, which is safe to set anywhere.
    if [ -n "${WEB_CONCURRENCY:-}" ] && [ "${WEB_CONCURRENCY}" != "1" ]; then
        echo "note: ignoring WEB_CONCURRENCY=${WEB_CONCURRENCY} — proctoring requires" \
             "exactly one worker. Tune PROCTOR_INFERENCE_SLOTS instead." >&2
    fi
    echo "Starting HireFlow proctoring on ${BIND_HOST:-127.0.0.1}:8001 with 1 worker..."
    exec gunicorn main:app \
        -k uvicorn.workers.UvicornWorker \
        -w 1 \
        -b "${BIND_HOST:-127.0.0.1}:8001" \
        --timeout 0 \
        --access-logfile - \
        --error-logfile -
    exit 1   # see the note above: a role branch must never fall through
fi

if [ "$ROLE" = "email-worker" ]; then
    # --with-scheduler is required so deferred (rate-limited/retry) sends fire.
    #
    # This process holds the key that decrypts every company's mailbox password, so
    # like the judge it only runs what it must (src/mailq/safety.py):
    #
    # --job-class   refuses any job that is not the email send job.
    # --serializer  jobs arrive as JSON, not pickle — unpickling alone can execute
    #               code. The API enqueues with the same serializer, so the two must
    #               be deployed together (drain the queue first: docs/DEPLOY_UPDATE.md).
    #
    # The Redis URL comes from the same function the API uses, so both agree on the
    # email queue's own database (EMAIL_REDIS_DB, default 1) rather than the judge's.
    # A standalone assignment, so `set -e` stops here if it fails instead of letting
    # rq fall back to localhost db 0.
    EMAIL_QUEUE_URL="$(python -c 'from src.mailq.queue import resolve_redis_url; print(resolve_redis_url())')"
    # Secrets it never uses. It keeps DATABASE_URL (delivery log), EMAIL_ENCRYPTION_KEY
    # (queued mailbox passwords) and the SMTP/Innomax mailbox credentials it sends with.
    unset SECRET_KEY JUDGE_REGRADE_TOKEN SUPERADMIN_PASSWORD PLATFORM_ADMIN_PASSWORD \
          GEMINI_API_KEY
    echo "Starting email RQ worker (with scheduler)..."
    exec rq worker "${EMAIL_QUEUE:-email}" --with-scheduler \
        --job-class src.mailq.safety.SafeEmailJob \
        --serializer rq.serializers.JSONSerializer \
        --url "$EMAIL_QUEUE_URL"
    exit 1   # see the note above: a role branch must never fall through
fi

# Web role — the API. Worker count = WEB_CONCURRENCY (default 2 — never 1 here:
# with blocking DB/Gemini calls a single worker serializes every request, which is
# what caused the QueuePool-exhaustion outage).
#
# Proctoring does NOT use this role; it has its own above, precisely so that its
# one-worker requirement is not a variable anything can override.
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
