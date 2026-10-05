"""The one place that decides which sender an email uses — or that it must not send.

``resolve_sender(db, tenant_id, event_type, requested_sender_id=...)``:

1. The event must exist, and platform events only belong to the platform company.
2. A route for the event that is switched off means *send nothing*: no message
   is created at all.
3. An explicitly requested sender must belong to this company, be enabled, and
   have an active connection. Otherwise the message is **blocked** — never
   quietly sent from something else.
4. Otherwise the event's routed sender, if one is set (blocked if unusable).
5. Otherwise the company default (the ``__default__`` route; blocked if unusable).
6. Otherwise, for OTP / verification / reset mail only, the platform company's
   explicit route for that event — a database record shown in Mail Routing.
7. Otherwise blocked: ``no_sender_configured``.

The environment is never consulted. "Nothing configured" and "configured but
broken" are different reason codes, so the admin view can say which.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from . import constants as C
from . import senders as S


@dataclass(frozen=True)
class SenderDecision:
    status: str                      # ok | blocked | disabled
    company_id: str
    event_type: str
    reason_code: Optional[str] = None
    reason: Optional[str] = None
    sender_id: Optional[str] = None
    sender_company_id: Optional[str] = None
    connection_id: Optional[str] = None
    route_source: Optional[str] = None
    policy_version: Optional[int] = None
    from_email: Optional[str] = None
    from_name: Optional[str] = None
    reply_to: Optional[str] = None
    hourly_limit: Optional[int] = None
    connection_host: Optional[str] = None
    connection_port: Optional[int] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Actor:
    """Who is asking. ``type`` is user | api_key | system."""
    type: str = "system"
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    external_reference: Optional[str] = None


SYSTEM = Actor()


def authorize_sender_use(actor: Actor, sender) -> bool:
    """Whether ``actor`` may send as ``sender``.

    Every user and key of a company may use any of its enabled senders today;
    per-sender permissions (``email_sender_permissions``) plug in here.
    """
    return True


def _route(db, company_id: str, event_type: str):
    from src.models.models import EmailRoute
    return (db.query(EmailRoute)
            .filter(EmailRoute.company_id == company_id, EmailRoute.event_type == event_type)
            .first())


def _is_platform(db, company_id: str) -> bool:
    from src.tenancy.platform_company import get_platform_company_id
    return bool(company_id) and get_platform_company_id(db) == company_id


def _blocked(company_id, event_type, code, *, conn=None, source=None, version=None,
             sender_id=None, detail=None) -> SenderDecision:
    return SenderDecision("blocked", company_id, event_type, code,
                          detail or S.describe_reason(code, conn), route_source=source,
                          policy_version=version, sender_id=sender_id)


def _ok(company_id, event_type, sender, conn, source, version) -> SenderDecision:
    return SenderDecision(
        "ok", company_id, event_type, sender_id=sender.id,
        sender_company_id=sender.company_id, connection_id=conn.id,
        route_source=source, policy_version=version, from_email=sender.email,
        from_name=sender.display_name, reply_to=sender.reply_to,
        hourly_limit=sender.hourly_limit or conn.hourly_limit,
        connection_host=conn.host, connection_port=conn.port)


def _use(db, company_id, event_type, sender_id, source, version, actor, *,
         unusable_code=None, owner_id=None) -> SenderDecision:
    owner_id = owner_id or company_id
    sender, conn = S.load(db, owner_id, sender_id)
    reason = S.usable_reason(sender, conn)
    if reason is None and not authorize_sender_use(actor, sender):
        reason = C.R_SENDER_NOT_FOUND
    if reason:
        code = unusable_code or reason
        detail = S.describe_reason(reason, conn)
        return _blocked(company_id, event_type, code, conn=conn, source=source,
                        version=version, sender_id=sender_id, detail=detail)
    return _ok(company_id, event_type, sender, conn, source, version)


def _migration_pending(db, company_id: str) -> bool:
    """Legacy SMTP settings exist but were never copied into connections."""
    from src.models.models import TenantConfig, CompanyMailbox, EmailConnection
    has_new = (db.query(EmailConnection.id)
               .filter(EmailConnection.company_id == company_id,
                       EmailConnection.legacy_source.isnot(None))
               .first())
    if has_new:
        return False
    legacy = (db.query(TenantConfig.id)
              .filter(TenantConfig.company_id == company_id, TenantConfig.smtp_user.isnot(None),
                      TenantConfig.smtp_user != "")
              .first()
              or db.query(CompanyMailbox.id).filter(CompanyMailbox.company_id == company_id).first())
    return bool(legacy)


def resolve_sender(db, tenant_id: str, event_type: str, *,
                   requested_sender_id: Optional[str] = None,
                   actor: Actor = SYSTEM) -> SenderDecision:
    """Decide the sender for one email. See the module docstring for the order.

    ``tenant_id`` is the authority here, and every query below filters on it
    explicitly. The request's bound tenant is bypassed so that a decision made
    *for* another company — the platform's support mail, sent while a client
    admin's request is bound to the client — sees that company's rows.
    """
    from src.tenancy import tenant_bypass
    with tenant_bypass():
        return _resolve(db, tenant_id, event_type, requested_sender_id, actor)


def _resolve(db, tenant_id, event_type, requested_sender_id, actor) -> SenderDecision:
    category = C.event_category(event_type)
    if not tenant_id or category is None:
        return _blocked(tenant_id, event_type, C.R_EVENT_NOT_ALLOWED)
    if category == C.CAT_PLATFORM and not _is_platform(db, tenant_id):
        return _blocked(tenant_id, event_type, C.R_EVENT_NOT_ALLOWED)

    route = _route(db, tenant_id, event_type) if event_type != C.DEFAULT_ROUTE else None
    version = route.policy_version if route else None
    if route is not None and not route.enabled:
        return SenderDecision("disabled", tenant_id, event_type, C.R_ROUTE_DISABLED,
                              S.describe_reason(C.R_ROUTE_DISABLED), policy_version=version)

    if requested_sender_id:
        return _use(db, tenant_id, event_type, requested_sender_id, C.SRC_REQUESTED,
                    version, actor)

    if route is not None and route.sender_id:
        return _use(db, tenant_id, event_type, route.sender_id, C.SRC_ROUTE, version, actor,
                    unusable_code=C.R_ROUTE_SENDER_UNAVAILABLE)

    default = _route(db, tenant_id, C.DEFAULT_ROUTE)
    if default is not None and default.sender_id:
        return _use(db, tenant_id, event_type, default.sender_id, C.SRC_DEFAULT,
                    default.policy_version, actor, unusable_code=C.R_ROUTE_SENDER_UNAVAILABLE)

    if category == C.CAT_AUTH and not _is_platform(db, tenant_id):
        decision = _platform_auth(db, tenant_id, event_type, actor)
        if decision is not None:
            return decision

    if _migration_pending(db, tenant_id):
        return _blocked(tenant_id, event_type, C.R_MIGRATION_PENDING)
    return _blocked(tenant_id, event_type, C.R_NO_SENDER)


def _platform_auth(db, tenant_id: str, event_type: str, actor: Actor) -> Optional[SenderDecision]:
    """The explicit platform route for verification / reset mail.

    The only place the email policy uses another company's sender for a
    company's mail, and only for the auth category.
    """
    from src.tenancy.platform_company import get_platform_company_id
    platform_id = get_platform_company_id(db)
    if not platform_id:
        return None
    route = _route(db, platform_id, event_type)
    if route is None or not route.enabled or not route.sender_id:
        return None
    return _use(db, tenant_id, event_type, route.sender_id, C.SRC_PLATFORM_AUTH,
                route.policy_version, actor, unusable_code=C.R_ROUTE_SENDER_UNAVAILABLE,
                owner_id=platform_id)


def describe_routing(db, company_id: str) -> list:
    """Effective decision per event, for the Admin routing view. Never a secret."""
    out = []
    for event_type, meta in C.EVENT_CATALOG.items():
        if meta["category"] == C.CAT_PLATFORM and not _is_platform(db, company_id):
            continue
        if event_type == C.EVENT_CONNECTION_TEST:
            continue
        d = resolve_sender(db, company_id, event_type)
        out.append({
            "event_type": event_type,
            "label": meta["label"],
            "category": meta["category"],
            "status": d.status,
            "reason_code": d.reason_code,
            "reason": d.reason,
            "route_source": d.route_source,
            "sender_id": d.sender_id,
            "from_email": d.from_email,
        })
    return out
