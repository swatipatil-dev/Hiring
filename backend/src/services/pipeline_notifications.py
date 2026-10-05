"""Per-step pipeline notification settings.

For each company and hiring-pipeline step:

* **CC** — extra addresses HR typed, from ``pipeline_step_configs``
  (``extra_emails``/``as_cc``; JSON-encoded lists).
* **Sender and on/off** — the step's ``email_routes`` row, decided by the email
  API's sender policy (src/email_api/policy.py). A switched-off step sends
  nothing; a step whose chosen sender cannot send is blocked, not quietly sent
  from somewhere else.

The ``enabled``/``mailbox_id`` columns of ``pipeline_step_configs`` are no longer
read; revision t2e3m4a5i6l7 copied them into ``email_routes``.
"""

import json
import logging
import re
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)

# step_key -> human label. Order matters: the HR UI renders steps in this order.
STEP_LABELS: Dict[str, str] = {
    "application_received": "Application Received",
    # No "shortlisted" entry on purpose. There is a send_candidate_shortlisted
    # template, but nothing ever calls it and "shortlisted" is never passed to
    # resolve_step_dispatch/resolve_step_sender — so the row was configurable in
    # the HR UI while being incapable of sending anything. Listing it invited
    # admins to assign a mailbox to a stage that cannot fire. Re-add it here at
    # the same time as wiring the sender, not before.
    "test_invitation":     "Assessment Invitation",
    "mcq_result":          "Assessment Result",
    "interview_scheduled": "Interview Scheduled",
    "panel_invite":        "Panel Interview Invite",
    "offer_letter":        "Offer Letter",
    "offer_accepted":      "Offer Accepted (notify HR)",
    "offer_rejected":      "Offer Declined (notify HR)",
    "candidate_rejected":  "Candidate Rejected",
    # Not a pipeline stage — the HR dashboard's ad-hoc Compose Mail. Present so that
    # ad-hoc sends resolve their sender through the same precedence chain as every
    # automated send, and so Admin can give them a default sender + CC in the
    # Pipeline Notifications UI. Keep it last: STEP_KEYS order drives that UI.
    "custom_mail":         "Custom / Ad-hoc Mail",
}
STEP_KEYS: List[str] = list(STEP_LABELS.keys())

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def is_valid_step(step_key: str) -> bool:
    return step_key in STEP_LABELS


def _json_list(raw: Optional[str]) -> List[str]:
    """Parse a JSON list column into a clean list of strings."""
    if not raw:
        return []
    try:
        val = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(val, list):
        return []
    return [str(x).strip() for x in val if str(x).strip()]


def _clean_emails(addrs: List[str]) -> List[str]:
    """De-dupe (case-insensitive), keep only well-formed addresses, preserve order."""
    seen = set()
    clean: List[str] = []
    for a in addrs:
        a = (a or "").strip()
        if a and _EMAIL_RE.match(a) and a.lower() not in seen:
            seen.add(a.lower())
            clean.append(a)
    return clean


def _load_row(db, company_id: str, step_key: str):
    from src.models.models import PipelineStepConfig
    return (
        db.query(PipelineStepConfig)
        .filter(
            PipelineStepConfig.company_id == company_id,
            PipelineStepConfig.step_key == step_key,
        )
        .first()
    )


def step_cc(db, company_id: Optional[str], step_key: str) -> List[str]:
    """The extra addresses HR typed for this step (CC). Never raises."""
    if not company_id or db is None or not is_valid_step(step_key):
        return []
    try:
        row = _load_row(db, company_id, step_key)
        return _clean_emails(_json_list(row.extra_emails)) if row else []
    except Exception as e:
        logger.warning(f"step CC lookup failed for {company_id}/{step_key}: {e}")
        return []


def resolve_step_dispatch(db, company_id: Optional[str], step_key: str, *,
                          interview=None, job=None, offer=None) -> Dict[str, Any]:
    """The sender decision and CC list for one pipeline step.

    Returns ``{"cc": [...], "smtp_config": SenderRef, "enabled": bool}``.
    ``smtp_config`` is ALWAYS a ``SenderRef`` — ok, blocked or disabled — so the
    ``disp["smtp_config"] or resolve_sender(...)`` pattern at the call sites
    stops at the policy's answer and never falls through to another sender.
    """
    from src.services.email_service import resolve_sender
    ref = resolve_sender(db, company_id, event_type=step_key)
    enabled = ref.decision.status != "disabled"
    return {"cc": step_cc(db, company_id, step_key) if enabled else [],
            "smtp_config": ref, "enabled": enabled}


