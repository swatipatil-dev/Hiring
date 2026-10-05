"""Bridge from the hiring application's send helpers to the email API.

About thirty call sites resolve a sender and pass it as ``smtp_config=`` to a
``send_*`` helper, which hands it to ``email_service.dispatch``. ``SenderRef``
lets them keep that shape while carrying a sender *decision* instead of
credentials:

* it is a dict, so existing ``.get("from_email")`` / ``.get("hourly_limit")``
  reads keep working;
* it never holds a password — there is nothing secret in it to leak;
* it is always truthy, including when the decision is "blocked" or "disabled".
  That is deliberate: every ``disp["smtp_config"] or resolve_sender(...)`` in the
  codebase therefore stops at the policy's answer instead of falling through to
  another sender.

``dispatch`` only accepts a ``SenderRef``. A plain dict — a call site that was
missed, or something rebuilding credentials by hand — is refused, not sent.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

from .policy import Actor, SenderDecision, SYSTEM

_actor: ContextVar[Optional[Actor]] = ContextVar("hireflow_email_actor", default=None)


def set_current_actor(actor: Optional[Actor]):
    """Bind who is acting for this request (set by ``src.auth.get_current_user``)."""
    return _actor.set(actor)


def current_actor() -> Actor:
    return _actor.get() or SYSTEM


class SenderRef(dict):
    """A secret-free sender decision, shaped like the legacy ``smtp_config`` dict."""

    def __init__(self, decision: SenderDecision, step_key: Optional[str] = None):
        super().__init__(
            company_id=decision.company_id,
            event_type=decision.event_type,
            step_key=step_key,
            status=decision.status,
            reason_code=decision.reason_code,
            reason=decision.reason,
            sender_id=decision.sender_id,
            sender_company_id=decision.sender_company_id,
            connection_id=decision.connection_id,
            route_source=decision.route_source,
            from_email=decision.from_email,
            from_name=decision.from_name,
            reply_to=decision.reply_to,
            # Legacy readers show "user" as the sending account.
            user=decision.from_email,
            host=decision.connection_host,
            port=decision.connection_port,
            hourly_limit=decision.hourly_limit,
        )
        self.decision = decision

    def __bool__(self) -> bool:  # see the module docstring
        return True

    @property
    def ok(self) -> bool:
        return self.decision.status == "ok"
