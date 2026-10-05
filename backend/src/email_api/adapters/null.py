"""An adapter that never opens a socket.

Used for ``hf_test_`` API keys (so a customer can exercise the whole API
without mailing anyone) and for local development with
``EMAIL_DELIVERY_MODE=null``. It can simulate every outcome from the
recipient's local part, so acceptance tests and customers alike can reach each
state on purpose:

  fail+...@      permanent 550 for that recipient
  bounce+...@    550 5.1.1 user unknown (also suppresses the address)
  defer+...@     temporary 451 for that recipient
  throttle+...@  421 rate limit for the whole message
  unknown+...@   outcome lost after DATA — status ``unknown``
  anything else  accepted
"""
from __future__ import annotations

from .base import (Capabilities, DeliveryError, RecipientResult, Snapshot,
                   SubmissionResult, ValidationResult, PERMANENT, TEMPORARY,
                   THROTTLED, UNKNOWN, BLOCKED)
from .. import constants as C


def _tag(addr: str) -> str:
    local = addr.split("@", 1)[0].lower()
    return local.split("+", 1)[0] if "+" in local else ""


class NullAdapter:
    capabilities = Capabilities()

    def validate_connection(self, conn, secret: str) -> ValidationResult:
        if (getattr(conn, "username", "") or "").startswith("fail"):
            return ValidationResult(False, "auth", DeliveryError(
                C.R_AUTH_FAILED, BLOCKED, "simulated authentication failure", 535,
                connection_status=C.CONN_FAILED))
        return ValidationResult(True, "done")

    def send(self, conn, snapshot: Snapshot, secret: str) -> SubmissionResult:
        rcpts = snapshot.envelope_recipients
        tags = {_tag(a) for a in rcpts}
        if "throttle" in tags:
            return SubmissionResult(C.OUT_TEMP, "mail", error=DeliveryError(
                "throttled", THROTTLED, "simulated rate limit", 421, retry_after=900))
        results = []
        for addr in rcpts:
            tag = _tag(addr)
            if tag == "fail":
                results.append(RecipientResult(addr, C.RCPT_FAILED, 550, "simulated rejection"))
            elif tag == "bounce":
                results.append(RecipientResult(addr, C.RCPT_FAILED, 550,
                                               "5.1.1 simulated user unknown", suppress=True))
            elif tag == "defer":
                results.append(RecipientResult(addr, C.RCPT_DEFERRED, 451, "simulated deferral"))
            else:
                results.append(RecipientResult(addr, C.RCPT_ACCEPTED, 250, "OK"))
        accepted = [r for r in results if r.status == C.RCPT_ACCEPTED]
        if not accepted:
            temp = any(r.status == C.RCPT_DEFERRED for r in results)
            return SubmissionResult(
                C.OUT_TEMP if temp else C.OUT_PERM, "rcpt", results,
                DeliveryError("recipients_rejected", TEMPORARY if temp else PERMANENT,
                              "no recipient accepted", results[0].smtp_code))
        if "unknown" in tags:
            return SubmissionResult(C.OUT_UNKNOWN, "data", results, DeliveryError(
                "connection_lost_after_data", UNKNOWN, "simulated disconnect after DATA"))
        return SubmissionResult(C.OUT_ACCEPTED, "done", results, smtp_code=250,
                                provider_response="simulated: queued")
