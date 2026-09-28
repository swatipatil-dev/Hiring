"""Shared rate limiter and client-IP key function.

Why this exists
---------------
Behind Cloud Run (and any reverse proxy / load balancer) the *direct* peer
address the app sees is the proxy, not the end user. On HireFlow's Cloud Run
deployment that peer is the internal front-end proxy ``169.254.169.126``.

slowapi's default ``get_remote_address`` reads ``request.client.host``, so
**every** visitor collapses into a single rate-limit bucket. The moment the
global ``10/hour`` cap on ``/api/v1/auth/register`` is reached, every real
sign-up is rejected with ``429`` and the UI shows "registration failed" — even
though each individual user only tried once.

The fix is to key on the real client IP, which Cloud Run/proxies put as the
left-most entry of the ``X-Forwarded-For`` header.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address


def client_ip_key(request) -> str:
    """Real client IP for rate limiting, proxy-aware.

    Uses the left-most address in ``X-Forwarded-For`` (the originating client;
    subsequent entries are proxies). Falls back to the direct peer address when
    the header is absent (e.g. local development).
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return get_remote_address(request)


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


# Single shared limiter so the routes' decorators and app.state.limiter (used by
# the 429 handler) are the same instance.
limiter = Limiter(key_func=client_ip_key)
