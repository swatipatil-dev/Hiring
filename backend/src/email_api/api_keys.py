"""Company API keys for the external email API.

A key looks like ``hf_live_<id8><secret>`` (or ``hf_test_...``). The first 16
characters are the non-secret ``key_prefix``: stored in clear, unique, used to
find the row and to name the key in the UI. The whole token is hashed — HMAC
with ``EMAIL_API_KEY_PEPPER`` when that is set, plain SHA-256 otherwise; both
are fine for 190 bits of randomness, the pepper just makes a stolen table
useless on its own. The token is shown once, at creation, and never stored.

Test keys resolve to the same company but only ever see and create test-mode
messages, which never leave the building (NullAdapter).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import os
import secrets
from datetime import datetime, timedelta
from typing import Iterable, Optional, Tuple

from . import constants as C

PREFIXES = {"live": "hf_live_", "test": "hf_test_"}
PREFIX_LEN = 16           # "hf_live_" + 8 id characters
LAST_USED_GRANULARITY_S = 60
MAX_ROTATION_OVERLAP_H = 168


class ApiKeyError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 401):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def looks_like_key(token: Optional[str]) -> bool:
    return bool(token) and token.startswith(tuple(PREFIXES.values()))


def hash_token(token: str) -> str:
    pepper = os.getenv("EMAIL_API_KEY_PEPPER", "")
    if pepper:
        return hmac.new(pepper.encode(), token.encode(), hashlib.sha256).hexdigest()
    return hashlib.sha256(token.encode()).hexdigest()


def _new_token(mode: str) -> str:
    # token_urlsafe can produce '-' and '_'; fine inside the token, and the
    # prefix boundary is positional, not a separator.
    return PREFIXES[mode] + secrets.token_urlsafe(32)


def validate_scopes(scopes: Iterable[str]) -> list:
    scopes = sorted(set(scopes or ()))
    unknown = [s for s in scopes if s not in C.SCOPES]
    if unknown:
        raise ApiKeyError("scope_invalid", f"Unknown scope(s): {', '.join(unknown)}", 422)
    if not scopes:
        raise ApiKeyError("scope_required", "At least one scope is required", 422)
    return scopes


def validate_ips(ips: Optional[Iterable[str]]) -> Optional[list]:
    if not ips:
        return None
    out = []
    for ip in ips:
        try:
            out.append(str(ipaddress.ip_network(ip.strip(), strict=False)))
        except ValueError:
            raise ApiKeyError("allowed_ip_invalid", f"Not an IP address or network: {ip!r}", 422)
    return out


def create(db, *, company_id: str, name: str, mode: str, scopes: Iterable[str],
           actor_user_id: Optional[str], expires_at: Optional[datetime] = None,
           allowed_ips: Optional[Iterable[str]] = None, hourly_quota: Optional[int] = None,
           rotated_from_id: Optional[str] = None) -> Tuple[object, str]:
    """Create a key. Returns ``(row, token)``; the token is never retrievable again."""
    from src.models.models import TenantApiKey
    if mode not in PREFIXES:
        raise ApiKeyError("mode_invalid", "mode must be 'live' or 'test'", 422)
    if not (name or "").strip():
        raise ApiKeyError("name_required", "A key needs a name", 422)
    scopes = validate_scopes(scopes)
    ips = validate_ips(allowed_ips)
    for _ in range(5):
        token = _new_token(mode)
        prefix = token[:PREFIX_LEN]
        if not db.query(TenantApiKey.id).filter(TenantApiKey.key_prefix == prefix).first():
            break
    else:  # pragma: no cover - 48 random bits colliding five times running
        raise ApiKeyError("key_generation_failed", "Could not generate a unique key", 500)
    row = TenantApiKey(company_id=company_id, name=name.strip()[:100], mode=mode,
                       key_prefix=prefix, key_hash=hash_token(token), scopes=scopes,
                       allowed_ips=ips, hourly_quota=hourly_quota, expires_at=expires_at,
                       rotated_from_id=rotated_from_id, created_by_user_id=actor_user_id,
                       created_at=datetime.utcnow())
    db.add(row)
    db.flush()
    return row, token


def _ip_allowed(allowed, ip: Optional[str]) -> bool:
    if not allowed:
        return True
    if not ip:
        return False
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(addr in ipaddress.ip_network(n, strict=False) for n in allowed)


def verify(db, token: str, ip: Optional[str] = None):
    """Return the key row for ``token`` or raise ``ApiKeyError`` (401/403).

    Every failure before the hash comparison says the same thing, so a caller
    cannot tell an unknown prefix from a wrong secret.
    """
    from src.models.models import TenantApiKey, Company
    from src.tenancy import tenant_bypass
    invalid = ApiKeyError("api_key_invalid", "Invalid API key")
    if not looks_like_key(token) or len(token) < PREFIX_LEN + 20:
        raise invalid
    with tenant_bypass():
        row = (db.query(TenantApiKey)
               .filter(TenantApiKey.key_prefix == token[:PREFIX_LEN])
               .first())
        if row is None or not hmac.compare_digest(row.key_hash, hash_token(token)):
            raise invalid
        now = datetime.utcnow()
        if row.revoked_at is not None:
            raise ApiKeyError("api_key_revoked", "This API key has been revoked")
        if row.expires_at is not None and row.expires_at <= now:
            raise ApiKeyError("api_key_expired", "This API key has expired")
        if not _ip_allowed(row.allowed_ips, ip):
            raise ApiKeyError("ip_not_allowed", "Requests from this address are not allowed", 403)
        company = db.query(Company).filter(Company.id == row.company_id).first()
        if company is None or not company.is_active:
            raise ApiKeyError("company_inactive", "This company account is not active", 403)
        if row.last_used_at is None or (now - row.last_used_at).total_seconds() >= LAST_USED_GRANULARITY_S:
            row.last_used_at, row.last_used_ip = now, (ip or "")[:45] or None
            db.flush()
    return row


def revoke(db, row, actor_user_id: Optional[str]) -> None:
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        row.revoked_by_user_id = actor_user_id
        db.flush()


def rotate(db, row, *, overlap_hours: int, actor_user_id: Optional[str]):
    """Issue a replacement with the same settings; the old key keeps working
    for ``overlap_hours`` so a deployment can switch without downtime."""
    if row.revoked_at is not None:
        raise ApiKeyError("api_key_revoked", "A revoked key cannot be rotated", 409)
    if not 0 <= int(overlap_hours) <= MAX_ROTATION_OVERLAP_H:
        raise ApiKeyError("overlap_invalid", f"overlap_hours must be 0-{MAX_ROTATION_OVERLAP_H}", 422)
    new_row, token = create(db, company_id=row.company_id, name=row.name, mode=row.mode,
                            scopes=row.scopes, actor_user_id=actor_user_id,
                            expires_at=row.expires_at, allowed_ips=row.allowed_ips,
                            hourly_quota=row.hourly_quota, rotated_from_id=row.id)
    cutoff = datetime.utcnow() + timedelta(hours=int(overlap_hours))
    if row.expires_at is None or row.expires_at > cutoff:
        row.expires_at = cutoff
    db.flush()
    return new_row, token


def public(row) -> dict:
    now = datetime.utcnow()
    state = ("revoked" if row.revoked_at else
             "expired" if row.expires_at and row.expires_at <= now else "active")
    iso = lambda d: d.isoformat() if d else None  # noqa: E731
    return {"id": row.id, "name": row.name, "mode": row.mode, "key_prefix": row.key_prefix,
            "scopes": row.scopes, "allowed_ips": row.allowed_ips, "hourly_quota": row.hourly_quota,
            "state": state, "created_at": iso(row.created_at), "expires_at": iso(row.expires_at),
            "revoked_at": iso(row.revoked_at), "last_used_at": iso(row.last_used_at),
            "rotated_from_id": row.rotated_from_id}
