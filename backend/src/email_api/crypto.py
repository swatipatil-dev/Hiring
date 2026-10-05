"""Versioned encryption for email credentials and stored message content.

``src/security.py`` derives its key from ``SECRET_KEY`` — the JWT signing secret
— whenever ``EMAIL_ENCRYPTION_KEY`` is unset, and its ``decrypt`` returns ``""``
on a wrong key. The send path turned that ``""`` into "use the environment's
password", which is how a key mix-up became mail from the wrong account.

Here instead:

* Keys come only from ``EMAIL_CREDENTIAL_KEYS``, a comma-separated list of
  ``<version>:<fernet key>``. The first is the one new values are written with;
  the rest still decrypt, so rotation is "prepend a key, re-encrypt, drop the
  old one" (scripts/reencrypt_email_credentials.py).
* Credentials and message content use separate HKDF subkeys of each master key,
  so content handling can never be turned into a credential oracle.
* A ciphertext is stored as ``<version>$<token>``; the version also has its own
  column so a re-encryption sweep can find old rows with an index.
* ``decrypt_*`` raises ``CredentialUnavailable``. It never returns a blank.

Version ``legacy`` marks a credential copied verbatim from the old tables by the
data migration. It is read with ``src.security``'s key until the re-encryption
script rewrites it, and is never written.
"""
from __future__ import annotations

import base64
import os
from typing import Dict, List, Optional, Tuple

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

LEGACY = "legacy"
_SEP = "$"
_PURPOSE_CRED = b"hireflow-email:credential"
_PURPOSE_CONTENT = b"hireflow-email:content"


class CredentialUnavailable(Exception):
    """A stored secret could not be decrypted — or no key exists to write one."""


def _derive(master: bytes, purpose: bytes) -> Fernet:
    raw = base64.urlsafe_b64decode(master)
    sub = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose).derive(raw)
    return Fernet(base64.urlsafe_b64encode(sub))


def _parse_keys(spec: str) -> List[Tuple[str, bytes]]:
    keys = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        version, sep, key = part.partition(":")
        version, key = version.strip(), key.strip()
        if not sep or not version or not key or version == LEGACY or _SEP in version:
            raise ValueError(f"EMAIL_CREDENTIAL_KEYS entry {version!r} must be '<version>:<fernet key>'")
        Fernet(key.encode())  # validates length/encoding; raises ValueError
        keys.append((version, key.encode()))
    return keys


class _Keyring:
    def __init__(self, spec: str):
        self.keys = _parse_keys(spec)
        self._cred: Dict[str, Fernet] = {v: _derive(k, _PURPOSE_CRED) for v, k in self.keys}
        self._content: Dict[str, Fernet] = {v: _derive(k, _PURPOSE_CONTENT) for v, k in self.keys}

    @property
    def primary(self) -> Optional[str]:
        return self.keys[0][0] if self.keys else None


_ring: Optional[_Keyring] = None
_ring_spec: Optional[str] = None


def _keyring() -> _Keyring:
    # Re-read when the variable changes so tests (and a rotation in a long-lived
    # dev shell) see the current value without a restart.
    global _ring, _ring_spec
    spec = os.getenv("EMAIL_CREDENTIAL_KEYS", "")
    if _ring is None or spec != _ring_spec:
        _ring, _ring_spec = _Keyring(spec), spec
    return _ring


def is_configured() -> bool:
    return _keyring().primary is not None


def primary_version() -> Optional[str]:
    return _keyring().primary


def generate_key() -> str:
    """A new master key, for operators: ``python -m src.email_api.crypto``."""
    return Fernet.generate_key().decode()


def _encrypt(table: Dict[str, Fernet], plaintext: str) -> Tuple[str, str]:
    ring = _keyring()
    if ring.primary is None:
        raise CredentialUnavailable(
            "EMAIL_CREDENTIAL_KEYS is not set; refusing to store an email secret")
    token = table[ring.primary].encrypt(plaintext.encode()).decode()
    return f"{ring.primary}{_SEP}{token}", ring.primary


def _split(stored: str) -> Tuple[Optional[str], str]:
    version, sep, token = stored.partition(_SEP)
    if not sep:
        return None, stored
    return version, token


def _decrypt(table: Dict[str, Fernet], stored: Optional[str]) -> str:
    if not stored:
        raise CredentialUnavailable("no secret stored")
    version, token = _split(stored)
    fernet = table.get(version) if version else None
    if fernet is None:
        raise CredentialUnavailable(f"no key for version {version!r}")
    try:
        return fernet.decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as e:
        raise CredentialUnavailable(f"secret under {version!r} did not decrypt") from e


def encrypt_secret(plaintext: str) -> Tuple[str, str]:
    """Encrypt a credential. Returns ``(stored_value, key_version)``."""
    if not plaintext:
        raise ValueError("refusing to encrypt an empty credential")
    return _encrypt(_keyring()._cred, plaintext)


def decrypt_secret(stored: Optional[str], key_version: Optional[str] = None) -> str:
    """Decrypt a credential, or raise ``CredentialUnavailable``."""
    if key_version == LEGACY:
        return decrypt_legacy(stored)
    return _decrypt(_keyring()._cred, stored)


def decrypt_legacy(stored: Optional[str]) -> str:
    """Read a value written by ``src.security.encrypt`` (the pre-API tables).

    Raises rather than returning the ciphertext itself, which is what the old
    ``decrypt(x) or x`` "plaintext tolerance" did with a wrong key.
    """
    if not stored:
        raise CredentialUnavailable("no secret stored")
    from src.security import _fernet as legacy_fernet
    try:
        return legacy_fernet.decrypt(stored.encode()).decode()
    except (InvalidToken, ValueError) as e:
        raise CredentialUnavailable("legacy secret did not decrypt") from e


def looks_like_fernet_token(value: Optional[str]) -> bool:
    """Whether a legacy column holds a token rather than a plaintext password."""
    return bool(value) and value.startswith("gAAAAA") and len(value) >= 100


def encrypt_content(plaintext: str) -> Tuple[str, str]:
    """Encrypt stored message content. Returns ``(stored_value, key_version)``."""
    return _encrypt(_keyring()._content, plaintext)


def decrypt_content(stored: Optional[str]) -> str:
    return _decrypt(_keyring()._content, stored)


def needs_reencryption(key_version: Optional[str]) -> bool:
    return key_version != primary_version()


if __name__ == "__main__":  # pragma: no cover - operator helper
    print(f"k1:{generate_key()}")
