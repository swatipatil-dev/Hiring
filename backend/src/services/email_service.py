"""
📧 EMAIL NOTIFICATION SERVICE
Sends automated emails at each hiring workflow stage.
Uses SMTP (configurable). Falls back to console logging if not configured.
"""

import re
import smtplib
import socket
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import formataddr, formatdate, make_msgid
from typing import Optional, Dict, Any
import os

logger = logging.getLogger(__name__)

SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")
SMTP_PASS = os.getenv("SMTP_PASS", "")
FROM_EMAIL = os.getenv("FROM_EMAIL", "")
EMAIL_ENABLED = os.getenv("EMAIL_ENABLED", "true").lower() == "true"
# Implicit SSL (SMTPS). Auto-detected for port 465; force with SMTP_USE_SSL=true.
# Note: Cloud Run blocks outbound port 25 — use 587 (STARTTLS) or 465 (SSL) to a relay.
SMTP_USE_SSL = os.getenv("SMTP_USE_SSL", "").lower() in ("1", "true", "yes")
MSGID_DOMAIN = os.getenv("EMAIL_MSGID_DOMAIN", "hireflow.io")

# ── Innomax (platform) per-category sender mailboxes ──
# For the platform's OWN company only, route mail by category to dedicated domain
# mailboxes: candidate-handling → hr@, client-company-handling → official@.
# support/system mail keeps the default SMTP_* (Gmail). Client companies are
# unaffected (they keep resolve_smtp_for_company). Passwords come from env/Secret Mgr.
INNOMAX_SMTP_HOST = os.getenv("INNOMAX_SMTP_HOST", "mail.innomaxsol.com")
INNOMAX_SMTP_PORT = int(os.getenv("INNOMAX_SMTP_PORT", "465"))
INNOMAX_HR_USER = os.getenv("INNOMAX_HR_USER", "")
INNOMAX_HR_PASS = os.getenv("INNOMAX_HR_PASS", "")
INNOMAX_OFFICIAL_USER = os.getenv("INNOMAX_OFFICIAL_USER", "")
INNOMAX_OFFICIAL_PASS = os.getenv("INNOMAX_OFFICIAL_PASS", "")


_TAG_RE   = re.compile(r"<[^>]+>")
_WS_RE    = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


def _html_to_text(html: str) -> str:
    """Cheap HTML→text fallback used as the multipart/alternative text part.

    Spam scorers add weight to HTML-only mail; even a rough text alt removes
    that penalty. We don't pull in BeautifulSoup just for this.
    """
    if not html:
        return ""
    text = html.replace("<br/>", "\n").replace("<br>", "\n")
    text = text.replace("</p>", "\n\n").replace("</tr>", "\n")
    text = _TAG_RE.sub("", text)
    # Decode the two HTML entities our templates actually use.
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&rarr;", "→").replace("&#8377;", "₹")
    text = _WS_RE.sub(" ", text)
    text = _BLANK_RE.sub("\n\n", text)
    return text.strip()


def resolve_smtp_for_company(db, company_id: Optional[str]) -> Optional[Dict[str, Any]]:
    """Look up per-company SMTP override from tenant_configs.

    Returns a dict suitable for passing as `smtp_config` to _send() / send_*,
    or None if the company has not configured custom SMTP (caller falls back
    to env-var defaults).
    """
    if not company_id or db is None:
        return None
    try:
        from src.models.models import TenantConfig
        from src.security import decrypt
        tc = db.query(TenantConfig).filter(TenantConfig.company_id == company_id).first()
        if not tc or not tc.smtp_user or not tc.smtp_pass:
            return None
        password = decrypt(tc.smtp_pass) or tc.smtp_pass  # tolerate legacy plain values
        return {
            "host": tc.smtp_host or "smtp.gmail.com",
            "port": int(tc.smtp_port or 587),
            "user": tc.smtp_user,
            "password": password,
            "from_email": tc.smtp_from_email or tc.smtp_user,
        }
    except Exception as e:
        logger.warning(f"resolve_smtp_for_company failed for {company_id}: {e}")
        return None


def _innomax_mailbox(user: str, password: str) -> Optional[Dict[str, Any]]:
    """Build an smtp_config for an Innomax domain mailbox, or None if not configured."""
    if not user or not password:
        return None
    return {
        "host": INNOMAX_SMTP_HOST,
        "port": INNOMAX_SMTP_PORT,
        "user": user,
        "password": password,
        "from_email": user,
    }


