"""
🔔 NOTIFICATION SERVICE
Tiny helper for creating in-app notifications. Keeps every emit site to a single
call so notifications are created consistently across the app.

Notifications are keyed on the recipient's User.id (NOT Candidate.id). Pass
commit=False from inside flows that already commit their primary record, so the
notification flushes in the same transaction and never persists on rollback.
"""

import logging

from src.models.models import Notification

logger = logging.getLogger(__name__)


def create_notification(
    db,
    *,
    user_id,
    company_id=None,
    title,
    message,
    type="info",
    reference_type=None,
    reference_id=None,
    commit=True,
):
    """Create a notification for a user. Returns the Notification, or None if no
    user_id was supplied (e.g. an anonymous/legacy application with no linked user).

    Failures are swallowed and logged — a notification must never break the
    primary workflow that triggered it.
    """
    if not user_id:
        return None
    try:
        notif = Notification(
            user_id=user_id,
            company_id=company_id,
            title=title,
            message=message,
            type=type,
            reference_type=reference_type,
            reference_id=str(reference_id) if reference_id is not None else None,
        )
        db.add(notif)
        if commit:
            db.commit()
        return notif
    except Exception as e:  # pragma: no cover - defensive
        logger.warning(f"Failed to create notification for user {user_id}: {e}")
        return None
