"""
📧 EMAIL NOTIFICATION SERVICE
Builds the hiring workflow's emails and hands them to the email API.

Sending itself lives in ``src/email_api`` (docs/EMAIL_API.md). This module
keeps the message templates and the helpers the routes already call:

* ``resolve_sender`` / ``resolve_auth_sender`` / ``resolve_smtp_for_company``
  ask the sender policy and return a ``SenderRef`` — a decision, never a
  credential. Nothing here reads SMTP settings from the environment.
* ``dispatch`` persists the message (with its outbox row) through
  ``email_api.messages.submit``; the outbox publishes it after the transaction
  commits and the worker delivers it.
"""

import logging
from typing import Optional, Dict, Any
import os

from src.email_api import constants as EC
from src.email_api.compat import SenderRef, current_actor

logger = logging.getLogger(__name__)

# Kill switch, read by the delivery worker: false defers every message (nothing is
# dropped). Kept here because the admin views report it.
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "true").lower() == "true"

# ── Body logging ──────────────────────────────────────────────────────────────
# Message bodies are never logged by default. They carry OTP and password-reset
# codes — live authentication material — plus offer salaries, bonuses and terms,
# and a log line reaches journald, log shipping and any crash reporter behind it.
#
# Turning this on requires BOTH an explicit opt-in and DEBUG level, so flipping
# the app to DEBUG to chase an unrelated bug cannot start dumping offer letters.
# OTP mail is excluded unconditionally and cannot be re-enabled by either.
EMAIL_LOG_BODIES = os.getenv("EMAIL_LOG_BODIES", "").lower() in ("1", "true", "yes")

if EMAIL_LOG_BODIES:
    logger.warning(
        "EMAIL_LOG_BODIES is enabled — non-OTP message bodies will be written to "
        "the log at DEBUG level. This exposes offer salaries and terms. Do not "
        "leave this on in production."
    )


def _log_bodies_allowed(is_otp: bool) -> bool:
    """Whether this message's body may be logged. OTP mail: never."""
    if is_otp or not EMAIL_LOG_BODIES:
        return False
    return logger.isEnabledFor(logging.DEBUG)


if os.getenv("SMTP_TLS_VERIFY", "true").lower() in ("0", "false", "no"):
    # The escape hatch is gone: the email API always verifies certificates
    # (src/email_api/ssrf.py). Say so rather than ignore the setting silently.
    logger.error("SMTP_TLS_VERIFY=false is set but no longer honoured: SMTP certificates "
                 "are always verified. Trust the relay's CA instead.")


# Kept under its old name; the implementation moved with the MIME builder.
from src.email_api.mime import html_to_text as _html_to_text  # noqa: E402,F401


# ==================== SENDER RESOLUTION ====================
#
# Each returns a SenderRef: the sender policy's decision for one event
# (src/email_api/policy.py). A blocked or disabled decision is returned as such;
# it is never replaced by some other sender.

# Which event a legacy "category" stands for, for the callers that still pass
# one without an event_type.
_CATEGORY_EVENTS = {
    "candidate": "custom_mail",
    "client": "platform.tenant_welcome",
    "system": "workflow.reminder_digest",
}


def resolve_sender(db, company_id: Optional[str], category: str = "system", *,
                   event_type: Optional[str] = None,
                   requested_sender_id: Optional[str] = None) -> SenderRef:
    """The sender for one email, as decided by the sender policy."""
    from src.email_api.policy import resolve_sender as _policy
    event = event_type or _CATEGORY_EVENTS.get(category, "custom_mail")
    decision = _policy(db, company_id, event, requested_sender_id=requested_sender_id,
                       actor=current_actor())
    return SenderRef(decision, step_key=event if event in EC.EVENT_CATALOG else None)


def resolve_auth_sender(db, company_id: Optional[str],
                        purpose: str = EC.EVENT_AUTH_VERIFY) -> SenderRef:
    """The sender for verification / password-reset codes.

    The company's own auth route or default sender; otherwise the platform's
    explicit auth route (shown in Admin → Mail Routing). Never the environment.
    """
    return resolve_sender(db, company_id, event_type=purpose)