def _is_platform_company(db, company_id: Optional[str]) -> bool:
    """True if company_id is the platform's own (Innomax) company."""
    if not company_id or db is None:
        return False
    try:
        from src.tenancy.platform_company import get_platform_company
        platform = get_platform_company(db)
        return bool(platform and platform.id == company_id)
    except Exception as e:
        logger.warning(f"_is_platform_company check failed: {e}")
        return False


def resolve_sender(db, company_id: Optional[str], category: str = "system") -> Optional[Dict[str, Any]]:
    """Resolve the SMTP sender for an email, routing by category for the Innomax tenant.

    category: "candidate" (→ hr@innomaxsol.com), "client" (→ official@innomaxsol.com),
    or "system" (→ default SMTP_* env, i.e. the Innomax Gmail). Client companies are
    unaffected — they always resolve via resolve_smtp_for_company. Returns None to mean
    "use the default SMTP_* env fallback".
    """
    if _is_platform_company(db, company_id):
        if category == "candidate":
            return _innomax_mailbox(INNOMAX_HR_USER, INNOMAX_HR_PASS) \
                or resolve_smtp_for_company(db, company_id)
        if category == "client":
            return _innomax_mailbox(INNOMAX_OFFICIAL_USER, INNOMAX_OFFICIAL_PASS) \
                or resolve_smtp_for_company(db, company_id)
        # system/support → default env (Gmail)
        return None
    # Any other (client) company: unchanged behaviour.
    return resolve_smtp_for_company(db, company_id)


# Named mailbox used for OTP / email-verification / password-reset ("system auth")
# mail, matching the Admin-created mailbox of the same name. Override via env.
AUTH_MAILBOX_NAME = os.getenv("AUTH_MAILBOX_NAME", "Auth")


def resolve_company_mailbox(db, company_id: Optional[str], name: str) -> Optional[Dict[str, Any]]:
    """Return an smtp_config for a named, active CompanyMailbox, or None.

    None when the company/name is missing, the mailbox is absent/inactive, or it
    has no credentials — the caller then falls back to its previous sender
    resolution (so this is always safe to prepend).
    """
    if not company_id or db is None or not name:
        return None
    try:
        from src.models.models import CompanyMailbox
        mb = (
            db.query(CompanyMailbox)
            .filter(CompanyMailbox.company_id == company_id,
                    CompanyMailbox.name == name,
                    CompanyMailbox.is_active == True)  # noqa: E712
            .first()
        )
        if not mb or not mb.smtp_user or not mb.smtp_pass:
            return None
        from src.security import decrypt
        password = decrypt(mb.smtp_pass) or mb.smtp_pass  # tolerate legacy plain
        return {
            "host": mb.smtp_host or "smtp.gmail.com",
            "port": int(mb.smtp_port or 587),
            "user": mb.smtp_user,
            "password": password,
            "from_email": mb.smtp_from_email or mb.smtp_user,
        }
    except Exception as e:
        logger.warning(f"resolve_company_mailbox({name!r}) failed for {company_id}: {e}")
        return None


