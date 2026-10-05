"""Email API foundation: connections, senders, routes, messages and outbox

The schema for the multi-tenant email API (docs/EMAIL_SYSTEM.md):

  email_connections   how mail leaves: one upstream account + its credential
  email_senders       authorised From identities on a connection
  email_routes        per-company, per-event sender choice and on/off switch
  email_messages      each accepted email, with its frozen identity and content
  email_recipients    per-addressee outcome
  email_attempts      each try at handing a message to its connection
  email_outbox        committed work still to be published to the queue
  email_suppressions  addresses a company must not mail again
  api_keys            hashed credentials for the external API

Schema only — moving the legacy SMTP settings into these tables is the next
revision. Nothing reads these tables until the send path is cut over, so this
is safe to apply ahead of the code.

Same-company ownership is enforced with composite foreign keys onto
``UNIQUE(company_id, id)``; see src/models/email_api_models.py for why a plain
foreign key is not enough.

Idempotent per object: a fresh database is built by create_all() then stamped,
so a table may already exist; indexes and foreign keys are still checked by name.

Revision ID: t1e2m3a4i5l6
Revises: s2p3l4a5t6n7
Create Date: 2026-10-05 00:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

from migration_helpers import referenced_collation


revision: str = 't1e2m3a4i5l6'
down_revision: Union[str, None] = 's2p3l4a5t6n7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _inspector():
    return sa.inspect(op.get_bind())


def _has_table(table: str) -> bool:
    return _inspector().has_table(table)


def _index_names(table: str) -> set:
    # MySQL reports UNIQUE constraints as unique indexes, so this covers both.
    return {i["name"] for i in _inspector().get_indexes(table)}


def _fk_names(table: str) -> set:
    return {fk["name"] for fk in _inspector().get_foreign_keys(table)}


def _company():
    return sa.Column("company_id", sa.String(36), sa.ForeignKey("companies.id"), nullable=False)


def _ts():
    return [sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True)]


# Each entry: (table, columns, unique constraints, plain indexes, composite FKs).
# Constraint/FK tuples are (name, [columns]) / (name, [columns], parent, [parent columns]).
TABLES = [
    ("email_connections",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("type", sa.String(30), nullable=False, server_default="smtp"),
         sa.Column("provider", sa.String(50), nullable=False, server_default="smtp"),
         sa.Column("name", sa.String(100), nullable=False),
         sa.Column("status", sa.String(30), nullable=False, server_default="pending_verification"),
         sa.Column("error_code", sa.String(64), nullable=True),
         sa.Column("error_detail", sa.String(500), nullable=True),
         sa.Column("host", sa.String(255), nullable=True),
         sa.Column("port", sa.Integer(), nullable=True),
         sa.Column("security", sa.String(30), nullable=True),
         sa.Column("username", sa.String(255), nullable=True),
         sa.Column("credential_enc", sa.Text(), nullable=True),
         sa.Column("credential_key_version", sa.String(20), nullable=True),
         sa.Column("config", sa.JSON(), nullable=True),
         sa.Column("account_key", sa.String(64), nullable=False),
         sa.Column("hourly_limit", sa.Integer(), nullable=True),
         sa.Column("is_shared", sa.Boolean(), nullable=False, server_default=sa.text("0")),
         sa.Column("verification_state", sa.String(30), nullable=True),
         sa.Column("verified_at", sa.DateTime(), nullable=True),
         sa.Column("last_tested_at", sa.DateTime(), nullable=True),
         sa.Column("last_success_at", sa.DateTime(), nullable=True),
         sa.Column("last_error_at", sa.DateTime(), nullable=True),
         sa.Column("disconnected_at", sa.DateTime(), nullable=True),
         sa.Column("legacy_source", sa.String(100), nullable=True),
         sa.Column("created_by_user_id", sa.String(36), nullable=True),
         *_ts(),
     ],
     [("uq_email_conn_company_id", ["company_id", "id"]),
      ("uq_email_conn_legacy_source", ["legacy_source"])],
     [("ix_email_conn_company_status", ["company_id", "status"])],
     []),

    ("email_senders",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("connection_id", sa.String(36), nullable=False),
         sa.Column("label", sa.String(100), nullable=False),
         sa.Column("email", sa.String(255), nullable=False),
         sa.Column("display_name", sa.String(255), nullable=True),
         sa.Column("reply_to", sa.String(255), nullable=True),
         sa.Column("verification_status", sa.String(30), nullable=False, server_default="primary"),
         sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
         sa.Column("hourly_limit", sa.Integer(), nullable=True),
         sa.Column("deleted_at", sa.DateTime(), nullable=True),
         sa.Column("legacy_source", sa.String(100), nullable=True),
         sa.Column("created_by_user_id", sa.String(36), nullable=True),
         *_ts(),
     ],
     [("uq_email_sender_company_id", ["company_id", "id"]),
      ("uq_email_sender_company_label", ["company_id", "label"]),
      ("uq_email_sender_legacy_source", ["legacy_source"])],
     [],
     [("fk_email_sender_connection", ["company_id", "connection_id"],
       "email_connections", ["company_id", "id"])]),

    ("email_routes",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("event_type", sa.String(64), nullable=False),
         sa.Column("sender_id", sa.String(36), nullable=True),
         sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("1")),
         sa.Column("policy_version", sa.Integer(), nullable=False, server_default="1"),
         sa.Column("updated_by_user_id", sa.String(36), nullable=True),
         *_ts(),
     ],
     [("uq_email_route_company_event", ["company_id", "event_type"])],
     [],
     [("fk_email_route_sender", ["company_id", "sender_id"],
       "email_senders", ["company_id", "id"])]),

    ("email_messages",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.text("0")),
         sa.Column("event_type", sa.String(64), nullable=False),
         sa.Column("priority", sa.String(10), nullable=False, server_default="normal"),
         sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
         sa.Column("status_reason", sa.String(64), nullable=True),
         sa.Column("status_detail", sa.String(500), nullable=True),
         sa.Column("sender_id", sa.String(36), nullable=True),
         sa.Column("sender_company_id", sa.String(36), nullable=True),
         sa.Column("connection_id", sa.String(36), nullable=True),
         sa.Column("route_source", sa.String(30), nullable=True),
         sa.Column("policy_version", sa.Integer(), nullable=True),
         sa.Column("from_email", sa.String(255), nullable=True),
         sa.Column("from_name", sa.String(255), nullable=True),
         sa.Column("reply_to", sa.String(255), nullable=True),
         sa.Column("subject", sa.String(998), nullable=False, server_default=""),
         sa.Column("content_enc", sa.Text().with_variant(mysql.MEDIUMTEXT(), "mysql"), nullable=True),
         sa.Column("content_key_version", sa.String(20), nullable=True),
         sa.Column("content_sha256", sa.String(64), nullable=True),
         sa.Column("content_purged_at", sa.DateTime(), nullable=True),
         sa.Column("is_sensitive", sa.Boolean(), nullable=False, server_default=sa.text("0")),
         sa.Column("template_id", sa.String(36), nullable=True),
         sa.Column("message_id_header", sa.String(255), nullable=True),
         sa.Column("actor_type", sa.String(20), nullable=False, server_default="system"),
         sa.Column("actor_user_id", sa.String(36), nullable=True),
         sa.Column("api_key_id", sa.String(36), nullable=True),
         sa.Column("external_actor_reference", sa.String(255), nullable=True),
         sa.Column("external_reference", sa.String(255), nullable=True),
         sa.Column("metadata", sa.JSON(), nullable=True),
         sa.Column("reference_type", sa.String(50), nullable=True),
         sa.Column("reference_id", sa.String(36), nullable=True),
         sa.Column("step_key", sa.String(50), nullable=True),
         sa.Column("idempotency_key", sa.String(255), nullable=True),
         sa.Column("request_hash", sa.String(64), nullable=True),
         sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
         sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="6"),
         sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
         sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
         sa.Column("accepted_at", sa.DateTime(), nullable=True),
         sa.Column("completed_at", sa.DateTime(), nullable=True),
         sa.Column("cancelled_at", sa.DateTime(), nullable=True),
         *_ts(),
     ],
     [("uq_email_msg_company_id", ["company_id", "id"]),
      ("uq_email_msg_idempotency", ["company_id", "is_test", "idempotency_key"])],
     [("ix_email_msg_company_created", ["company_id", "created_at"]),
      ("ix_email_msg_company_status", ["company_id", "status"]),
      ("ix_email_msg_company_extref", ["company_id", "external_reference"]),
      ("ix_email_msg_connection_status", ["connection_id", "status"])],
     [("fk_email_msg_sender", ["sender_company_id", "sender_id"],
       "email_senders", ["company_id", "id"]),
      ("fk_email_msg_connection", ["sender_company_id", "connection_id"],
       "email_connections", ["company_id", "id"])]),

    ("email_recipients",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("message_id", sa.String(36), nullable=False),
         sa.Column("email", sa.String(255), nullable=False),
         sa.Column("name", sa.String(255), nullable=True),
         sa.Column("kind", sa.String(5), nullable=False, server_default="to"),
         sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
         sa.Column("smtp_code", sa.Integer(), nullable=True),
         sa.Column("smtp_response", sa.String(500), nullable=True),
         sa.Column("updated_at", sa.DateTime(), nullable=True),
     ],
     [],
     [("ix_email_rcpt_company_email", ["company_id", "email"])],
     [("fk_email_rcpt_message", ["company_id", "message_id"],
       "email_messages", ["company_id", "id"])]),

    ("email_attempts",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("message_id", sa.String(36), nullable=False),
         sa.Column("attempt_no", sa.Integer(), nullable=False),
         sa.Column("connection_id", sa.String(36), nullable=True),
         sa.Column("account_key", sa.String(64), nullable=True),
         sa.Column("worker_id", sa.String(100), nullable=True),
         sa.Column("phase_reached", sa.String(10), nullable=True),
         sa.Column("outcome", sa.String(30), nullable=True),
         sa.Column("error_code", sa.String(64), nullable=True),
         sa.Column("smtp_code", sa.Integer(), nullable=True),
         sa.Column("provider_response", sa.String(1000), nullable=True),
         sa.Column("started_at", sa.DateTime(), nullable=True),
         sa.Column("finished_at", sa.DateTime(), nullable=True),
         sa.Column("duration_ms", sa.Integer(), nullable=True),
     ],
     [("uq_email_attempt_no", ["message_id", "attempt_no"])],
     [("ix_email_attempt_account_started", ["account_key", "started_at"])],
     [("fk_email_attempt_message", ["company_id", "message_id"],
       "email_messages", ["company_id", "id"])]),

    ("email_outbox",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("message_id", sa.String(36), nullable=False),
         sa.Column("state", sa.String(10), nullable=False, server_default="pending"),
         sa.Column("available_at", sa.DateTime(), nullable=False),
         sa.Column("lease_owner", sa.String(64), nullable=True),
         sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
         sa.Column("publish_count", sa.Integer(), nullable=False, server_default="0"),
         sa.Column("last_publish_error", sa.String(255), nullable=True),
         *_ts(),
     ],
     [("uq_email_outbox_message", ["message_id"])],
     [("ix_email_outbox_state_available", ["state", "available_at"]),
      ("ix_email_outbox_state_lease", ["state", "lease_expires_at"])],
     [("fk_email_outbox_message", ["company_id", "message_id"],
       "email_messages", ["company_id", "id"])]),

    ("email_suppressions",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("email", sa.String(255), nullable=False),
         sa.Column("reason", sa.String(20), nullable=False),
         sa.Column("source", sa.String(20), nullable=False),
         sa.Column("created_by_user_id", sa.String(36), nullable=True),
         sa.Column("created_at", sa.DateTime(), nullable=True),
     ],
     [("uq_email_suppression_company_email", ["company_id", "email"])],
     [],
     []),

    ("api_keys",
     lambda: [
         sa.Column("id", sa.String(36), primary_key=True),
         _company(),
         sa.Column("name", sa.String(100), nullable=False),
         sa.Column("mode", sa.String(4), nullable=False, server_default="live"),
         sa.Column("key_prefix", sa.String(24), nullable=False),
         sa.Column("key_hash", sa.String(64), nullable=False),
         sa.Column("scopes", sa.JSON(), nullable=False),
         sa.Column("allowed_ips", sa.JSON(), nullable=True),
         sa.Column("hourly_quota", sa.Integer(), nullable=True),
         sa.Column("rotated_from_id", sa.String(36), nullable=True),
         sa.Column("created_by_user_id", sa.String(36), nullable=True),
         sa.Column("revoked_by_user_id", sa.String(36), nullable=True),
         sa.Column("created_at", sa.DateTime(), nullable=True),
         sa.Column("expires_at", sa.DateTime(), nullable=True),
         sa.Column("revoked_at", sa.DateTime(), nullable=True),
         sa.Column("last_used_at", sa.DateTime(), nullable=True),
         sa.Column("last_used_ip", sa.String(45), nullable=True),
     ],
     [("uq_api_key_prefix", ["key_prefix"])],
     [("ix_api_key_company", ["company_id"])],
     []),
]


def upgrade() -> None:
    # One collation for every new table: the composite foreign keys point at
    # each other as well as at companies.id, and MySQL requires both sides of
    # every one of them to agree (error 3780). See referenced_collation().
    table_kwargs = {"mysql_engine": "InnoDB"}
    collation = referenced_collation(op.get_bind(), ("companies", "id"))
    if collation:
        table_kwargs["mysql_collate"] = collation

    for table, columns, uniques, indexes, fks in TABLES:
        if not _has_table(table):
            op.create_table(
                table, *columns(),
                *[sa.UniqueConstraint(*cols, name=name) for name, cols in uniques],
                **table_kwargs,
            )
        existing = _index_names(table)
        for name, cols in uniques:
            if name not in existing:
                op.create_unique_constraint(name, table, cols)
        for name, cols in indexes:
            if name not in existing:
                op.create_index(name, table, cols)
        existing_fks = _fk_names(table)
        for name, cols, parent, parent_cols in fks:
            if name not in existing_fks:
                op.create_foreign_key(name, table, parent, cols, parent_cols)


def downgrade() -> None:
    # Children first. Dropping a table takes its indexes and foreign keys with
    # it, which avoids MySQL refusing to drop an index a foreign key needs.
    for table, *_ in reversed(TABLES):
        if _has_table(table):
            op.drop_table(table)