def _build_email(title: str, body_html: str, branding: Optional[Dict[str, Any]] = None) -> str:
    """Wrap body_html in a minimal, transactional-looking shell.

    Deliberately plain — gradients, oversized logos, and big marketing footers
    push transactional mail into spam. We keep one thin header bar with the
    company name and a small footer with a real contact address.
    """
    b = branding or {}
    company_name = b.get("company_name") or "HireFlow"
    color        = b.get("color") or "#2b7cba"
    support      = b.get("support_email") or ""

    footer_contact = (
        f'Questions? Reply to this email or contact <a href="mailto:{support}" '
        f'style="color:{color};text-decoration:none">{support}</a>.'
        if support
        else "This is an automated message; please do not reply."
    )

    return f"""
    <div style="font-family:-apple-system,Segoe UI,Roboto,Arial,sans-serif;max-width:560px;margin:0 auto;background:#ffffff;color:#1f2937">
      <div style="padding:18px 24px;border-bottom:2px solid {color}">
        <span style="font-size:1.05rem;font-weight:700;color:{color};letter-spacing:-0.01em">{company_name}</span>
      </div>
      <div style="padding:28px 24px;font-size:0.95rem;line-height:1.55">
        {body_html}
      </div>
      <div style="padding:14px 24px;border-top:1px solid #e5e7eb;color:#6b7280;font-size:0.78rem">
        {footer_contact}
      </div>
    </div>"""


def _display_name(ref: SenderRef, branding: Optional[Dict[str, Any]]) -> str:
    """The From display name: the sender's own, else the legacy branded form."""
    if ref.get("from_name"):
        return ref["from_name"]
    company_name = (branding or {}).get("company_name")
    if company_name and company_name.lower() != "hireflow":
        return f"{company_name} via HireFlow"
    return "HireFlow"


def _not_resolved(to: str) -> Dict[str, Any]:
    logger.error("[EMAIL] refusing to send to %s: no sender decision was passed. Resolve the "
                 "sender with resolve_sender / resolve_step_sender first.", to)
    return {"status": "blocked", "sent": False, "ok": False, "error_type": "blocked",
            "error": "sender not resolved", "message_id": None}


def dispatch(to: str, subject: str, body: str, reply_to: Optional[str] = None,
             smtp_config: Optional[Dict[str, Any]] = None,
             branding: Optional[Dict[str, Any]] = None,
             is_otp: bool = False, cc: Optional[list] = None,
             hourly_limit: Optional[int] = None, db=None,
             reference_type: Optional[str] = None,
             reference_id: Optional[str] = None, *,
             idempotency_key: Optional[str] = None,
             defer_inline: bool = False) -> Dict[str, Any]:
    """Accept one email for delivery through the email API.

    ``smtp_config`` must be the ``SenderRef`` a resolver returned; it fixes the
    company, the event and the sender. Anything else is refused — there is no
    default sender to fall back to.

    With ``db`` the message joins the caller's transaction and is published
    when that commits (the request's ``get_db`` commits at the end). Without it,
    a session is opened, committed and closed here.

    Returns ``{"mode": "outbox", "message_id", "status", "status_reason"}``; a
    ``sent`` key appears only once the outcome is final. ``hourly_limit`` is
    ignored: limits belong to the sender and its connection.
    """
    if not isinstance(smtp_config, SenderRef):
        return _not_resolved(to)
    ref = smtp_config
    decision = ref.decision
    if decision.status == "disabled":
        return {"status": "disabled", "mode": "skipped", "sent": False, "ok": False,
                "error": decision.reason, "error_type": "disabled", "message_id": None}

    from src.email_api import db as email_db
    from src.email_api.messages import submit, SubmitError
    own = db is None
    session = email_db.open_session() if own else db
    try:
        result = submit(
            session, tenant_id=decision.company_id, decision=decision,
            event_type=decision.event_type, to=[to], cc=cc or (), subject=subject, html=body,
            actor=current_actor(), idempotency_key=idempotency_key,
            reference_type=reference_type, reference_id=reference_id,
            step_key=ref.get("step_key"),
            priority=EC.PRIORITY_AUTH if is_otp else EC.PRIORITY_NORMAL,
            is_sensitive=is_otp, from_name=_display_name(ref, branding),
            reply_to=reply_to,
            unsubscribe_to=None if is_otp else ((branding or {}).get("support_email") or reply_to),
            defer_inline=defer_inline,
        )
        msg = result.message
        message_id = msg.id if msg is not None else None
        if own:
            session.commit()       # publishes (or, for auth mail without Redis, delivers)
            if message_id:
                # Re-read: delivery may already have happened in the commit hook
                # (auth mail without Redis). The message can belong to another
                # company than the bound one (platform support mail).
                from src.tenancy import tenant_bypass
                session.expire_all()
                with tenant_bypass():
                    msg = session.get(type(msg), message_id)
        status = msg.status if msg is not None else result.status
        out = {"mode": "outbox", "message_id": message_id, "status": status,
               "status_reason": getattr(msg, "status_reason", None)}
    except SubmitError as e:
        if own:
            session.rollback()
        logger.warning("[EMAIL] %s to %s rejected: %s", decision.event_type, to, e)
        return {"status": "failed", "sent": False, "ok": False, "error_type": "invalid",
                "error": str(e), "message_id": None}
    finally:
        if own:
            session.close()

    if status == EC.ACCEPTED:
        out["sent"] = True
    elif status in (EC.BLOCKED, EC.FAILED, EC.UNKNOWN, EC.CANCELLED):
        out.update(sent=False, error=out["status_reason"], error_type=status)
    logger.info("[EMAIL] %s -> %s from %s (%s, %s)", decision.event_type, to,
                decision.from_email or "-", status, out["status_reason"] or "-")
    if _log_bodies_allowed(is_otp):
        logger.debug(f"[EMAIL BODY]\n{body}\n{'=' * 60}")
    return out


