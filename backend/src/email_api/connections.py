"""Email connections: create, verify, update and disconnect.

A new SMTP connection is checked in the order the spec sets out: hostname and
port, destination allowability (``ssrf``), TLS with a verified certificate,
authentication. It becomes ``active`` only after verification succeeds. A
failure is stored with its error code rather than discarded, so the admin sees
*why*.

The password is encrypted the moment it arrives (``crypto``) and never leaves
this module again except to the adapter, immediately before use.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import Optional

from . import constants as C
from . import crypto, ssrf
from .adapters import base as adapter_base

logger = logging.getLogger(__name__)


class ConnectionError_(ValueError):
    """A connection request that cannot be accepted. ``code`` is machine-readable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def account_key(host: Optional[str], username: Optional[str]) -> str:
    """The rate-limit bucket for an upstream account.

    Two connections that log in to the same account share one provider quota,
    so they must share one bucket too.
    """
    raw = f"{(host or '').strip().lower()}|{(username or '').strip().lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _store_password(conn, password: str) -> None:
    try:
        conn.credential_enc, conn.credential_key_version = crypto.encrypt_secret(password)
    except crypto.CredentialUnavailable as e:
        raise ConnectionError_("encryption_unavailable",
                               "Email credential storage is not configured on this server") from e


def mark_verified(conn, now: Optional[datetime] = None) -> None:
    now = now or datetime.utcnow()
    conn.status = C.CONN_ACTIVE
    conn.error_code = conn.error_detail = None
    conn.verification_state = "verified"
    conn.verified_at = conn.last_tested_at = conn.last_success_at = now


def mark_failed(conn, error, now: Optional[datetime] = None) -> None:
    """Record a connection-level failure (bad password, bad certificate, ...)."""
    now = now or datetime.utcnow()
    conn.status = error.connection_status or C.CONN_FAILED
    conn.error_code = error.code
    conn.error_detail = (error.detail or "")[:500]
    conn.last_error_at = now


def verify(db, conn) -> adapter_base.ValidationResult:
    """Connect, start TLS and log in. Updates the connection's status."""
    now = datetime.utcnow()
    conn.last_tested_at = now
    try:
        secret = crypto.decrypt_secret(conn.credential_enc, conn.credential_key_version)
    except crypto.CredentialUnavailable as e:
        err = adapter_base.DeliveryError(C.R_CREDENTIAL_UNREADABLE, adapter_base.BLOCKED,
                                         str(e), connection_status=C.CONN_NEEDS_ATTENTION)
        mark_failed(conn, err, now)
        db.flush()
        return adapter_base.ValidationResult(False, "connect", err)
    try:
        adapter = adapter_base.get_adapter(conn)
    except adapter_base.UnsupportedConnection:
        err = adapter_base.DeliveryError(C.R_CONNECTION_TYPE_UNSUPPORTED, adapter_base.BLOCKED,
                                         conn.type)
        return adapter_base.ValidationResult(False, "connect", err)
    result = adapter.validate_connection(conn, secret)
    if result.ok:
        mark_verified(conn, now)
    else:
        if result.error.connection_status is None:
            result.error.connection_status = C.CONN_FAILED
        mark_failed(conn, result.error, now)
    db.flush()
    return result


def _check_endpoint(host, port, security, resolver=None) -> dict:
    try:
        return ssrf.validate_smtp_endpoint(host, port, security, resolver=resolver)
    except ssrf.DestinationNotAllowed as e:
        raise ConnectionError_(e.code, str(e)) from e


def create_smtp(db, *, company_id: str, name: str, host: str, port, username: str,
                password: str, security: Optional[str] = None,
                actor_user_id: Optional[str] = None, validate: bool = True,
                resolver=None, hourly_limit: Optional[int] = None):
    """Create and (by default) verify an SMTP connection.

    Endpoint checks run first and raise ``ConnectionError_`` with nothing
    stored. A connection that is reachable but fails TLS or login is stored as
    ``failed`` with its error code, so the admin can see and fix it.
    """
    from src.models.models import EmailConnection
    if not (username or "").strip():
        raise ConnectionError_("username_required", "SMTP username is required")
    if not password:
        raise ConnectionError_("password_required", "SMTP password is required")
    endpoint = _check_endpoint(host, port, security, resolver)
    conn = EmailConnection(
        company_id=company_id, type=C.CONN_SMTP, provider="smtp", name=name,
        status=C.CONN_PENDING, host=endpoint["host"], port=endpoint["port"],
        security=endpoint["security"], username=username.strip(),
        account_key=account_key(endpoint["host"], username), hourly_limit=hourly_limit,
        created_by_user_id=actor_user_id,
    )
    _store_password(conn, password)
    db.add(conn)
    db.flush()
    if validate:
        verify(db, conn)
    return conn


def update_smtp(db, conn, *, name=None, host=None, port=None, security=None,
                username=None, password=None, hourly_limit=None, validate: bool = True,
                resolver=None):
    """Change a connection. A blank ``password`` keeps the stored one.

    Changing where or how it connects re-runs verification; until that passes
    the connection is not ``active``, so nothing sends through a half-edited
    configuration.
    """
    if conn.status == C.CONN_DISCONNECTED and not password:
        raise ConnectionError_("password_required", "Re-enter the password to reconnect")
    changed = False
    if name is not None:
        conn.name = name
    if hourly_limit is not None:
        conn.hourly_limit = hourly_limit or None
    new_host = host if host is not None else conn.host
    new_port = port if port is not None else conn.port
    new_security = security if security is not None else (None if port is not None else conn.security)
    new_user = (username.strip() if username is not None else conn.username)
    if (new_host, int(new_port or 0), new_security, new_user) != (
            conn.host, int(conn.port or 0), conn.security, conn.username):
        endpoint = _check_endpoint(new_host, new_port, new_security, resolver)
        conn.host, conn.port, conn.security = endpoint["host"], endpoint["port"], endpoint["security"]
        conn.username = new_user
        conn.account_key = account_key(conn.host, conn.username)
        changed = True
    if password:
        _store_password(conn, password)
        changed = True
    if changed:
        conn.status = C.CONN_PENDING
        conn.disconnected_at = None
        if validate:
            verify(db, conn)
    db.flush()
    return conn


def disconnect(db, conn) -> None:
    """Stop using a connection and destroy its stored credential.

    Messages already queued through it block at the worker; nothing switches
    to another sender.
    """
    conn.status = C.CONN_DISCONNECTED
    conn.credential_enc = None
    conn.credential_key_version = None
    conn.disconnected_at = datetime.utcnow()
    db.flush()


def public(conn) -> dict:
    """API representation. Never includes the credential."""
    caps = adapter_base.Capabilities()
    return {
        "id": conn.id,
        "type": conn.type,
        "provider": conn.provider,
        "name": conn.name,
        "status": conn.status,
        "error_code": conn.error_code,
        "error_detail": conn.error_detail,
        "host": conn.host,
        "port": conn.port,
        "security": conn.security,
        "username": conn.username,
        "has_credential": bool(conn.credential_enc),
        "hourly_limit": conn.hourly_limit,
        "verification_state": conn.verification_state,
        "verified_at": conn.verified_at.isoformat() if conn.verified_at else None,
        "last_tested_at": conn.last_tested_at.isoformat() if conn.last_tested_at else None,
        "last_success_at": conn.last_success_at.isoformat() if conn.last_success_at else None,
        "last_error_at": conn.last_error_at.isoformat() if conn.last_error_at else None,
        "capabilities": {k: getattr(caps, k) for k in caps.__dataclass_fields__},
    }
