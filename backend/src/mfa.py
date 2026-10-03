"""
🔑 TOTP MULTI-FACTOR AUTH (RFC 6238)

Time-based one-time passwords, compatible with Google Authenticator, Microsoft
Authenticator, 1Password, Authy and the rest: SHA-1, 6 digits, 30-second steps.
Implemented here rather than pulled in as a dependency — it is thirty lines of
HMAC, and test_mfa.py checks it against the RFC's own test vectors.

The shared secret is stored Fernet-encrypted (src/security.py). Recovery codes
are stored only as HMAC-SHA256 digests keyed by SECRET_KEY, and each works once.
"""

import base64
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from typing import List, Optional, Tuple
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
# Accept the previous and next step too, for clock drift between phone and server.
DRIFT_STEPS = 1
RECOVERY_CODE_COUNT = 10


def generate_secret() -> str:
    """A new random base32 secret (160 bits, the RFC 4226 recommendation)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def current_step(at: Optional[float] = None) -> int:
    return int((time.time() if at is None else at) // STEP_SECONDS)


def totp(secret: str, at: Optional[float] = None) -> str:
    return hotp(secret, current_step(at))


def verify_totp(secret: str, code: str, last_step: Optional[int] = None,
                at: Optional[float] = None) -> Optional[int]:
    """Return the matched time step, or None.

    A step at or before `last_step` is refused, so a code seen over someone's
    shoulder (or replayed from a log) cannot be used a second time.
    """
    code = (code or "").strip().replace(" ", "")
    if not (code.isdigit() and len(code) == DIGITS):
        return None
    now = current_step(at)
    for step in range(now - DRIFT_STEPS, now + DRIFT_STEPS + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(hotp(secret, step), code):
            return step
    return None


def provisioning_uri(secret: str, account: str, issuer: Optional[str] = None) -> str:
    """The otpauth:// URI an authenticator app imports (as a QR code or a link)."""
    issuer = issuer or os.getenv("MFA_ISSUER", "HireFlow")
    label = quote(f"{issuer}:{account}")
    return (f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}")


# ── Recovery codes ──────────────────────────────────────────────────────────

# A recovery code has 40 bits. As a plain SHA-256 that is a few hours of GPU time for
# anyone holding a database dump; keyed by SECRET_KEY, the dump alone is not enough.
# New digests carry this prefix. Unprefixed ones are the old plain SHA-256, still
# accepted so codes issued before the change keep working until used or regenerated.
_HMAC_PREFIX = "h1:"


def _normalise_recovery(code: str) -> bytes:
    return (code or "").strip().lower().replace("-", "").replace(" ", "").encode("utf-8")


def _hash_recovery(code: str) -> str:
    key = os.getenv("SECRET_KEY", "").encode("utf-8")
    return _HMAC_PREFIX + hmac.new(key, _normalise_recovery(code), hashlib.sha256).hexdigest()


def _legacy_hash_recovery(code: str) -> str:
    return hashlib.sha256(_normalise_recovery(code)).hexdigest()


def generate_recovery_codes() -> Tuple[List[str], str]:
    """Return (plaintext codes to show once, JSON of their hashes to store)."""
    codes = []
    for _ in range(RECOVERY_CODE_COUNT):
        raw = secrets.token_hex(5)            # 10 hex chars, 40 bits
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes, json.dumps([_hash_recovery(c) for c in codes])


def consume_recovery_code(stored_json: Optional[str], code: str) -> Tuple[bool, Optional[str]]:
    """Check `code` against the stored hashes; on a match, return the list without it."""
    try:
        hashes = json.loads(stored_json or "[]")
    except (TypeError, ValueError):
        return False, stored_json
    wanted, legacy = _hash_recovery(code), _legacy_hash_recovery(code)
    for i, h in enumerate(hashes):
        expected = wanted if str(h).startswith(_HMAC_PREFIX) else legacy
        if hmac.compare_digest(str(h), expected):
            remaining = hashes[:i] + hashes[i + 1:]
            return True, json.dumps(remaining)
    return False, stored_json


def recovery_codes_remaining(stored_json: Optional[str]) -> int:
    try:
        return len(json.loads(stored_json or "[]"))
    except (TypeError, ValueError):
        return 0
