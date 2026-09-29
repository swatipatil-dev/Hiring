"""
💰 SALARY VISIBILITY — who may see pay figures

Pay figures are the job's salary range, a candidate's current and expected salary, and
an offer's salary and bonus. The rule, enforced on the server:

* admin        — every job in their own company.
* hr           — only jobs they own or co-own (src/services/job_access.can_access_job).
* interviewer, candidate-facing staff views, anyone else — never.
* superadmin   — never through tenant routes; they have no business reading client pay.

A candidate still sees the salary range the company *published* on a job ad and their
own offer — those are candidate-facing routes and don't go through here.

Hiding a field in the UI is not enforcement: anything in the JSON is readable in the
browser's network tab. Routes must drop the keys before returning, via
``redact_salary`` / ``redact_salary_many``.
"""

from typing import Iterable, List, Optional

# Every key a route might use for a pay figure. Nested dicts are walked, so an offer
# embedded in a candidate profile is covered too.
SALARY_KEYS = frozenset({
    "salary", "salary_min", "salary_max", "salary_range",
    "bonus",
    "current_salary", "expected_salary",
    "expected_salary_min", "expected_salary_max",
    "offer_salary", "offered_salary", "ctc", "expected_ctc", "current_ctc",
})


def can_view_salary(user, db=None, job=None) -> bool:
    """True if `user` may see pay figures, for `job` when one is given.

    Without a job (e.g. a cross-job list) an HR user is allowed only when the caller
    has already restricted the rows to jobs that HR can access — pass the job whenever
    the row has one.
    """
    role = (getattr(user, "role", None) or "").lower()
    if role == "admin":
        return job is None or str(getattr(job, "company_id", None)) == str(user.company_id)
    if role != "hr":
        return False
    if job is None:
        return True
    if str(getattr(job, "company_id", None)) != str(user.company_id):
        return False
    if db is None:
        return False
    from src.services.job_access import can_access_job
    hr_profile = getattr(user, "hr_profile", None)
    return bool(hr_profile and can_access_job(db, job, hr_profile))


def _strip(value):
    if isinstance(value, dict):
        return {k: _strip(v) for k, v in value.items() if k not in SALARY_KEYS}
    if isinstance(value, list):
        return [_strip(v) for v in value]
    return value


def redact_salary(data: dict, user, db=None, job=None) -> dict:
    """Return `data` without pay figures unless `user` may see them for `job`."""
    if can_view_salary(user, db=db, job=job):
        return data
    return _strip(data)


def redact_salary_many(rows: Iterable[dict], user, db=None,
                       job_for_row=None) -> List[dict]:
    """Redact a list of dicts. `job_for_row(row)` returns the row's job, or None."""
    out = []
    for row in rows:
        job: Optional[object] = job_for_row(row) if job_for_row else None
        out.append(redact_salary(row, user, db=db, job=job))
    return out