def _public_sender(cfg: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Strip an smtp_config down to the non-secret fields safe to return over the API.

    Deliberately allow-lists keys rather than popping ``password`` — a future key
    added to smtp_config dicts must not silently start leaking.
    """
    if not cfg:
        return None
    return {
        "host":       cfg.get("host") or SMTP_HOST,
        "port":       int(cfg.get("port") or SMTP_PORT),
        "user":       cfg.get("user") or "",
        # Mirror _send's derivation exactly (see :241) so this shows the real
        # From address rather than a plausible-looking guess.
        "from_email": cfg.get("from_email") or FROM_EMAIL or cfg.get("user") or "",
    }


def describe_sender_chain(db, company_id: Optional[str],
                          category: str = "candidate") -> list:
    """Describe the sender-resolution chain for the Admin routing view.

    Returns one entry per layer, in resolution order, shaped as::

        {"layer", "label", "applies_to", "configured", "active",
         "sender": {host, port, user, from_email}|None, "detail"}

    ``active`` marks the layer that actually wins for ``category`` among the
    company-wide layers. The per-step and auth layers are reported as
    ``applies_to``-scoped because they only apply to some mail, so marking them
    "active" for a generic send would be misleading.

    Never returns a password. The winning entry is derived by calling the real
    ``resolve_sender`` and passing it through ``_public_sender``, so this view
    cannot drift from what the send path does.
    """
    is_platform = _is_platform_company(db, company_id)
    company_cfg = resolve_smtp_for_company(db, company_id)
    auth_cfg    = resolve_company_mailbox(db, company_id, AUTH_MAILBOX_NAME)
    winner      = resolve_sender(db, company_id, category)

    # Which company-wide layer produced `winner`? None means "fell through to env".
    if winner is None:
        active_layer = "env_default"
    elif is_platform and category in ("candidate", "client") and (
        winner.get("user") in (INNOMAX_HR_USER, INNOMAX_OFFICIAL_USER)
        and winner.get("user")
    ):
        active_layer = "platform_category"
    else:
        active_layer = "company_override"

    # How many pipeline steps point at a mailbox that is genuinely usable.
    step_total, step_usable = 0, 0
    try:
        from src.models.models import PipelineStepConfig, CompanyMailbox
        from src.services.pipeline_notifications import mailbox_unusable_reason
        rows = (db.query(PipelineStepConfig)
                  .filter(PipelineStepConfig.company_id == company_id,
                          PipelineStepConfig.mailbox_id.isnot(None))
                  .all()) if company_id and db is not None else []
        step_total = len(rows)
        for r in rows:
            mb = db.query(CompanyMailbox).filter(CompanyMailbox.id == r.mailbox_id).first()
            if mailbox_unusable_reason(mb) is None:
                step_usable += 1
    except Exception as e:
        logger.warning(f"describe_sender_chain step scan failed for {company_id}: {e}")

    innomax_user = (INNOMAX_HR_USER if category == "candidate"
                    else INNOMAX_OFFICIAL_USER if category == "client" else "")

    return [
        {
            "layer": "step_mailbox",
            "label": "Per-step mailbox",
            "applies_to": "Pipeline step mail",
            "configured": step_total > 0,
            "active": False,
            "sender": None,
            "detail": (
                f"{step_usable} of {step_total} configured step(s) resolve to a usable mailbox"
                if step_total else "No pipeline step has a mailbox assigned"
            ),
        },
        {
            "layer": "auth_mailbox",
            "label": f"Named “{AUTH_MAILBOX_NAME}” mailbox",
            "applies_to": "OTP & password reset",
            "configured": auth_cfg is not None,
            "active": False,
            "sender": _public_sender(auth_cfg),
            "detail": (
                "Verification and reset codes send from this mailbox"
                if auth_cfg else
                f"No active mailbox named “{AUTH_MAILBOX_NAME}” — auth mail uses the layers below"
            ),
        },
        {
            "layer": "platform_category",
            "label": "Platform category routing",
            "applies_to": "Platform company only",
            "configured": bool(is_platform and innomax_user),
            "active": active_layer == "platform_category",
            "sender": _public_sender(
                {"host": INNOMAX_SMTP_HOST, "port": INNOMAX_SMTP_PORT,
                 "user": innomax_user, "from_email": innomax_user}
            ) if (is_platform and innomax_user) else None,
            "detail": (
                "Not applicable — this is a client company"
                if not is_platform else
                f"{category} mail routes to the dedicated Innomax mailbox"
                if innomax_user else
                "Platform company, but no Innomax mailbox is set for this category"
            ),
        },
        {
            "layer": "company_override",
            "label": "Company SMTP",
            "applies_to": "All company mail",
            "configured": company_cfg is not None,
            "active": active_layer == "company_override",
            "sender": _public_sender(company_cfg),
            "detail": (
                "Saved under Email Settings above"
                if company_cfg else
                "Not configured — mail falls through to the platform default"
            ),
        },
        {
            "layer": "env_default",
            "label": "Platform default (environment)",
            "applies_to": "Fallback for all mail",
            "configured": bool(SMTP_USER),
            "active": active_layer == "env_default",
            "sender": _public_sender({"user": SMTP_USER}) if SMTP_USER else None,
            "detail": (
                "Set by SMTP_* environment variables"
                if SMTP_USER else
                "SMTP_USER is not set — with no other layer configured, mail cannot send"
            ),
        },
    ]


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


def _send(to: str, subject: str, body: str, reply_to: Optional[str] = None,
          smtp_config: Optional[Dict[str, Any]] = None,
          branding: Optional[Dict[str, Any]] = None,
          is_otp: bool = False,
          cc: Optional[list] = None) -> Dict[str, Any]:
    """Send email and return delivery result.

    If ``smtp_config`` is provided (host/port/user/password/from_email keys),
    those credentials are used. Otherwise the global env-var defaults apply.

    ``branding`` and ``is_otp`` shape headers to reduce spam-folder placement:
      * A branded display name is composed for ``From`` when ``branding`` is set.
      * ``Message-ID`` and ``Date`` are always emitted explicitly.
      * A ``text/plain`` alternative is attached alongside the HTML body.
      * Transactional OTP mail gets ``Auto-Submitted: auto-generated`` and
        ``Precedence: bulk``; non-OTP mail with a known support address gets a
        ``List-Unsubscribe`` mailto pointer (RFC 8058).

    Returns:
        {"sent": bool, "error": str | None, "error_type": str | None}
        error_type is one of: None, "disabled", "auth", "smtp", "network", "unknown"
    """
    cfg = smtp_config or {}
    host = cfg.get("host") or SMTP_HOST
    port = int(cfg.get("port") or SMTP_PORT)
    user = cfg.get("user") or SMTP_USER
    password = cfg.get("password") or SMTP_PASS
    from_email = cfg.get("from_email") or FROM_EMAIL or user

    # Build a branded display name so recipients see "HEALTOURE via HireFlow"
    # rather than a bare gmail address.
    company_name = (branding or {}).get("company_name")
    if company_name and company_name.lower() != "hireflow":
        from_header = formataddr((f"{company_name} via HireFlow", from_email))
    else:
        from_header = formataddr(("HireFlow", from_email))

    support_email = (branding or {}).get("support_email") or reply_to

    # Normalise CC: drop blanks/the primary recipient, de-dupe (case-insensitive),
    # preserve order. Recipients beyond ``to`` get the same copy of the message.
    cc_list = []
    if cc:
        _seen = {(to or "").strip().lower()}
        for addr in cc:
            a = (addr or "").strip()
            if a and a.lower() not in _seen:
                _seen.add(a.lower())
                cc_list.append(a)

    logger.info(f"[EMAIL] To: {to} | Cc: {', '.join(cc_list) or '-'} | From: {from_header} | Subject: {subject}")
    logger.info(f"[EMAIL BODY]\n{body}\n{'='*60}")
    if not EMAIL_ENABLED or not user:
        return {"sent": False, "error": "EMAIL_ENABLED is false or SMTP user not set", "error_type": "disabled"}
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = from_header
        msg["To"] = to
        if cc_list:
            msg["Cc"] = ", ".join(cc_list)
        msg["Date"] = formatdate(localtime=True)
        msg["Message-ID"] = make_msgid(domain=MSGID_DOMAIN)
        msg["X-Mailer"] = "HireFlow Notifications"
        if reply_to:
            msg["Reply-To"] = reply_to
        if is_otp:
            # Tells receivers this is automated transactional mail, not a
            # broadcast — reduces spam weight without harming inbox placement.
            msg["Auto-Submitted"] = "auto-generated"
            msg["Precedence"] = "bulk"
            msg["X-Auto-Response-Suppress"] = "All"
        elif support_email:
            # Non-OTP mail (offers, reminders, etc.) should expose an unsubscribe
            # pointer; mailto works without a separate web endpoint.
            msg["List-Unsubscribe"] = f"<mailto:{support_email}?subject=unsubscribe>"
            msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

        # text/plain MUST be attached before text/html in multipart/alternative
        # so older clients pick the plain version and spam filters see balance.
        msg.attach(MIMEText(_html_to_text(body), "plain", "utf-8"))
        msg.attach(MIMEText(body, "html", "utf-8"))

        # Port 465 = implicit SSL (SMTPS); 587/25 = plain + STARTTLS upgrade.
        use_ssl = bool(cfg.get("use_ssl")) or SMTP_USE_SSL or port == 465
        recipients = [to] + cc_list
        if use_ssl:
            with smtplib.SMTP_SSL(host, port, timeout=15) as server:
                server.login(user, password)
                server.sendmail(from_email, recipients, msg.as_string())
        else:
            with smtplib.SMTP(host, port, timeout=15) as server:
                server.ehlo()
                server.starttls()
                server.ehlo()
                server.login(user, password)
                server.sendmail(from_email, recipients, msg.as_string())
        return {"sent": True, "error": None, "error_type": None}
    except smtplib.SMTPAuthenticationError as e:
        logger.error(f"SMTP authentication failed for {user}: {e}")
        return {"sent": False, "error": f"{e.smtp_code} {e.smtp_error!r}", "error_type": "auth"}
    except smtplib.SMTPException as e:
        logger.error(f"SMTP error sending to {to}: {e}")
        return {"sent": False, "error": str(e), "error_type": "smtp"}
    except (socket.gaierror, socket.timeout, TimeoutError, ConnectionError, OSError) as e:
        logger.error(f"Network error sending to {to}: {e}")
        return {"sent": False, "error": str(e), "error_type": "network"}
    except Exception as e:
        logger.error(f"Unexpected email send failure to {to}: {e}")
        return {"sent": False, "error": str(e), "error_type": "unknown"}


def dispatch(to: str, subject: str, body: str, reply_to: Optional[str] = None,
             smtp_config: Optional[Dict[str, Any]] = None,
             branding: Optional[Dict[str, Any]] = None,
             is_otp: bool = False, cc: Optional[list] = None,
             hourly_limit: Optional[int] = None) -> Dict[str, Any]:
    """Queue an email for rate-limited async delivery, or send inline.

    Used by the fire-and-forget stage notifications so a provider's per-hour cap
    defers (not drops) mail. Falls back to a synchronous ``_send`` when the queue
    module/Redis is unavailable or ``EMAIL_SYNC`` is set — i.e. dev is unchanged.
    The sender password is Fernet-encrypted in the queued payload so no plaintext
    credential sits in Redis.
    """
    try:
        from src.mailq.queue import enqueue_email
    except Exception:
        return _send(to, subject, body, reply_to=reply_to, smtp_config=smtp_config,
                     branding=branding, is_otp=is_otp, cc=cc)

    sender = None
    if smtp_config:
        pw = smtp_config.get("password") or ""
        try:
            from src.security import encrypt
            pw_enc = encrypt(pw) if pw else ""
        except Exception:
            pw_enc = ""
        sender = {
            "host": smtp_config.get("host"),
            "port": smtp_config.get("port"),
            "user": smtp_config.get("user"),
            "from_email": smtp_config.get("from_email"),
            "password_enc": pw_enc,
        }
    payload = {
        "to": to, "subject": subject, "body": body, "reply_to": reply_to,
        "branding": branding, "is_otp": is_otp, "cc": cc, "sender": sender,
        "hourly_limit": hourly_limit if hourly_limit is not None
        else (smtp_config or {}).get("hourly_limit"),
    }
    try:
        return enqueue_email(payload)
    except Exception as e:
        logger.warning(f"email enqueue failed ({e}); sending inline")
        return _send(to, subject, body, reply_to=reply_to, smtp_config=smtp_config,
                     branding=branding, is_otp=is_otp, cc=cc)


def delivery_outcome(result: Any) -> Dict[str, Any]:
    """Classify what ``_send`` / ``dispatch`` / ``enqueue_email`` reported.

    Delivery is three-state, not boolean: mail can be sent inline, accepted onto
    the queue for a worker to send later, or fail outright. These paths return
    different shapes — ``_send`` gives ``{"sent": bool, ...}`` while a queued
    ``dispatch`` gives ``{"mode": "async", "job_id": ...}`` with no ``sent`` key
    at all. Reading ``.get("sent")`` directly would therefore mis-report every
    healthy async send as a failure.

    Returns:
        {"status": "sent" | "queued" | "failed", "error": str | None,
         "error_type": str | None, "ok": bool} — ``ok`` is sent-or-queued.
    """
    # Legacy senders that return None: absence of a result is not evidence of
    # failure, so don't manufacture one.
    if not isinstance(result, dict):
        return {"status": "sent", "error": None, "error_type": None, "ok": True}

    if result.get("sent") is True:
        return {"status": "sent", "error": None, "error_type": None, "ok": True}

    if "sent" in result:  # present and falsy — an actual, reported failure
        return {
            "status": "failed",
            "error": result.get("error"),
            "error_type": result.get("error_type"),
            "ok": False,
        }

    # Queued: "async" from RQ, or the worker deferring/retrying. Outcome is not
    # known yet, but the message was accepted for delivery.
    return {"status": "queued", "error": None, "error_type": None, "ok": True}


# ==================== OTP / AUTH EMAILS ====================

def send_password_reset_otp(to: str, name: str, otp: str, branding: Optional[Dict[str, Any]] = None,
                            smtp_config: Optional[Dict[str, Any]] = None):
    body = f"""
        <p style="margin:0 0 16px">Hi {name},</p>
        <p style="margin:0 0 12px">Use this code to reset your password:</p>
        <p style="margin:0 0 16px;font-size:1.6rem;font-weight:600;letter-spacing:0.2em;font-family:Consolas,Menlo,monospace">{otp}</p>
        <p style="margin:0;color:#6b7280">The code expires in 10 minutes. If you did not request a reset, you can ignore this email.</p>"""
    return _send(to, "Your password reset code", _build_email("Password Reset", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, is_otp=True)


def send_otp_email(to: str, name: str, otp: str, branding: Optional[Dict[str, Any]] = None,
                   smtp_config: Optional[Dict[str, Any]] = None):
    body = f"""
        <p style="margin:0 0 16px">Hi {name},</p>
        <p style="margin:0 0 12px">Use this code to verify your email address:</p>
        <p style="margin:0 0 16px;font-size:1.6rem;font-weight:600;letter-spacing:0.2em;font-family:Consolas,Menlo,monospace">{otp}</p>
        <p style="margin:0;color:#6b7280">The code expires in 10 minutes. If you did not create an account, you can ignore this email.</p>"""
    return _send(to, "Your verification code", _build_email("Email Verification", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding, is_otp=True)


def send_admin_welcome_email(to: str, name: str, company_name: str,
                              login_url: Optional[str] = None,
                              branding: Optional[Dict[str, Any]] = None,
                              smtp_config: Optional[Dict[str, Any]] = None):
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
    return _send(to, f"Your {company_name} workspace is ready",
                 _build_email(f"Welcome to {company_name}", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding)


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


def notify_candidate_rejected(db, candidate) -> None:
    """Look up candidate's job title and dispatch a rejection email.

    Safe to call from any rejection workflow:
    - No-op if candidate has no email address.
    - Falls back to "the position" if the job lookup fails.
    - Uses candidate's company SMTP if configured; otherwise global defaults.
    - All exceptions are caught and logged; never raises.
    """
    try:
        if not candidate or not getattr(candidate, "email", None):
            return
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
        smtp_config = disp["smtp_config"] or resolve_sender(db, company_id, "candidate")
        send_candidate_rejected(candidate.email, name, job_title,
                                smtp_config=smtp_config, cc=disp["cc"])
    except Exception as e:
        logger.warning(
            f"notify_candidate_rejected failed for "
            f"{getattr(candidate, 'id', '?')}: {e}"
        )


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
                                    smtp_config: Optional[Dict[str, Any]] = None):
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
    return _send(to, f"[Support] {subject}",
                 _build_email("New Support Ticket", body), smtp_config=smtp_config)


def send_support_reply_to_admin(to: str, requester_name: str, subject: str,
                                 body_text: str, ticket_url: str,
                                 branding: Optional[Dict[str, Any]] = None,
                                 smtp_config: Optional[Dict[str, Any]] = None):
    """Notify a client admin that the platform team replied on their ticket."""
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px">Hi <b>{requester_name}</b>,</p>
        <p style="color:#374151;font-size:0.95rem;margin:0 0 16px">The platform team replied on your ticket <b>{subject}</b>:</p>
        <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin:0 0 20px;white-space:pre-wrap;color:#374151;font-size:0.92rem">{body_text}</div>
        <p style="margin:0"><a href="{ticket_url}" style="background:#1a6b5a;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9rem">View ticket</a></p>"""
    return _send(to, f"[Support] Re: {subject}",
                 _build_email("Support Reply", body, branding),
                 reply_to=(branding or {}).get("support_email"),
                 smtp_config=smtp_config, branding=branding)


def send_support_reply_to_superadmin(to: str, company_name: str, requester_name: str,
                                      subject: str, body_text: str, ticket_url: str,
                                      smtp_config: Optional[Dict[str, Any]] = None):
    """Notify superadmin when an admin replies on their open ticket."""
    body = f"""
        <p style="color:#374151;font-size:1rem;margin:0 0 16px"><b>{requester_name}</b> from <b>{company_name}</b> replied on a ticket.</p>
        <p style="color:#6b7280;font-size:0.88rem;margin:0 0 12px">Subject: <b>{subject}</b></p>
        <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:14px;margin:0 0 20px;white-space:pre-wrap;color:#374151;font-size:0.92rem">{body_text}</div>
        <p style="margin:0"><a href="{ticket_url}" style="background:#1a6b5a;color:#fff;padding:10px 18px;border-radius:8px;text-decoration:none;font-weight:600;font-size:0.9rem">Open ticket</a></p>"""
    return _send(to, f"[Support] Re: {subject}",
                 _build_email("Support Reply", body), smtp_config=smtp_config)