def _send(to: str, subject: str, body: str, reply_to: Optional[str] = None,
          smtp_config: Optional[Dict[str, Any]] = None,
          branding: Optional[Dict[str, Any]] = None,
          is_otp: bool = False, cc: Optional[list] = None, db=None, **_ignored) -> Dict[str, Any]:
    """Deprecated alias of ``dispatch``.

    There is no synchronous SMTP path any more: everything goes through the
    outbox. Connection tests use ``src.email_api.connections.send_test``.
    """
    return dispatch(to, subject, body, reply_to=reply_to, smtp_config=smtp_config,
                    branding=branding, is_otp=is_otp, cc=cc, db=db)


def delivery_outcome(result: Any) -> Dict[str, Any]:
    """Classify what ``dispatch`` reported, for routes that tell the user.

    Delivery is not boolean. ``accepted`` means the customer's mail server took
    the message; ``queued``/``deferred``/``sending`` mean it is on its way;
    ``blocked`` means configuration stopped it (no sender, a broken mailbox) and
    nothing will be sent until someone fixes it; ``disabled`` means the email
    type is switched off.

    Returns:
        {"status": "sent" | "queued" | "failed" | "blocked" | "disabled",
         "error": str | None, "error_type": str | None, "ok": bool} —
        ``ok`` is sent-or-queued.
    """
    # Legacy senders that return None: absence of a result is not evidence of
    # failure, so don't manufacture one.
    if not isinstance(result, dict):
        return {"status": "sent", "error": None, "error_type": None, "ok": True}

    status = result.get("status")
    if result.get("sent") is True or status == EC.ACCEPTED:
        return {"status": "sent", "error": None, "error_type": None, "ok": True}
    if status == "disabled":
        return {"status": "disabled", "error": result.get("error"), "error_type": "disabled",
                "ok": False}
    if status == EC.BLOCKED:
        return {"status": "blocked", "error": result.get("error") or result.get("status_reason"),
                "error_type": "blocked", "ok": False}
    if "sent" in result or status in (EC.FAILED, EC.UNKNOWN, EC.CANCELLED):
        return {
            "status": "failed",
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "ok": False,
        }
    # queued / deferred / sending: accepted for delivery, outcome not known yet.
    return {"status": "queued", "error": None, "error_type": None, "ok": True}


# ==================== OTP / AUTH EMAILS ====================

def send_password_reset_otp(to: str, name: str, otp: str, branding: Optional[Dict[str, Any]] = None,
                            smtp_config: Optional[Dict[str, Any]] = None, db=None,
                            defer_inline: bool = False):
    body = f"""
        <p style="margin:0 0 16px">Hi {name},</p>
        <p style="margin:0 0 12px">Use this code to reset your password:</p>
        <p style="margin:0 0 16px;font-size:1.6rem;font-weight:600;letter-spacing:0.2em;font-family:Consolas,Menlo,monospace">{otp}</p>
        <p style="margin:0;color:#6b7280">The code expires in 10 minutes. If you did not request a reset, you can ignore this email.</p>"""
    return dispatch(to, "Your password reset code", _build_email("Password Reset", body, branding),
                    reply_to=(branding or {}).get("support_email"),
                    smtp_config=smtp_config, branding=branding, is_otp=True, db=db,
                    defer_inline=defer_inline)