def mailbox_smtp_config(db, company_id: Optional[str], mailbox_id: Optional[str],
                        step_key: Optional[str] = None):
    """The decision for an explicitly chosen sender (a "mailbox" in the HR UI).

    None when no id was given. A chosen id that is not this company's, or that
    cannot send, comes back as a blocked decision — never as some other sender.
    """
    if not mailbox_id or not company_id or db is None:
        return None
    from src.services.email_service import resolve_sender
    return resolve_sender(db, company_id, event_type=step_key or "custom_mail",
                          requested_sender_id=mailbox_id)


def resolve_step_sender(db, company_id: Optional[str], step_key: str,
                        override_mailbox_id: Optional[str] = None, *,
                        job=None, category: str = "candidate"):
    """Sender + CC for a pipeline step, honouring HR's per-send choice.

    Precedence (in the sender policy): HR's choice → the step's routed sender →
    the company default. Returns ``(SenderRef, dispatch_dict)``. An HR choice
    that is not usable blocks the message; it is not silently replaced.
    """
    disp = resolve_step_dispatch(db, company_id, step_key, job=job)
    if override_mailbox_id and disp["enabled"]:
        return mailbox_smtp_config(db, company_id, override_mailbox_id, step_key), disp
    return disp["smtp_config"], disp


def describe_step_dispatch(db, company_id: Optional[str], step_key: str) -> Dict[str, Any]:
    """What one step's mail will do, for the Admin routing view and HR previews.

    Returns::

        {"step_key", "label", "enabled", "cc": [...], "as_cc": bool,
         "mailbox": {...}|None, "fallback_reason": str|None,
         "effective": {"status", "reason_code", "reason", "route_source",
                       "sender_id", "from_email"}}

    ``mailbox`` is the sender routed for this step (even when it cannot send, so
    the UI can name it). ``fallback_reason`` is set when mail for the step will
    NOT be sent from the expected place — it is now a reason the mail is
    blocked, not a note that another sender stands in. Never returns a secret.
    """
    from src.email_api.policy import resolve_sender as policy_resolve
    from src.email_api import senders as S
    out = {
        "step_key":        step_key,
        "label":           STEP_LABELS.get(step_key, step_key),
        "enabled":         True,
        "cc":              [],
        "as_cc":           True,
        "mailbox":         None,
        "fallback_reason": None,
        "effective":       None,
    }
    if not company_id or db is None or not is_valid_step(step_key):
        return out
    try:
        from src.models.models import EmailRoute
        d = policy_resolve(db, company_id, step_key)
        out["effective"] = {"status": d.status, "reason_code": d.reason_code, "reason": d.reason,
                            "route_source": d.route_source, "sender_id": d.sender_id,
                            "from_email": d.from_email}
        out["enabled"] = d.status != "disabled"
        row = _load_row(db, company_id, step_key)
        if row is not None:
            out["as_cc"] = bool(row.as_cc)
        if not out["enabled"]:
            return out
        out["cc"] = step_cc(db, company_id, step_key)
        route = (db.query(EmailRoute)
                 .filter(EmailRoute.company_id == company_id, EmailRoute.event_type == step_key)
                 .first())
        if route is not None and route.sender_id:
            sender, conn = S.load(db, company_id, route.sender_id)
            reason = S.usable_reason(sender, conn)
            if sender is not None:
                out["mailbox"] = {"id": sender.id, "name": sender.label,
                                  "from_email": sender.email,
                                  "hourly_limit": sender.hourly_limit or (conn.hourly_limit if conn else None),
                                  "usable": reason is None}
        if d.status == "blocked":
            out["fallback_reason"] = d.reason
        return out
    except Exception as e:
        logger.warning(f"describe_step_dispatch failed for {company_id}/{step_key}: {e}")
        return out


def serialize_row(row) -> Dict[str, Any]:
    """Shape a PipelineStepConfig row for API responses."""
    return {
        "step_key":     row.step_key,
        "enabled":      bool(row.enabled),
        "extra_emails": _json_list(row.extra_emails),
        "as_cc":        bool(row.as_cc),
        "mailbox_id":   row.mailbox_id or None,
    }


def default_step_payload(step_key: str) -> Dict[str, Any]:
    """Default (unconfigured) shape for a step, for the GET endpoint."""
    return {
        "step_key":     step_key,
        "label":        STEP_LABELS.get(step_key, step_key),
        "enabled":      True,
        "extra_emails": [],
        "as_cc":        True,
        "mailbox_id":   None,
    }
