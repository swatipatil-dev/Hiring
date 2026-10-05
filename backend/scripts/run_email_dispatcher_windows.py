"""Run the email outbox dispatcher on a Windows development machine.

The dispatcher is plain Python (no fork, no SIGALRM), so this only puts the repo
root on the path and starts the same loop production runs
(``ROLE=email-dispatcher`` in entrypoint.sh):

    python scripts/run_email_dispatcher_windows.py          # loop
    python scripts/run_email_dispatcher_windows.py --once   # one pass

Needs DATABASE_URL and Redis (EMAIL_REDIS_URL, or REDIS_URL + EMAIL_REDIS_DB)
resolved exactly as the API does. Run it alongside
scripts/run_email_worker_windows.py.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.email_api.outbox import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or ["--loop"]))