def send_otp_email(to: str, name: str, otp: str, branding: Optional[Dict[str, Any]] = None,
                   smtp_config: Optional[Dict[str, Any]] = None, db=None,
                   defer_inline: bool = False):
    body = f"""
        <p style="margin:0 0 16px">Hi {name},</p>
        <p style="margin:0 0 12px">Use this code to verify your email address:</p>
        <p style="margin:0 0 16px;font-size:1.6rem;font-weight:600;letter-spacing:0.2em;font-family:Consolas,Menlo,monospace">{otp}</p>
        <p style="margin:0;color:#6b7280">The code expires in 10 minutes. If you did not create an account, you can ignore this email.</p>"""
    return dispatch(to, "Your verification code", _build_email("Email Verification", body, branding),
                    reply_to=(branding or {}).get("support_email"),
                    smtp_config=smtp_config, branding=branding, is_otp=True, db=db,
                    defer_inline=defer_inline)


def send_admin_welcome_email(to: str, name: str, company_name: str,
                              login_url: Optional[str] = None,
                              branding: Optional[Dict[str, Any]] = None,
                              smtp_config: Optional[Dict[str, Any]] = None, db=None):
    """Sent to the first admin user of a freshly-onboarded tenant.

    Note: the email intentionally does NOT contain the temporary password.
    SuperAdmin shares that out-of-band. The email's job is just to let the
    admin know the workspace exists and where to log in."""
    link_block = ""
    if login_url:
        link_block = f"""
        <p style="margin:0 0 16px">
            <a href="{login_url}" style="display:inline-block;padding:10px 20px;background:#2b7cba;color:#fff;text-decoration:none;border-radius:6px;font-weight:600">Open your workspace</a>
        </p>
        <p style="margin:0 0 12px;color:#6b7280;font-size:0.85rem">Or paste this into your browser: <span style="color:#374151">{login_url}</span></p>"""
    body = f"""
        <p style="margin:0 0 16px">Hi {name},</p>
        <p style="margin:0 0 12px">Your <b>{company_name}</b> workspace on HireFlow is ready and you've been set up as the admin.</p>
        {link_block}
        <p style="margin:0 0 12px;color:#374151">Sign in using your email <b>{to}</b> and the temporary password your account manager shared with you. Please change it from <i>Account → Settings</i> after your first login.</p>
        <p style="margin:0;color:#6b7280;font-size:0.85rem">If you weren't expecting this email, you can safely ignore it.</p>"""
    return dispatch(to, f"Your {company_name} workspace is ready",
                    _build_email(f"Welcome to {company_name}", body, branding),
                    reply_to=(branding or {}).get("support_email"),
                    smtp_config=smtp_config, branding=branding, db=db)


# ==================== STAGE EMAILS ====================

