"""
🔐 Encryption utility for sensitive credentials.

Encrypts/decrypts strings (e.g. SMTP app passwords) before they are stored
in the database. Uses Fernet (AES-128 + HMAC) so values are tamper-evident.

Key resolution order:
    1. EMAIL_ENCRYPTION_KEY env var (preferred — already a valid Fernet key)
    2. SECRET_KEY env var (derived via SHA-256 → base64)
    3. Hardcoded development fallback (NOT for production)
"""

import os
import base64
import hashlib
from cryptography.fernet import Fernet, InvalidToken


def _derive_key() -> bytes:
    raw = os.getenv("EMAIL_ENCRYPTION_KEY")
    if raw:
        return raw.encode() if isinstance(raw, str) else raw
    secret = os.getenv("SECRET_KEY") or "hireflow-dev-key-change-me"
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


_fernet = Fernet(_derive_key())


def encrypt(plaintext: str) -> str:
    if plaintext is None or plaintext == "":
        return ""
    return _fernet.encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet.decrypt(ciphertext.encode()).decode()
    except (InvalidToken, ValueError):
        return ""
