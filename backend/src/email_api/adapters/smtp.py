"""Deliver through a customer's own SMTP server.

The SMTP conversation is driven one command at a time — not ``sendmail()`` —
because what to do after a failure depends on how far it got:

* before the message payload is sent, nothing has been delivered, so a
  network error is safe to retry;
* once the payload has been sent, a dropped connection or timeout means the
  server may already have accepted the mail. Retrying could send it twice, so
  the outcome is ``unknown`` and nobody retries it automatically.

``smtplib.SMTP.data()`` hides that boundary inside one call, so ``_data`` below
does the same work with the boundary exposed.

Every connection goes through ``ssrf`` first (on every send, not just when the
connection was saved) and TLS is always verified.
"""
from __future__ import annotations

import logging
import os
import re
import smtplib
import socket
import ssl
from typing import List, Optional

from .base import (Capabilities, DeliveryError, RecipientResult, Snapshot,
                   SubmissionResult, ValidationResult, BLOCKED, TEMPORARY,
                   THROTTLED, PERMANENT, UNKNOWN)
from .. import constants as C
from .. import ssrf
from ..mime import build_mime

logger = logging.getLogger(__name__)

SMTP_TIMEOUT_S = int(os.getenv("SMTP_TIMEOUT_S", "15"))
MSGID_DOMAIN = os.getenv("EMAIL_MSGID_DOMAIN", "hireflow.io")
THROTTLE_BACKOFF_S = 900

_THROTTLE_WORDS = re.compile(r"rate|too many|limit|throttl|try again later", re.I)
_NO_SUCH_USER = re.compile(r"5\.1\.1|5\.1\.10|user unknown|no such user|does not exist|"
                           r"unknown user|mailbox unavailable|recipient not found", re.I)


def _text(resp) -> str:
    if isinstance(resp, bytes):
        resp = resp.decode("utf-8", "replace")
    return (resp or "")[:500]


def _is_throttle(code: int, resp: str) -> bool:
    return code == 421 or (400 <= code < 500 and bool(_THROTTLE_WORDS.search(resp)))


class _PayloadSent(Exception):
    """Wraps any failure that happened after the message payload left us."""

    def __init__(self, cause: BaseException):
        super().__init__(str(cause))
        self.cause = cause


