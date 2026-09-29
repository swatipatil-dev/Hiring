"""Auth hardening: hashed single-purpose OTPs, session revocation, login lockout,
TOTP MFA, and an audit trail for privileged access

users:
  otp_hash / otp_purpose / otp_attempts   the emailed code is stored only as an HMAC,
                                          bound to one purpose (verify | reset), and
                                          voided after 5 wrong guesses. The legacy
                                          plaintext otp_code is cleared here.
  token_version                           bumped to revoke every JWT the user holds.
  failed_login_count / last_failed_login_at / locked_until
                                          per-account lockout.
  mfa_enabled / mfa_secret / mfa_recovery_codes / mfa_last_step
                                          TOTP multi-factor auth.

audit_log              append-only: superadmin requests, reads of client data,
                       support-access grants.
support_access_grants  a company admin's time-limited consent for platform support
                       to read that company's candidate and interview data.

Idempotent per object; every new users column has a server default or is nullable,
so existing rows keep working.

Revision ID: k7a1b2c3d4e5
Revises: j6z7a8b9c0d1
Create Date: 2026-09-29 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

from migration_helpers import referenced_collation


revision: str = 'k7a1b2c3d4e5'
down_revision: Union[str, None] = 'j6z7a8b9c0d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

USER_COLUMNS = [
    ("otp_hash", sa.String(64), {}),
    ("otp_purpose", sa.String(20), {}),
    ("otp_attempts", sa.Integer(), {"server_default": sa.text("0"), "nullable": False}),
    ("otp_recent_failures", sa.Integer(), {"server_default": sa.text("0"), "nullable": False}),
    ("otp_failures_since", sa.DateTime(), {}),
    ("token_version", sa.Integer(), {"server_default": sa.text("0"), "nullable": False}),
    ("failed_login_count", sa.Integer(), {"server_default": sa.text("0"), "nullable": False}),
    ("last_failed_login_at", sa.DateTime(), {}),
    ("locked_until", sa.DateTime(), {}),
    ("mfa_enabled", sa.Boolean(), {"server_default": sa.text("0"), "nullable": False}),
    ("mfa_secret", sa.String(255), {}),
    ("mfa_recovery_codes", sa.Text(), {}),
    ("mfa_last_step", sa.Integer(), {}),
]


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _has_column(table: str, column: str) -> bool:
    return column in {c["name"] for c in _inspector().get_columns(table)}


def _has_index(table: str, name: str) -> bool:
    if not _has_table(table):
        return False
    return name in {i["name"] for i in _inspector().get_indexes(table)}


def _table_kwargs(*targets):
    collation = referenced_collation(op.get_bind(), *targets)
    return {"mysql_collate": collation} if collation else {}


def upgrade() -> None:
    for name, type_, kw in USER_COLUMNS:
        if not _has_column("users", name):
            op.add_column("users", sa.Column(name, type_, nullable=kw.get("nullable", True),
                                             server_default=kw.get("server_default")))

    # Plaintext codes issued before this revision become unusable rather than being
    # hashed in place: they expire within 10 minutes anyway.
    op.execute("UPDATE users SET otp_code = NULL, otp_expires_at = NULL")

    # Tokens minted before this revision carry no `tv` claim, which reads as 0 and
    # would match the new column's default — a token stolen before the deploy would
    # keep working (and /refresh would extend it). Start everyone at 1 so every
    # pre-deploy token is refused and each user signs in once more.
    op.execute("UPDATE users SET token_version = 1 WHERE token_version = 0")

    if not _has_table("audit_log"):
        op.create_table(
            "audit_log",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=True),
            sa.Column("actor_user_id", sa.String(36), nullable=True),
            sa.Column("actor_email", sa.String(255), nullable=True),
            sa.Column("actor_role", sa.String(50), nullable=True),
            sa.Column("action", sa.String(100), nullable=False),
            sa.Column("resource", sa.String(500), nullable=True),
            sa.Column("method", sa.String(10), nullable=True),
            sa.Column("path", sa.String(500), nullable=True),
            sa.Column("ip", sa.String(64), nullable=True),
            sa.Column("user_agent", sa.String(255), nullable=True),
            sa.Column("detail", sa.Text, nullable=True),
            **_table_kwargs(("companies", "id")),
        )
    for col in ("created_at", "company_id", "actor_user_id"):
        name = f"ix_audit_log_{col}"
        if not _has_index("audit_log", name):
            op.create_index(name, "audit_log", [col])

    if not _has_table("support_access_grants"):
        op.create_table(
            "support_access_grants",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False),
            sa.Column("granted_by", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("reason", sa.String(500), nullable=True),
            sa.Column("created_at", sa.DateTime, nullable=True),
            sa.Column("expires_at", sa.DateTime, nullable=False),
            sa.Column("revoked_at", sa.DateTime, nullable=True),
            sa.Column("revoked_by", sa.String(36), sa.ForeignKey("users.id"), nullable=True),
            **_table_kwargs(("companies", "id"), ("users", "id")),
        )
    if not _has_index("support_access_grants", "ix_support_access_grants_company_id"):
        op.create_index("ix_support_access_grants_company_id", "support_access_grants",
                        ["company_id"])


def downgrade() -> None:
    if _has_table("support_access_grants"):
        op.drop_table("support_access_grants")
    if _has_table("audit_log"):
        op.drop_table("audit_log")
    for name, _type, _kw in reversed(USER_COLUMNS):
        if _has_column("users", name):
            op.drop_column("users", name)