def send_application_received(candidate_email: str, candidate_name: str, job_title: str,
                               branding: Optional[Dict[str, Any]] = None,
                               smtp_config: Optional[Dict[str, Any]] = None,
                               cc: Optional[list] = None):
    company = (branding or {}).get("company_name") or "our team"
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">Thank you for applying for the <b>{job_title}</b> role. Your application has been received successfully, and the team at {company} is excited to learn more about you.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">Every application is reviewed individually by our talent team. We will carefully assess your experience against the role and get back to you within <b>5–7 business days</b> with the next steps.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0">We genuinely appreciate the time and effort you invested in applying, and we wish you the very best.</p>
        <p style="color:#374151;font-size:0.95rem;margin:20px 0 0">Warm regards,<br/>The {company} Talent Acquisition Team</p>"""
    return dispatch(candidate_email, f"Application Received – {job_title}",
                 _build_email("Application Received", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


# NOT CALLED ANYWHERE. Kept because the copy is written and the stage is real,
# but nothing sends it and "shortlisted" was removed from STEP_LABELS so the HR
# UI no longer offers a mailbox for it. To turn it on you need three things: a
# call site at the point candidates are shortlisted, a resolve_step_sender lookup
# there, and the "shortlisted" key back in STEP_LABELS.
def send_candidate_shortlisted(candidate_email: str, candidate_name: str, job_title: str,
                                ats_score: float, branding: Optional[Dict[str, Any]] = None,
                                smtp_config: Optional[Dict[str, Any]] = None,
                                cc: Optional[list] = None):
    company = (branding or {}).get("company_name") or "our team"
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">Congratulations — we have wonderful news! After reviewing a highly competitive pool of applicants, your profile stood out with a strong match score of <b>{ats_score:.1f}%</b>, and we are delighted to shortlist you for the <b>{job_title}</b> role.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">This is an exciting milestone, and it reflects the strength of your background and the value we believe you can bring to our team. Our HR team will be in touch shortly to walk you through the next steps.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0">We look forward to getting to know you better.</p>
        <p style="color:#374151;font-size:0.95rem;margin:20px 0 0">Warm regards,<br/>The {company} Talent Acquisition Team</p>"""
    return dispatch(candidate_email, f"You've Been Shortlisted – {job_title}",
                 _build_email("Shortlisted!", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def send_candidate_rejected(candidate_email: str, candidate_name: str, job_title: str,
                             branding: Optional[Dict[str, Any]] = None,
                             smtp_config: Optional[Dict[str, Any]] = None,
                             cc: Optional[list] = None):
    company = (branding or {}).get("company_name") or "our team"
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">Thank you for the time and genuine interest you showed in the <b>{job_title}</b> role, and for allowing us to consider your application.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">After careful and thoughtful review, we have decided to move forward with other candidates whose experience more closely aligns with the specific requirements of this position. This was not an easy decision — we were genuinely impressed by much of your background.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0">Please don't be discouraged. We warmly encourage you to apply for future opportunities with us that match your skills and aspirations, and we wish you great success in your career journey.</p>
        <p style="color:#374151;font-size:0.95rem;margin:20px 0 0">With appreciation,<br/>The {company} Talent Acquisition Team</p>"""
    return dispatch(candidate_email, f"Application Update – {job_title}",
                 _build_email("Application Update", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def notify_candidate_rejected(db, candidate) -> Dict[str, Any]:
    """Look up candidate's job title and dispatch a rejection email.

    Safe to call from any rejection workflow:
    - No-op if candidate has no email address.
    - Falls back to "the position" if the job lookup fails.
    - Sends from the company's routed sender; blocked (not sent) if it has none.
    - All exceptions are caught and logged; never raises.

    Returns a ``delivery_outcome`` dict so callers can tell what happened. It used
    to return None, which was worse than useless: ``delivery_outcome`` maps None
    to "sent" on purpose ("absence of a result is not evidence of failure"), so a
    caller that started reading the return would have been told every rejection
    succeeded, including the ones that never left the building.
    """
    try:
        if not candidate or not getattr(candidate, "email", None):
            return {"status": "skipped", "error": None, "error_type": None, "ok": False}
        # Local import avoids a circular dependency with src.models
        from src.models.models import JobPosting
        job = None
        job_title = "the position"
        job_id = getattr(candidate, "job_id", None)
        if job_id:
            job = db.query(JobPosting).filter(JobPosting.id == job_id).first()
            if job and job.title:
                job_title = job.title
        first = (getattr(candidate, "first_name", "") or "").strip()
        last = (getattr(candidate, "last_name", "") or "").strip()
        name = (first + " " + last).strip() or "Candidate"
        company_id = getattr(candidate, "company_id", None)
        from src.services.pipeline_notifications import resolve_step_dispatch
        disp = resolve_step_dispatch(db, company_id, "candidate_rejected", job=job)
        result = send_candidate_rejected(candidate.email, name, job_title,
                                         smtp_config=disp["smtp_config"], cc=disp["cc"])
        outcome = delivery_outcome(result)
        if not outcome["ok"]:
            logger.warning(
                f"rejection email to {candidate.email} not delivered: "
                f"{outcome['error_type']} — {outcome['error']}"
            )
        return outcome
    except Exception as e:
        logger.warning(
            f"notify_candidate_rejected failed for "
            f"{getattr(candidate, 'id', '?')}: {e}"
        )
        return {"status": "failed", "error": str(e), "error_type": "unknown", "ok": False}


def send_test_invitation(candidate_email: str, candidate_name: str, job_title: str,
                         test_link: str, deadline: str, branding: Optional[Dict[str, Any]] = None,
                         smtp_config: Optional[Dict[str, Any]] = None,
                         cc: Optional[list] = None):
    color = (branding or {}).get("color") or "#2b7cba"
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 20px">Congratulations! Your profile impressed our team, and we are pleased to invite you to the next stage for the <b>{job_title}</b> role — an online assessment designed to let your skills shine.</p>
        <table style="width:100%;border-collapse:collapse;margin:0 0 20px">
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600;width:40%">Position</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{job_title}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Complete By</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{deadline}</td></tr>
        </table>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 20px">This is your opportunity to show us what you can do. Take your time, trust your preparation, and give it your best.</p>
        <a href="{test_link}" style="display:inline-block;padding:12px 28px;background:{color};color:#fff;border-radius:8px;text-decoration:none;font-weight:700;font-size:0.95rem">Start Assessment &rarr;</a>
        <p style="color:#9ca3af;font-size:0.82rem;margin:20px 0 0">For the best experience, choose a quiet environment with a stable internet connection before you begin. Best of luck — we're rooting for you!</p>"""
    return dispatch(candidate_email, f"Assessment Invitation – {job_title}",
                 _build_email("Assessment Invitation", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def send_interview_scheduled(candidate_email: str, candidate_name: str, job_title: str,
                              scheduled_at: str, meeting_link: str, interviewer_name: str,
                              branding: Optional[Dict[str, Any]] = None,
                              smtp_config: Optional[Dict[str, Any]] = None,
                              cc: Optional[list] = None):
    color = (branding or {}).get("color") or "#2b7cba"
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 20px">Great news — we enjoyed reviewing your application and would love to meet you. Your interview for the <b>{job_title}</b> role has been confirmed.</p>
        <table style="width:100%;border-collapse:collapse;margin:0 0 20px">
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600;width:40%">Date &amp; Time</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{scheduled_at}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Interviewer</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{interviewer_name}</td></tr>
        </table>
        <a href="{meeting_link}" style="display:inline-block;padding:12px 28px;background:{color};color:#fff;border-radius:8px;text-decoration:none;font-weight:700;font-size:0.95rem">Join Meeting &rarr;</a>
        <p style="color:#9ca3af;font-size:0.82rem;margin:20px 0 0">Come as you are and be ready to share your story — we're looking forward to a great conversation. Please join 5 minutes early to settle in.</p>"""
    return dispatch(candidate_email, f"Interview Scheduled – {job_title}",
                 _build_email("Interview Scheduled", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def send_interview_reminder(candidate_email: str, candidate_name: str, job_title: str,
                             scheduled_at: str, meeting_link: str,
                             branding: Optional[Dict[str, Any]] = None,
                             smtp_config: Optional[Dict[str, Any]] = None):
    color = (branding or {}).get("color") or "#2b7cba"
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 20px">A friendly reminder that your interview for the <b>{job_title}</b> role is coming up on <b>{scheduled_at}</b>. We're genuinely looking forward to speaking with you!</p>
        <a href="{meeting_link}" style="display:inline-block;padding:12px 28px;background:{color};color:#fff;border-radius:8px;text-decoration:none;font-weight:700;font-size:0.95rem">Join Meeting &rarr;</a>
        <p style="color:#9ca3af;font-size:0.82rem;margin:20px 0 0">Find a quiet space and join a few minutes early so you can start relaxed. You've got this.</p>"""
    return dispatch(candidate_email, f"Interview Reminder – {job_title} (Tomorrow)",
                 _build_email("Interview Reminder", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding)


def send_panel_invite(interviewer_email: str, interviewer_name: str, candidate_name: str,
                      job_title: str, scheduled_at: str, meeting_details: str,
                      fellow_panelists: Optional[list] = None,
                      branding: Optional[Dict[str, Any]] = None,
                      smtp_config: Optional[Dict[str, Any]] = None,
                      cc: Optional[list] = None):
    """Invite an interviewer to a panel interview. Sent once per panel member."""
    color = (branding or {}).get("color") or "#2b7cba"
    is_link = isinstance(meeting_details, str) and meeting_details.startswith("http")
    panel_row = ""
    if fellow_panelists:
        panel_row = (
            '<tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600;width:40%">Fellow Panelists</td>'
            f'<td style="padding:10px 14px;border:1px solid #e5e7eb">{", ".join(fellow_panelists)}</td></tr>'
        )
    where_row = (
        f'<tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Location</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{meeting_details}</td></tr>'
        if not is_link else ""
    )
    cta = (
        f'<a href="{meeting_details}" style="display:inline-block;padding:12px 28px;background:{color};color:#fff;border-radius:8px;text-decoration:none;font-weight:700;font-size:0.95rem">Join Meeting &rarr;</a>'
        if is_link else ""
    )
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Hi <b>{interviewer_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 20px">You've been invited to join the interview panel for <b>{candidate_name}</b>, who is interviewing for the <b>{job_title}</b> role. We'd value your perspective in this conversation.</p>
        <table style="width:100%;border-collapse:collapse;margin:0 0 20px">
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600;width:40%">Candidate</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{candidate_name}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Role</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{job_title}</td></tr>
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Date &amp; Time</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{scheduled_at}</td></tr>
          {where_row}
          {panel_row}
        </table>
        {cta}
        <p style="color:#9ca3af;font-size:0.82rem;margin:20px 0 0">Please review the candidate's profile beforehand and join a few minutes early. You'll be able to submit your feedback after the interview.</p>"""
    return dispatch(interviewer_email, f"Panel Interview – {candidate_name} for {job_title}",
                 _build_email("Panel Interview Invitation", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def send_offer_letter(candidate_email: str, candidate_name: str, job_title: str,
                      salary: float, bonus: float, start_date: str,
                      expiry_date: str, offer_type: str, terms: str,
                      branding: Optional[Dict[str, Any]] = None,
                      smtp_config: Optional[Dict[str, Any]] = None,
                      cc: Optional[list] = None):
    color = (branding or {}).get("color") or "#2b7cba"
    company = (branding or {}).get("company_name") or "our team"
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 20px">It is our great pleasure to offer you the position of <b>{job_title}</b>. Having gotten to know you through this process, we are confident you will be a tremendous addition to our team — and we can't wait to see the impact you'll make.</p>
        <table style="width:100%;border-collapse:collapse;margin:0 0 20px">
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600;width:40%">Position</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{job_title}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Employment Type</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{offer_type}</td></tr>
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Annual Salary</td><td style="padding:10px 14px;border:1px solid #e5e7eb">&#8377;{salary:,.2f}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Signing Bonus</td><td style="padding:10px 14px;border:1px solid #e5e7eb">&#8377;{bonus:,.2f}</td></tr>
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Start Date</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{start_date}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Offer Expires</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{expiry_date}</td></tr>
        </table>
        {f'<p style="color:#374151;font-size:0.88rem;margin:0 0 20px"><b>Terms:</b> {terms}</p>' if terms else ''}
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">To accept, please respond through your candidate portal before the offer expiry date. If you have any questions in the meantime, we would be glad to help.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0">Welcome aboard — congratulations on this well-deserved achievement!</p>
        <p style="color:#374151;font-size:0.95rem;margin:20px 0 0">Warm regards,<br/>The {company} Talent Acquisition Team</p>"""
    return dispatch(candidate_email, f"Offer Letter – {job_title}",
                 _build_email("Offer Letter", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def send_offer_accepted(hr_email: str, candidate_name: str, job_title: str, start_date: str,
                        branding: Optional[Dict[str, Any]] = None,
                        smtp_config: Optional[Dict[str, Any]] = None,
                        cc: Optional[list] = None):
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px"><b>{candidate_name}</b> has accepted the offer for <b>{job_title}</b>.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0">Expected Start Date: <b>{start_date}</b><br/>Please initiate the onboarding process.</p>"""
    return dispatch(hr_email, f"Offer Accepted – {candidate_name} for {job_title}",
                 _build_email("Offer Accepted", body, branding),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def send_offer_rejected(hr_email: str, candidate_name: str, job_title: str,
                        branding: Optional[Dict[str, Any]] = None,
                        smtp_config: Optional[Dict[str, Any]] = None,
                        cc: Optional[list] = None):
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px"><b>{candidate_name}</b> has declined the offer for <b>{job_title}</b>.</p>
        <p style="color:#374151;font-size:0.95rem;margin:0">You may consider the next candidate in the pipeline.</p>"""
    return dispatch(hr_email, f"Offer Declined – {candidate_name} for {job_title}",
                 _build_email("Offer Declined", body, branding),
                 smtp_config=smtp_config, branding=branding, cc=cc)


def send_mcq_result(candidate_email: str, candidate_name: str, job_title: str,
                    score: float, total: int, passed: bool,
                    branding: Optional[Dict[str, Any]] = None,
                    smtp_config: Optional[Dict[str, Any]] = None,
                    cc: Optional[list] = None):
    result_label = "PASSED" if passed else "NOT PASSED"
    result_color = "#16a34a" if passed else "#dc2626"
    pct = score / total * 100 if total else 0
    closing = (
        '<p style="color:#374151;font-size:0.95rem;margin:0">Congratulations on a strong performance! Our team will be in touch with the next steps very shortly. We are excited about your progress.</p>'
        if passed else
        '<p style="color:#374151;font-size:0.95rem;margin:0">While this outcome wasn\'t what you were hoping for, we sincerely appreciate the effort you put in and the interest you showed in us. We warmly encourage you to apply for future roles that match your strengths.</p>'
    )
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Dear <b>{candidate_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 20px">Thank you for completing the assessment for the <b>{job_title}</b> role. Here is a summary of your results:</p>
        <table style="width:100%;border-collapse:collapse;margin:0 0 20px">
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600;width:40%">Score</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{score:.1f} / {total}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Percentage</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{pct:.1f}%</td></tr>
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Result</td><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:700;color:{result_color}">{result_label}</td></tr>
        </table>
        {closing}"""
    return dispatch(candidate_email, f"Assessment Result – {job_title}",
                 _build_email("Assessment Result", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, cc=cc)


# ==================== SUPPORT TICKET EMAILS ====================

PRIORITY_COLORS = {
    "low":    "#64748b",
    "normal": "#2b7cba",
    "high":   "#f59e0b",
    "urgent": "#dc2626",
}


def send_support_new_to_superadmin(to: str, company_name: str, requester_name: str,
                                    subject: str, priority: str, body_text: str,
                                    ticket_url: str,
                                    smtp_config: Optional[Dict[str, Any]] = None, db=None):
    """Notify the platform superadmin that a client admin opened a new ticket."""
    color = PRIORITY_COLORS.get((priority or "normal").lower(), "#2b7cba")
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px"><b>{requester_name}</b> from <b>{company_name}</b> opened a new support ticket.</p>
        <table style="width:100%;border-collapse:collapse;margin:0 0 20px">
          <tr style="background:#f8fafc"><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600;width:32%">Subject</td><td style="padding:10px 14px;border:1px solid #e5e7eb">{subject}</td></tr>
          <tr><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:600">Priority</td><td style="padding:10px 14px;border:1px solid #e5e7eb;font-weight:700;color:{color}">{(priority or 'normal').upper()}</td></tr>
        </table>
        <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin:0 0 20px;white-space:pre-wrap;color:#374151;font-size:0.92rem">{body_text}</div>
        <p style="margin:0"><a href="{ticket_url}" style="background:#1a6b5a;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9rem">Open ticket</a></p>"""
    return dispatch(to, f"[Support] {subject}",
                    _build_email("New Support Ticket", body), smtp_config=smtp_config, db=db)


def send_support_reply_to_admin(to: str, requester_name: str, subject: str,
                                 body_text: str, ticket_url: str,
                                 branding: Optional[Dict[str, Any]] = None,
                                 smtp_config: Optional[Dict[str, Any]] = None, db=None):
    """Notify a client admin that the platform team replied on their ticket."""
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Hi <b>{requester_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">The platform team replied on your ticket <b>{subject}</b>:</p>
        <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin:0 0 20px;white-space:pre-wrap;color:#374151;font-size:0.92rem">{body_text}</div>
        <p style="margin:0"><a href="{ticket_url}" style="background:#1a6b5a;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9rem">View ticket</a></p>"""
    return dispatch(to, f"[Support] Re: {subject}",
                    _build_email("Support Reply", body, branding),
                    reply_to=(branding or {}).get("support_email"),
                    smtp_config=smtp_config, branding=branding, db=db)


def send_support_reply_to_superadmin(to: str, company_name: str, requester_name: str,
                                      subject: str, body_text: str, ticket_url: str,
                                      smtp_config: Optional[Dict[str, Any]] = None, db=None):
    """Notify superadmin when an admin replies on their open ticket."""
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px"><b>{requester_name}</b> from <b>{company_name}</b> replied on a ticket.</p>
        <p style="color:#6b7280;font-size:0.88rem;margin:0 0 12px">Subject: <b>{subject}</b></p>
        <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin:0 0 20px;white-space:pre-wrap;color:#374151;font-size:0.92rem">{body_text}</div>
        <p style="margin:0"><a href="{ticket_url}" style="background:#1a6b5a;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9rem">Open ticket</a></p>"""
    return dispatch(to, f"[Support] Re: {subject}",
                    _build_email("Support Reply", body), smtp_config=smtp_config, db=db)