class SMTPAdapter:
    capabilities = Capabilities()

    # ── connection ──────────────────────────────────────────────────────────
    def _open(self, conn):
        """Vet the destination and connect to the vetted address."""
        host = ssrf.validate_host(conn.host)
        mode = ssrf.validate_port(conn.port, conn.security)
        ips = ssrf.resolve_public_ips(host, int(conn.port))
        ctx = ssrf.tls_context()
        if mode == C.SECURITY_IMPLICIT_TLS:
            server = ssrf.PinnedSMTP_SSL(conn.host, int(conn.port), pinned_ip=ips[0],
                                         timeout=SMTP_TIMEOUT_S, context=ctx)
            server.ehlo()
            return server, "tls"
        server = ssrf.PinnedSMTP(conn.host, int(conn.port), pinned_ip=ips[0],
                                 timeout=SMTP_TIMEOUT_S)
        server.ehlo()
        if not server.has_extn("starttls"):
            # Never fall through to AUTH on a plaintext connection.
            raise smtplib.SMTPNotSupportedError("STARTTLS not offered")
        server.starttls(context=ctx)
        server.ehlo()
        return server, "tls"

    @staticmethod
    def _close(server) -> None:
        if server is None:
            return
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass

    def validate_connection(self, conn, secret: str) -> ValidationResult:
        server, phase = None, "connect"
        try:
            server, phase = self._open(conn)
            phase = "auth"
            server.login(conn.username, secret)
            return ValidationResult(True, "done")
        except Exception as e:  # noqa: BLE001 — every failure is classified
            return ValidationResult(False, phase, self.normalize_error(e, phase))
        finally:
            self._close(server)

    # ── send ────────────────────────────────────────────────────────────────
    @staticmethod
    def _data(server, payload: bytes):
        """``smtplib.SMTP.data`` with the point of no return made visible."""
        server.putcmd("data")
        code, repl = server.getreply()
        if code != 354:
            raise smtplib.SMTPDataError(code, repl)
        q = smtplib._quote_periods(payload)
        if q[-2:] != smtplib.bCRLF:
            q = q + smtplib.bCRLF
        q = q + b"." + smtplib.bCRLF
        try:
            server.send(q)
            return server.getreply()
        except Exception as e:  # noqa: BLE001
            raise _PayloadSent(e) from e

    def send(self, conn, snapshot: Snapshot, secret: str) -> SubmissionResult:
        msg = build_mime(
            from_email=snapshot.from_email, from_name=snapshot.from_name,
            to=snapshot.to, cc=snapshot.cc, subject=snapshot.subject,
            html=snapshot.html, text=snapshot.text, reply_to=snapshot.reply_to,
            auto_submitted=snapshot.auto_submitted,
            unsubscribe_to=snapshot.unsubscribe_to,
            message_id=snapshot.message_id_header, msgid_domain=MSGID_DOMAIN,
        )
        payload = smtplib._fix_eols(msg.as_string()).encode("ascii")
        server, phase = None, "connect"
        results: List[RecipientResult] = []
        try:
            server, phase = self._open(conn)
            phase = "auth"
            server.login(conn.username, secret)

            phase = "mail"
            code, resp = server.mail(snapshot.from_email)
            if code != 250:
                return self._refused_sender(code, _text(resp))

            phase = "rcpt"
            for addr in snapshot.envelope_recipients:
                code, resp = server.rcpt(addr)
                resp = _text(resp)
                if code in (250, 251):
                    results.append(RecipientResult(addr, C.RCPT_ACCEPTED, code, resp))
                elif 400 <= code < 500:
                    results.append(RecipientResult(addr, C.RCPT_DEFERRED, code, resp))
                else:
                    results.append(RecipientResult(addr, C.RCPT_FAILED, code, resp,
                                                   suppress=bool(_NO_SUCH_USER.search(resp))))
            if not any(r.status == C.RCPT_ACCEPTED for r in results):
                # Nothing was sent, so this is safe to classify and retry.
                temp = [r for r in results if r.status == C.RCPT_DEFERRED]
                first = (temp or results)[0]
                kind = TEMPORARY if temp else PERMANENT
                if temp and _is_throttle(first.smtp_code or 0, first.smtp_response or ""):
                    kind = THROTTLED
                return SubmissionResult(
                    C.OUT_TEMP if temp else C.OUT_PERM, "rcpt", results,
                    DeliveryError("recipients_rejected", kind, first.smtp_response or "",
                                  first.smtp_code,
                                  retry_after=THROTTLE_BACKOFF_S if kind == THROTTLED else None),
                    smtp_code=first.smtp_code, provider_response=first.smtp_response)

            phase = "data"
            code, resp = self._data(server, payload)
            resp = _text(resp)
            if code == 250:
                return SubmissionResult(C.OUT_ACCEPTED, "done", results,
                                        smtp_code=code, provider_response=resp)
            # An explicit refusal of the whole message: nothing was accepted.
            for r in results:
                if r.status == C.RCPT_ACCEPTED:
                    r.status, r.smtp_code, r.smtp_response = (
                        C.RCPT_DEFERRED if code < 500 else C.RCPT_FAILED), code, resp
            return self._refused_data(code, resp, results)
        except _PayloadSent as e:
            for r in results:
                if r.status == C.RCPT_ACCEPTED:
                    r.status = C.RCPT_UNKNOWN
            return SubmissionResult(C.OUT_UNKNOWN, "data", results, DeliveryError(
                "connection_lost_after_data", UNKNOWN,
                f"{type(e.cause).__name__}: {e.cause}"[:500]))
        except Exception as e:  # noqa: BLE001
            err = self.normalize_error(e, phase)
            outcome = {BLOCKED: C.OUT_BLOCKED, PERMANENT: C.OUT_PERM}.get(err.kind, C.OUT_TEMP)
            return SubmissionResult(outcome, phase, results, err, smtp_code=err.smtp_code,
                                    provider_response=err.detail)
        finally:
            self._close(server)

    # ── classification ──────────────────────────────────────────────────────
    @staticmethod
    def _refused_sender(code: int, resp: str) -> SubmissionResult:
        if 400 <= code < 500:
            kind = THROTTLED if _is_throttle(code, resp) else TEMPORARY
            err = DeliveryError("mail_from_deferred", kind, resp, code,
                                retry_after=THROTTLE_BACKOFF_S if kind == THROTTLED else None)
            return SubmissionResult(C.OUT_TEMP, "mail", error=err, smtp_code=code,
                                    provider_response=resp)
        # The server will not let this account send as this address. Retrying
        # cannot help and no other sender may be substituted.
        err = DeliveryError(C.R_SENDER_REJECTED, BLOCKED, resp, code)
        return SubmissionResult(C.OUT_BLOCKED, "mail", error=err, smtp_code=code,
                                provider_response=resp)

    @staticmethod
    def _refused_data(code: int, resp: str, results) -> SubmissionResult:
        if 400 <= code < 500:
            kind = THROTTLED if _is_throttle(code, resp) else TEMPORARY
            return SubmissionResult(C.OUT_TEMP, "data", results, DeliveryError(
                "data_deferred", kind, resp, code,
                retry_after=THROTTLE_BACKOFF_S if kind == THROTTLED else None),
                smtp_code=code, provider_response=resp)
        return SubmissionResult(C.OUT_PERM, "data", results, DeliveryError(
            "data_rejected", PERMANENT, resp, code), smtp_code=code, provider_response=resp)

    @staticmethod
    def normalize_error(exc: BaseException, phase: str) -> DeliveryError:
        """Map an exception raised before the payload was sent."""
        if isinstance(exc, ssrf.DestinationNotAllowed):
            return DeliveryError(C.R_DESTINATION_NOT_ALLOWED, BLOCKED, str(exc),
                                 connection_status=C.CONN_FAILED)
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return DeliveryError(C.R_AUTH_FAILED, BLOCKED, _text(exc.smtp_error), exc.smtp_code,
                                 connection_status=C.CONN_FAILED)
        if isinstance(exc, smtplib.SMTPNotSupportedError):
            what = C.R_STARTTLS_UNAVAILABLE if phase in ("connect", "tls") else "auth_unsupported"
            return DeliveryError(what, BLOCKED, str(exc), connection_status=C.CONN_FAILED)
        # Must precede the OSError arm: SSLCertVerificationError is an OSError.
        if isinstance(exc, ssl.SSLCertVerificationError):
            logger.error("SMTP certificate verification failed (%s): %s", phase, exc)
            return DeliveryError(C.R_TLS_CERT_INVALID, BLOCKED, str(exc)[:500],
                                 connection_status=C.CONN_FAILED)
        if isinstance(exc, smtplib.SMTPResponseException):
            code, resp = exc.smtp_code, _text(exc.smtp_error)
            if 400 <= code < 500:
                kind = THROTTLED if _is_throttle(code, resp) else TEMPORARY
                return DeliveryError("smtp_temporary", kind, resp, code,
                                     retry_after=THROTTLE_BACKOFF_S if kind == THROTTLED else None)
            if phase == "mail":
                return DeliveryError(C.R_SENDER_REJECTED, BLOCKED, resp, code)
            return DeliveryError("smtp_rejected", PERMANENT, resp, code)
        if isinstance(exc, socket.gaierror):
            return DeliveryError("dns_error", TEMPORARY, str(exc))
        if isinstance(exc, (smtplib.SMTPException, ssl.SSLError, socket.timeout,
                            TimeoutError, ConnectionError, OSError)):
            return DeliveryError("network_error", TEMPORARY, f"{type(exc).__name__}: {exc}"[:500])
        return DeliveryError("unexpected_error", TEMPORARY, f"{type(exc).__name__}: {exc}"[:500])
