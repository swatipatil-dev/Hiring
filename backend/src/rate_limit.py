"""Shared rate limiter and client-IP key function.

Why this exists
---------------
Behind a reverse proxy the *direct* peer address the app sees is the proxy, not
the end user: Apache on the cPanel host (127.0.0.1), or Cloud Run's front end
(169.254.x.x). slowapi's default ``get_remote_address`` reads that peer, so
every visitor collapses into one bucket and a single busy campus drive locks
everyone out.

But ``X-Forwarded-For`` is a list anyone can start: the client may send
``X-Forwarded-For: 1.2.3.4`` and each proxy *appends* the address it saw. So
the left-most entry is attacker-chosen. Keying on it (as this module once did)
let a brute-forcer mint a fresh bucket per request just by changing a header,
which bypassed every limit including login and OTP.

The real client is the right-most address that was NOT added by one of our own
trusted proxies. We walk the header from the right, skipping trusted proxy
addresses, and take the first one that is not ours — and we only read the
header at all when the direct peer is itself a trusted proxy.

Configuration
-------------
TRUSTED_PROXIES          comma-separated IPs/CIDRs of our own proxies. Default:
                         loopback (Apache on the same host), link-local
                         169.254.0.0/16 (Cloud Run's internal proxy) and Google's
                         front-end ranges.
RATE_LIMIT_STORAGE_URI   limits storage URI (e.g. redis://localhost:6379/2). Falls
                         back to REDIS_URL, then in-process memory. With memory,
                         each gunicorn worker counts separately.
"""

import ipaddress
import logging
import os
from functools import lru_cache

from slowapi import Limiter
from slowapi.util import get_remote_address

logger = logging.getLogger(__name__)

_DEFAULT_TRUSTED = "127.0.0.0/8,::1,169.254.0.0/16,35.191.0.0/16,130.211.0.0/22"


@lru_cache(maxsize=1)
def _trusted_networks():
    raw = os.getenv("TRUSTED_PROXIES", _DEFAULT_TRUSTED)
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXIES: ignoring invalid entry %r", part)
    return tuple(nets)


def _is_trusted(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr)
    except ValueError:
        return False
    return any(ip in net for net in _trusted_networks())


def client_ip_key(request) -> str:
    """Real client IP for rate limiting, proxy-aware and spoof-resistant.

    Only when the direct peer is one of our trusted proxies is X-Forwarded-For
    consulted, and then from the right: each trusted hop is skipped, and the
    first address that is not ours is the client. Anything to the left of it was
    supplied by the client and is ignored.
    """
    peer = get_remote_address(request)
    forwarded = request.headers.get("x-forwarded-for")
    if not forwarded or not _is_trusted(peer):
        return peer
    hops = [h.strip() for h in forwarded.split(",") if h.strip()]
    for hop in reversed(hops):
        if not _is_trusted(hop):
            return hop
    # Every hop is one of ours (e.g. a health check from the proxy itself).
    return hops[0] if hops else peer


def user_key(request) -> str:
    """The signed-in user's id, for per-user limits; the client IP when there is none.

    Candidates often sit a test from the same office or campus network, so keying the
    code-execution limits on IP would let one busy room lock everyone out. The token
    is only decoded here to pick a bucket — the route still authenticates it.
    """
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        try:
            from jose import jwt
            from src.auth import SECRET_KEY, ALGORITHM
            sub = jwt.decode(auth[7:].strip(), SECRET_KEY, algorithms=[ALGORITHM]).get("sub")
            if sub:
                return f"user:{sub}"
        except Exception:
            pass
    return client_ip_key(request)


def _storage_uri() -> str:
    return (os.getenv("RATE_LIMIT_STORAGE_URI") or os.getenv("REDIS_URL") or "memory://").strip()


# Single shared limiter so the routes' decorators and app.state.limiter (used by
# the 429 handler) are the same instance. A shared store (Redis) makes the limits
# hold across gunicorn workers; if it is unreachable, the in-memory fallback keeps
# limiting per worker rather than failing every request.
limiter = Limiter(
    key_func=client_ip_key,
    storage_uri=_storage_uri(),
    in_memory_fallback_enabled=True,
    swallow_errors=True,
)
