"""Build the MIME message for one email.

Moved out of ``email_service._deliver`` so the legacy sender and the API's
adapters produce byte-for-byte the same message. The header choices are about
inbox placement, and the reasons are kept with them below.

Every header value that came from a caller is stripped of CR and LF. The email
package's compat32 policy, which ``MIMEMultipart`` uses, writes a newline in a
header value straight through — that is header injection (a Bcc: smuggled in a
subject). Validation in ``messages.submit`` rejects such input first; this is
the backstop.
"""
from __future__ import annotations

import re
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, formatdate, make_msgid
from typing import Iterable, Optional

_CRLF = re.compile(r"[\r\n]+")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANK_RE = re.compile(r"\n{3,}")


def clean_header(value: Optional[str]) -> str:
    """A header value with any line break collapsed to a space."""
    return _CRLF.sub(" ", value or "").strip()


def html_to_text(html: str) -> str:
    """Rough plain-text rendering of an HTML body, for the text/plain part.

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


def build_mime(*, from_email: str, from_name: Optional[str], to: Iterable[str],
               subject: str, html: str, text: Optional[str] = None,
               cc: Iterable[str] = (), reply_to: Optional[str] = None,
               auto_submitted: bool = False, unsubscribe_to: Optional[str] = None,
               message_id: Optional[str] = None, msgid_domain: str = "hireflow.io",
               x_mailer: str = "HireFlow Notifications") -> MIMEMultipart:
    """Assemble a multipart/alternative message.

    Bcc recipients are deliberately not a parameter: they belong only on the
    SMTP envelope, never in a header.

    ``auto_submitted`` marks automated transactional mail (OTP codes): it tells
    receivers this is not a broadcast, which reduces spam weight without harming
    inbox placement. Other mail with a known support address gets a
    ``List-Unsubscribe`` mailto pointer (RFC 8058) instead.
    """
    msg = MIMEMultipart("alternative")
    msg["Subject"] = clean_header(subject)
    msg["From"] = formataddr((clean_header(from_name), clean_header(from_email))) \
        if from_name else clean_header(from_email)
    msg["To"] = ", ".join(clean_header(a) for a in to)
    cc = [clean_header(a) for a in cc if a]
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = clean_header(message_id) or make_msgid(domain=msgid_domain)
    msg["X-Mailer"] = x_mailer
    if reply_to:
        msg["Reply-To"] = clean_header(reply_to)
    if auto_submitted:
        msg["Auto-Submitted"] = "auto-generated"
        msg["Precedence"] = "bulk"
        msg["X-Auto-Response-Suppress"] = "All"
    elif unsubscribe_to:
        msg["List-Unsubscribe"] = f"<mailto:{clean_header(unsubscribe_to)}?subject=unsubscribe>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"

    # text/plain MUST be attached before text/html in multipart/alternative
    # so older clients pick the plain version and spam filters see balance.
    msg.attach(MIMEText(text if text is not None else html_to_text(html), "plain", "utf-8"))
    msg.attach(MIMEText(html or "", "html", "utf-8"))
    return msg
