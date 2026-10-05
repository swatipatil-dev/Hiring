"""Who is calling the email API, which company they act for, and what they may do.

One bearer header, two kinds of credential:

* ``hf_live_`` / ``hf_test_`` — a company API key (``api_keys.verify``). Scopes
  are the key's.
* anything else — a signed-in user's JWT, through the same ``resolve_token_user``
  every route uses. Admins get every scope; HR users may send and read mail and
  view (not change) connections and senders. Other roles are refused.

Either way the company comes from the credential, server-side. A ``company_id``
in a request body is never authority — the body models forbid unknown fields, so
it is rejected outright. On a company's own domain the credential must belong to
that company.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import FrozenSet, Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from src.config.database_config_mysql import get_db

from . import api_keys
from . import constants as C
from .policy import Actor


@dataclass(frozen=True)
class EmailPrincipal:
    tenant_id: str
    actor_type: str                   # user | api_key
    scopes: FrozenSet[str]
    mode: str = "live"                # live | test
    user_id: Optional[str] = None
    api_key_id: Optional[str] = None
    key_hourly_quota: Optional[int] = None
    user: object = None

    @property
    def is_test(self) -> bool:
        return self.mode == "test"

    def actor(self, external_reference: Optional[str] = None) -> Actor:
        return Actor(type=self.actor_type, user_id=self.user_id, api_key_id=self.api_key_id,
                     external_reference=external_reference)


def _unauthorized(code: str, message: str, status: int = 401) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    return HTTPException(status_code=status, detail={"code": code, "message": message},
                         headers=headers)


def _client_ip(request: Request) -> Optional[str]:
    try:
        from src.rate_limit import client_ip_key
        return client_ip_key(request)
    except Exception:  # noqa: BLE001
        return request.client.host if request.client else None


def _authenticate_email_principal(request: Request, db: Session = Depends(get_db)) -> EmailPrincipal:
    header = request.headers.get("authorization") or ""
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized("credentials_required", "Send 'Authorization: Bearer <api key or token>'")
    token = token.strip()
    domain_company = getattr(request.state, "company_id", None)

    if api_keys.looks_like_key(token):
        try:
            key = api_keys.verify(db, token, _client_ip(request))
        except api_keys.ApiKeyError as e:
            raise _unauthorized(e.code, str(e), e.status_code)
        if domain_company and key.company_id != domain_company:
            raise _unauthorized("wrong_company", "This key belongs to another company", 403)
        return EmailPrincipal(tenant_id=key.company_id, actor_type="api_key",
                              scopes=frozenset(key.scopes or ()), mode=key.mode,
                              api_key_id=key.id, key_hourly_quota=key.hourly_quota)

    from src.auth import resolve_token_user
    user, _payload = resolve_token_user(db, token)
    if user.role == "admin":
        scopes = C.ADMIN_SCOPES
    elif user.role == "hr":
        scopes = C.HR_SCOPES
    else:
        raise _unauthorized("role_not_allowed", "Your role cannot use the email API", 403)
    if not user.company_id:
        raise _unauthorized("no_company", "Your account is not attached to a company", 403)
    if domain_company and user.company_id != domain_company:
        raise _unauthorized("wrong_company", "You do not have access to this company's portal", 403)
    return EmailPrincipal(tenant_id=user.company_id, actor_type="user", scopes=scopes,
                          user_id=user.id, user=user)


async def get_email_principal(
        principal: EmailPrincipal = Depends(_authenticate_email_principal)) -> EmailPrincipal:
    """Bind the caller's company as the tenant. Must stay ``async`` — see
    ``src.auth.get_current_user`` for why a sync dependency would lose it."""
    from src.tenancy import set_current_company
    set_current_company(principal.tenant_id)
    return principal


def require_scopes(*needed: str):
    """Dependency: the principal, provided it holds every scope in ``needed``."""
    for s in needed:
        assert s in C.SCOPES, s

    async def checker(principal: EmailPrincipal = Depends(get_email_principal)) -> EmailPrincipal:
        missing = [s for s in needed if s not in principal.scopes]
        if missing:
            raise _unauthorized("insufficient_scope", f"Missing scope: {', '.join(missing)}", 403)
        return principal
    return checker
