# Authentication Security

What protects sign-in and sessions, where it lives, and what is deliberately not done
yet. Code: `backend/src/auth.py`, `backend/routes/auth_routes.py`, `backend/src/mfa.py`,
`backend/src/rate_limit.py`. Tests: `test_auth.py`, `test_auth_otp.py`,
`test_auth_hardening.py`, `test_auth_gaps.py`. Support/super-admin access to client data
is covered in [TENANT_ISOLATION.md](TENANT_ISOLATION.md).

## Passwords

- **Hashing:** bcrypt with cost 12. There is no plaintext or SHA fallback: an unknown
  hash format never verifies.
- **One policy for every path that sets a password** (`validate_password_strength`):
  - 8 characters minimum;
  - at least two of lowercase, uppercase, digits and symbols;
  - not on the common-password list;
  - **at most 72 bytes**. bcrypt ignores everything after byte 72, so a longer password
    is refused rather than silently truncated.

## Sessions

- **Access tokens** are JWTs with a 60-minute lifetime (`ACCESS_TOKEN_EXPIRE_MINUTES`).
  `/auth/refresh` re-issues them until `SESSION_MAX_HOURS` (default 12) after the last
  real sign-in.
- **Revocation:** every token carries the user's `token_version`, and every
  authenticated path compares it with the database. `revoke_user_sessions` bumps it,
  which voids every token issued before, on:
  - logout, password change or reset, and an admin password reset;
  - MFA enrolment, disable or admin reset;
  - deactivation, through either the status toggle or "delete user";
  - company suspension.
- **Signed file links** (resumes, photos and so on) include the viewer's `token_version`
  in their signature, so they die with the session instead of living out their
  30-minute TTL.

## OTP and password reset

- **Codes:** six digits from the OS CSPRNG, stored only as an HMAC and bound to one
  purpose (`verify` or `reset`). They expire after 10 minutes.
- **Guess limits:** a code is voided after 5 wrong guesses, with at most 10 failures per
  account per hour across reissued codes.
- **After a reset:** every session is signed out and any login lockout is lifted.
- **No account enumeration:** verify, resend, forgot and reset all give the same answer
  for known and unknown emails. The email itself is sent after the response, so response
  time can't reveal an account either. Login failures are one generic 401, and unknown
  emails still spend bcrypt time.
- **Error responses:** a 500 from login or registration returns a generic message; the
  detail goes to the log.

## Rate limits and lockout

- **Per IP (slowapi):** login 5/min, forgot-password 3/min, resend-otp 3/min, MFA step
  10/min, and more (the decorators in `auth_routes.py`).
  - `X-Forwarded-For` is trusted only from `TRUSTED_PROXIES`.
  - The counter store is shared through Redis (`RATE_LIMIT_STORAGE_URI` or `REDIS_URL`).
- **Per account:** 10 failures in 15 minutes locks the account for 15 minutes. These
  count as failures:
  - wrong passwords at sign-in;
  - wrong MFA codes at sign-in and at enrolment confirm;
  - a wrong password or code when disabling MFA or regenerating recovery codes.

## Multi-factor authentication

- **Method:** TOTP (RFC 6238), compatible with any authenticator app.
  - The secret is Fernet-encrypted.
  - Codes are replay-guarded (`mfa_last_step`).
  - There are 10 single-use recovery codes, stored as HMACs keyed by `SECRET_KEY`.
    Codes issued before 2026-09-30 are plain SHA-256 and still work until used or
    regenerated.
- **Required for `MFA_REQUIRED_ROLES`** (default `superadmin,admin`):
  - these users are sent through enrolment at their next sign-in;
  - tokens without a verified second factor are refused;
  - they cannot turn MFA off.

  Anyone else can turn it on from their profile. Add `hr` to the list to require it for
  recruiters too; they will be asked to enrol at their next sign-in.
- **Changing the second factor** (disable, new recovery codes) needs the password **and**
  a current code.
- **Lost phone:** a company admin can reset another user's MFA from Admin → Users →
  "Reset 2FA" (`POST /admin/users/{id}/mfa/reset`).
  - The reset signs the user out and is recorded in the audit log.
  - Confirm who is asking before you do it.
  - Admins can't reset their own; they use a recovery code or ask another admin.
- **Key changes:** the TOTP secrets are encrypted with `EMAIL_ENCRYPTION_KEY` (else a key
  derived from `SECRET_KEY`), and recovery codes are keyed by `SECRET_KEY`. Rotating
  either key breaks every enrolment. An undecryptable secret is logged as an error
  ("cannot be decrypted"); fix it by restoring the key or resetting each user's MFA.

## Known limitations (not done yet)

- **Tokens live in `localStorage`**, so any XSS can read one. Moving to an httpOnly,
  SameSite cookie is the fix, and needs CSRF protection added alongside.
- **`/refresh` doesn't revoke the token it replaces**, because there is no `jti`
  denylist. The old token lives out its (at most 60-minute) lifetime.
- **Proctoring WebSockets** use a per-session capability token. Logging out does not close
  a test already in progress.
- **Enrolment is trust-on-first-use:** someone who has the password of an admin who hasn't
  enrolled yet can enrol their own phone. Create admin accounts and have them enrol
  promptly.
- **Registration says "Email already registered"**, which reveals that the account
  exists. It's kept for usability; the rate limit bounds it.
- **If Redis is down**, each API worker counts rate limits on its own, so the effective
  limit is up to 3× (the per-account lockout is in the database and unaffected).
