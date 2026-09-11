"""Per-step pipeline notification config.

Resolves, for a given company + hiring-pipeline step, the extra CC recipients
(typed addresses + role-based) and an optional custom sender mailbox that HR
configured via the HR dashboard. A missing/disabled row means "default
behaviour" — only the primary recipient, company/Innomax sender.

Backed by the ``pipeline_step_configs`` table (src/models/models.py).
``extra_emails``/``notify_roles`` are JSON-encoded lists; ``smtp_pass`` is
encrypted at rest (src/security.py). Everything here is defensive: a config
error must never break the actual email send, so callers can rely on the
returned dict always having sane keys.
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


def mailbox_unusable_reason(mb) -> Optional[str]:
    """Why this mailbox can't send, or None if it's usable.

    Shared by the send path (_mailbox_smtp_config) and the read-only Admin
    routing view (describe_step_dispatch) so the two can never disagree about
    whether a mailbox is actually going to be used.
    """
    if not mb:
        return "no such mailbox"
    if not mb.is_active:
        return "mailbox is inactive"
    if not mb.smtp_user:
        return "mailbox has no SMTP user"
    if not mb.smtp_pass:
        return "mailbox has no password set — add it in Admin > Email Settings"
    return None


def _mailbox_smtp_config(db, mailbox_id: Optional[str],
                         step_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Build an smtp_config dict from an Admin-defined mailbox, or None.

    Returns None when no mailbox is selected, the mailbox is missing/inactive,
    or it lacks credentials — caller then falls back to resolve_sender.
    """
    if not mailbox_id or db is None:
        return None
    try:
        from src.models.models import CompanyMailbox
        mb = db.query(CompanyMailbox).filter(CompanyMailbox.id == mailbox_id).first()
    except Exception as e:
        logger.warning("Mailbox %s lookup failed, falling back to default sender: %s",
                       mailbox_id, e)
        return None

    # Say WHY we are falling back. This used to return None silently, so a mailbox
    # that HR had selected for a pipeline step — and that looks correctly configured
    # in the UI — would quietly send from the company default address instead, with
    # nothing in the logs to explain it. A blank password is the common case: mailboxes
    # can be created without one (and the seed script deliberately does).
    reason = mailbox_unusable_reason(mb)
    if reason:
        logger.warning("Mailbox %s (%s) unusable: %s. Falling back to the default sender.",
                       mailbox_id, getattr(mb, "name", "?"), reason)
        return None
    try:
        from src.security import decrypt
        password = decrypt(mb.smtp_pass) or mb.smtp_pass  # tolerate legacy plain
    except Exception:
        password = mb.smtp_pass
    return {
        "host": mb.smtp_host or "smtp.gmail.com",
        "port": int(mb.smtp_port or 587),
        "user": mb.smtp_user,
        "password": password,
        "from_email": mb.smtp_from_email or mb.smtp_user,
        "hourly_limit": mb.hourly_limit,
        # Ride along for the delivery log. _send/dispatch only .get() from this
        # dict, so non-SMTP keys are inert here -- hourly_limit set the precedent.
        "company_id": getattr(mb, "company_id", None),
        "step_key": step_key,
    }


def resolve_step_dispatch(db, company_id: Optional[str], step_key: str, *,
                          interview=None, job=None, offer=None) -> Dict[str, Any]:
    """Return CC recipients + optional sender override for a pipeline step.

    Returns ``{"cc": [...], "smtp_config": {...}|None, "enabled": bool}``.
    Never raises — on any error it returns the no-op default so the send
    proceeds exactly as before this feature existed.
    """
    default = {"cc": [], "smtp_config": None, "enabled": True}
    if not company_id or db is None or not is_valid_step(step_key):
        return default
    try:
        row = _load_row(db, company_id, step_key)
        if not row:
            return default
        if not row.enabled:
            return {"cc": [], "smtp_config": None, "enabled": False}

        # CC is ONLY the addresses HR explicitly typed for this step — no role
        # expansion or any other automatic mapping.
        return {
            "cc": _clean_emails(_json_list(row.extra_emails)),
            "smtp_config": _mailbox_smtp_config(db, getattr(row, "mailbox_id", None),
                                                step_key),
            "enabled": True,
        }
    except Exception as e:
        logger.warning(f"resolve_step_dispatch failed for {company_id}/{step_key}: {e}")
        return default


