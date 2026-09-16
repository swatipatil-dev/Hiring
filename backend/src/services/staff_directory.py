"""
👥 STAFF DIRECTORY

One place to ask "who can I actually assign work to?".

``is_active`` lives on ``User``, not on the profile tables, so every picker has to join.
Skipping that join is how a deactivated person keeps showing up in a dropdown and gets
re-assigned the moment after they were detached — and ``src/auth.py`` refuses their login,
so the work lands on nobody.

The one place that must NOT use these helpers is the Admin -> Users screen
(``admin_routes._visible_users_query``): an admin has to see deactivated people in order to
reactivate them. Historical analytics are the other deliberate exception.
"""

from src.models.models import HRProfile, InterviewerProfile, User


def _scope(q, company_id):
    """Company scoping, applied only when the caller has a company.

    Matches the convention used across the route layer: a caller with no company_id is a
    legacy/unscoped caller, and filtering on ``company_id == None`` would match nothing
    rather than everything.
    """
    if company_id:
        return q.filter(User.company_id == company_id)
    return q


def active_interviewer_query(db, company_id=None):
    """InterviewerProfiles whose user is still active. Feeds every interviewer picker."""
    q = db.query(InterviewerProfile).join(User, InterviewerProfile.user_id == User.id).filter(
        User.is_active == True  # noqa: E712 - SQL boolean, not a Python identity test
    )
    return _scope(q, company_id)


def active_hr_query(db, company_id=None):
    """HRProfiles whose user is still active. Feeds every HR picker."""
    q = db.query(HRProfile).join(User, HRProfile.user_id == User.id).filter(
        User.is_active == True  # noqa: E712
    )
    return _scope(q, company_id)


def active_user_by_email(db, company_id, email):
    """Resolve an email to an ACTIVE company user, or None.

    Used when someone is typed in by hand rather than picked from a list — a deactivated
    person must not be silently resolved back to a live account.
    """
    email = (email or "").strip()
    if not email:
        return None
    q = db.query(User).filter(
        User.email == email,
        User.is_active == True,  # noqa: E712
    )
    return _scope(q, company_id).first()
