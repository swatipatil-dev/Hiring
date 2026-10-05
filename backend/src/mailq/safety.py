"""What the email worker will agree to run.

Same threat as the judge (see src/judge/rq_safety.py): stock RQ runs ANY importable
function a job names, and carries its arguments as a pickle, where unpickling alone
can execute code. The email worker holds the SMTP credentials and the key that
decrypts every company's mailbox password, so anyone able to write to its Redis
could otherwise run code as the worker and read them all.

Two changes close that, and both are needed:

* ``SafeEmailJob`` refuses any job whose function is not the worker's own send job,
  before anything is imported or called.
* Jobs and results travel as JSON (``SERIALIZER``), so reading a job off the queue can
  never execute anything. Payloads are plain dicts of strings/numbers already
  (src/services/email_service.py builds them).

The worker is started with both (backend/entrypoint.sh, scripts/run_email_worker_windows.py);
the API enqueues with the same serializer (src/mailq/queue.py). They must be deployed
together: a pickled job left on the queue from before the switch cannot be read by
the new worker — drain the queue first (docs/DEPLOY_UPDATE.md).
"""
from __future__ import annotations

from typing import Any

from rq.job import Job
from rq.serializers import JSONSerializer

SERIALIZER = JSONSerializer

SEND_JOB = "src.mailq.worker.send_email_job"
# The email API's job. Its payload is only {"message_id", "tenant_id"}; the
# worker loads everything else from the database (src/email_api/worker.py).
DELIVER_JOB = "src.email_api.worker.deliver_message_job"

ALLOWED_JOBS = frozenset({SEND_JOB, DELIVER_JOB})


class ForbiddenJob(RuntimeError):
    """A job named a function the email worker does not run."""


def check_allowed(func_name: str) -> None:
    if func_name not in ALLOWED_JOBS:
        raise ForbiddenJob(f"refusing to run {func_name!r}: not an email job")


class SafeEmailJob(Job):
    """An RQ Job that only runs the email worker's own send job."""

    def _execute(self) -> Any:
        # func_name comes from the JSON payload; nothing has been imported or called yet.
        check_allowed(self.func_name)
        if self.instance is not None:
            raise ForbiddenJob("refusing to run a bound method")
        return super()._execute()