def mailbox_smtp_config(db, company_id: Optional[str],
                        mailbox_id: Optional[str],
                        step_key: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Company-scoped mailbox lookup for a CALLER-SUPPLIED mailbox id.

    ``_mailbox_smtp_config`` looks a mailbox up by id alone, which is safe there
    because the id comes from the company's own PipelineStepConfig row. This
    variant is for ids that arrive over the wire (HR choosing a "send from"
    mailbox), so it additionally verifies ownership — without that check an HR
    user could send using another tenant's SMTP credentials by guessing an id.

    Returns None when the mailbox is missing, belongs to another company, or is
    unusable — caller then falls back to its normal sender resolution.
    """
    if not mailbox_id or not company_id or db is None:
        return None
    try:
        from src.models.models import CompanyMailbox
        mb = (
            db.query(CompanyMailbox)
            .filter(CompanyMailbox.id == mailbox_id,
                    CompanyMailbox.company_id == company_id)
            .first()
        )
    except Exception as e:
        logger.warning("Scoped mailbox %s lookup failed: %s", mailbox_id, e)
        return None
    if mb is None:
        logger.warning("Mailbox %s not found for company %s — ignoring override.",
                       mailbox_id, company_id)
        return None
    return _mailbox_smtp_config(db, mb.id, step_key)


def resolve_step_sender(db, company_id: Optional[str], step_key: str,
                        override_mailbox_id: Optional[str] = None, *,
                        job=None, category: str = "candidate"):
    """Resolve the sender + CC for a pipeline step, honouring an HR override.

    Precedence: HR's per-send choice → the step's configured mailbox → the
    company/platform default. Returns ``(smtp_config, dispatch_dict)`` where
    dispatch_dict is exactly what ``resolve_step_dispatch`` returns, so callers
    keep using ``disp["cc"]`` unchanged.

    Collapses the ``disp["smtp_config"] or resolve_sender(...)`` pattern that is
    repeated at every send site, so the override cannot be applied inconsistently.
    """
    from src.services.email_service import resolve_sender

    disp = resolve_step_dispatch(db, company_id, step_key, job=job)
    smtp = (
        mailbox_smtp_config(db, company_id, override_mailbox_id, step_key)
        or disp["smtp_config"]
        or resolve_sender(db, company_id, category)
    )
    return smtp, disp


def describe_step_dispatch(db, company_id: Optional[str], step_key: str) -> Dict[str, Any]:
    """Read-only mirror of resolve_step_dispatch for the Admin routing view.

    Reports what the send path *would* do — which mailbox wins, or why it falls
    back — without ever building an smtp_config, so no password (plaintext or
    ciphertext) can reach the API response. Shares mailbox_unusable_reason with
    the real resolver so the two cannot disagree.

    Returns::

        {"step_key", "label", "enabled", "cc": [...], "as_cc": bool,
         "mailbox": {...}|None, "fallback_reason": str|None}

    ``mailbox`` is set only when the mailbox is actually usable. When it is None
    and ``fallback_reason`` is set, the step sends from the company default.
    Never raises.
    """
    out = {
        "step_key":        step_key,
        "label":           STEP_LABELS.get(step_key, step_key),
        "enabled":         True,
        "cc":              [],
        "as_cc":           True,
        "mailbox":         None,
        "fallback_reason": None,
    }
    if not company_id or db is None or not is_valid_step(step_key):
        return out
    try:
        row = _load_row(db, company_id, step_key)
        if not row:
            return out

        out["enabled"] = bool(row.enabled)
        out["as_cc"]   = bool(row.as_cc)
        # A disabled row short-circuits the resolver before CC parsing, so mirror
        # that here rather than reporting CCs that would never be applied.
        if not row.enabled:
            return out

        out["cc"] = _clean_emails(_json_list(row.extra_emails))

        mailbox_id = getattr(row, "mailbox_id", None)
        if not mailbox_id:
            return out

        from src.models.models import CompanyMailbox
        mb = db.query(CompanyMailbox).filter(CompanyMailbox.id == mailbox_id).first()
        reason = mailbox_unusable_reason(mb)
        if reason:
            out["fallback_reason"] = reason
            # Name the mailbox even when unusable — "Offers (no password set)" is
            # far more actionable than a bare reason with no mailbox attached.
            if mb is not None:
                out["mailbox"] = {
                    "id": mb.id, "name": mb.name,
                    "from_email": mb.smtp_from_email or mb.smtp_user or None,
                    "hourly_limit": mb.hourly_limit, "usable": False,
                }
            return out

        out["mailbox"] = {
            "id":           mb.id,
            "name":         mb.name,
            "from_email":   mb.smtp_from_email or mb.smtp_user,
            "hourly_limit": mb.hourly_limit,
            "usable":       True,
        }
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
