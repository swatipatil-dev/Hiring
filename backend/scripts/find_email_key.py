#!/usr/bin/env python
"""
Work out which EMAIL_ENCRYPTION_KEY decrypts data migrated from an older
deployment, by testing candidates against a real encrypted value.

Why guessing is not good enough
-------------------------------
src/security.py resolves the encryption key in three steps:

    1. EMAIL_ENCRYPTION_KEY, used as-is
    2. SECRET_KEY, hashed SHA-256 then urlsafe-base64
    3. the literal "hireflow-dev-key-change-me"

A deployment that never set EMAIL_ENCRYPTION_KEY still encrypted its mailbox
passwords — against a key derived from whatever SECRET_KEY happened to be. And
the exact bytes matter: a secret created with `echo "value" | gcloud secrets
create` carries a trailing newline, which produces a completely different key
from the same value without one. There is no way to tell by looking.

So this script tries every plausible candidate against a ciphertext you actually
have, and tells you which one works.

Getting a ciphertext to test with
---------------------------------
From the OLD database:

    SELECT smtp_pass FROM company_mailboxes
     WHERE smtp_pass IS NOT NULL AND smtp_pass != '' LIMIT 1;

    -- or, if that is empty:
    SELECT smtp_pass FROM tenant_configs
     WHERE smtp_pass IS NOT NULL AND smtp_pass != '' LIMIT 1;

If BOTH queries return nothing, there is no encrypted data at all — leave
EMAIL_ENCRYPTION_KEY blank and ignore this script entirely.

Usage
-----
    python scripts/find_email_key.py --ciphertext 'gAAAAAB...'

Add the old SECRET_KEY to test the derived candidates (the usual case):

    python scripts/find_email_key.py --ciphertext 'gAAAAAB...' --secret-key-stdin
    # then paste the secret; it is read from stdin so it never reaches your
    # shell history or the process list

The decrypted password is never printed — only its length, as confirmation.
"""
import argparse
import base64
import hashlib
import sys

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    sys.exit("cryptography is not installed — run this inside the venv:\n"
             "  /opt/hireflow/venv/bin/python scripts/find_email_key.py ...")

# Defaults baked into the source over time. Public strings, not secrets.
_HARDCODED_FALLBACKS = [
    ("security.py dev fallback", "hireflow-dev-key-change-me"),
    ("old auth.py placeholder", "your-super-secret-key-change-in-production"),
]


def derive(secret: str) -> str:
    """The exact derivation src/security.py performs on SECRET_KEY."""
    return base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()).decode()


def candidates(secret_key: str | None):
    """Every key worth trying, most likely first, de-duplicated.

    Both directions of the trailing-newline problem are covered, because you
    cannot tell which applies from the value alone:

      * A secret created with `echo "v" | gcloud secrets create` stores "v\\n",
        so the app derived its key from a string ENDING in a newline.
      * Reading that secret into this script strips the newline, whether it
        arrives via stdin, a copy-paste, or the console's "view secret value".

    So a value handed to us WITHOUT a newline may well need one added back.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(label: str, key: str) -> None:
        if key not in seen:
            seen.add(key)
            out.append((label, key))

    if secret_key is not None:
        stripped = secret_key.strip()
        for value, how in (
            (secret_key, "exactly as provided"),
            (secret_key + "\n", "with a trailing newline added back "
                                "(secret created with `echo`)"),
            (stripped, "with surrounding whitespace stripped"),
            (stripped + "\n", "stripped, then with a trailing newline"),
        ):
            add(f"SECRET_KEY {how}", derive(value))

        # A value that is already a valid Fernet key may have been used directly.
        if len(stripped) == 44 and stripped.endswith("="):
            add("SECRET_KEY used directly as a Fernet key", stripped)

    for label, literal in _HARDCODED_FALLBACKS:
        add(f"{label} ({literal!r})", derive(literal))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Find the EMAIL_ENCRYPTION_KEY that decrypts migrated data.")
    ap.add_argument("--ciphertext", required=True,
                    help="An encrypted smtp_pass value from the OLD database.")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--secret-key-stdin", action="store_true",
                       help="Read the old SECRET_KEY from stdin (preferred — keeps "
                            "it out of shell history and the process list).")
    group.add_argument("--secret-key",
                       help="The old SECRET_KEY as an argument. Visible in `ps` "
                            "and your shell history; prefer --secret-key-stdin.")
    args = ap.parse_args()

    secret = None
    if args.secret_key_stdin:
        if sys.stdin.isatty():
            print("Paste the old SECRET_KEY, then press Enter:", file=sys.stderr)
        secret = sys.stdin.readline().rstrip("\n")
        if not secret:
            return _fail("no SECRET_KEY was read from stdin")
    elif args.secret_key:
        secret = args.secret_key

    ct = args.ciphertext.strip().encode()
    if not ct.startswith(b"gAAAA"):
        print("Warning: that does not look like a Fernet token (they start "
              "'gAAAA'). It may be a legacy plaintext password stored before "
              "encryption was added — in which case there is nothing to "
              "recover.\n", file=sys.stderr)

    tried = candidates(secret)
    print(f"Testing {len(tried)} candidate key(s)...\n")

    for label, key in tried:
        try:
            plain = Fernet(key.encode()).decrypt(ct)
        except (InvalidToken, ValueError, TypeError):
            print(f"  no   {label}")
            continue

        print(f"  YES  {label}\n")
        print("Decryption succeeded "
              f"(recovered {len(plain)} characters; value not shown).\n")
        print("Put this in backend/.env:\n")
        print(f"    EMAIL_ENCRYPTION_KEY={key}\n")
        print("Set it BEFORE importing the old data, and before configuring any\n"
              "new mailbox — otherwise rows written under a different key will\n"
              "stop decrypting when you change it.")
        return 0

    print("\nNone of the candidates worked.")
    if secret is None:
        print("Re-run with --secret-key-stdin and the old SECRET_KEY — the "
              "derived candidates are the most likely to match.")
    else:
        print("Possibilities:\n"
              "  - the ciphertext came from a different deployment than the "
              "SECRET_KEY\n"
              "  - the old service set EMAIL_ENCRYPTION_KEY directly after all\n"
              "  - the stored value is legacy plaintext, not encrypted\n\n"
              "None of these block the migration. Leave EMAIL_ENCRYPTION_KEY "
              "blank, import the data, and re-enter the affected mailbox\n"
              "passwords in the admin UI - the callers fall back to the raw\n"
              "stored value, so nothing crashes.")
    return 1


def _fail(msg: str) -> int:
    print(f"error: {msg}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
