"""Extend companies + subscription_plans with onboarding-profile fields

Adds optional legal, contact, address, business profile, hiring footprint and
branding-extension columns to ``companies`` so SuperAdmin can capture a complete
client record at activation. Also adds commercial columns (currency,
payment_method, PO) to ``subscription_plans``.

All added columns are nullable (or have safe defaults); existing tenants are
unaffected.

Revision ID: i9d0e1f2g3h4
Revises: h8c9d0e1f2g3
Create Date: 2026-05-22 12:00:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'i9d0e1f2g3h4'
down_revision: Union[str, None] = 'h8c9d0e1f2g3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


COMPANY_NEW_COLUMNS = [
    # Branding extension
    sa.Column("logo_dark_url", sa.String(length=500), nullable=True),

    # Legal & tax identity
    sa.Column("legal_name", sa.String(length=255), nullable=True),
    sa.Column("brand_name", sa.String(length=255), nullable=True),
    sa.Column("company_type", sa.String(length=50), nullable=True),
    sa.Column("cin", sa.String(length=50), nullable=True),
    sa.Column("incorporation_date", sa.Date(), nullable=True),
    sa.Column("authorized_signatory_name", sa.String(length=255), nullable=True),
    sa.Column("authorized_signatory_designation", sa.String(length=100), nullable=True),

    # Role-based contacts
    sa.Column("primary_contact_name", sa.String(length=255), nullable=True),
    sa.Column("primary_contact_email", sa.String(length=255), nullable=True),
    sa.Column("primary_contact_phone", sa.String(length=40), nullable=True),
    sa.Column("primary_contact_designation", sa.String(length=100), nullable=True),

    sa.Column("billing_contact_name", sa.String(length=255), nullable=True),
    sa.Column("billing_contact_email", sa.String(length=255), nullable=True),
    sa.Column("billing_contact_phone", sa.String(length=40), nullable=True),
    sa.Column("billing_contact_designation", sa.String(length=100), nullable=True),

    sa.Column("technical_contact_name", sa.String(length=255), nullable=True),
    sa.Column("technical_contact_email", sa.String(length=255), nullable=True),
    sa.Column("technical_contact_phone", sa.String(length=40), nullable=True),
    sa.Column("technical_contact_designation", sa.String(length=100), nullable=True),

    sa.Column("dpo_contact_name", sa.String(length=255), nullable=True),
    sa.Column("dpo_contact_email", sa.String(length=255), nullable=True),
    sa.Column("dpo_contact_phone", sa.String(length=40), nullable=True),
    sa.Column("dpo_contact_designation", sa.String(length=100), nullable=True),

    sa.Column("escalation_contact_name", sa.String(length=255), nullable=True),
    sa.Column("escalation_contact_email", sa.String(length=255), nullable=True),
    sa.Column("escalation_contact_phone", sa.String(length=40), nullable=True),
    sa.Column("escalation_contact_designation", sa.String(length=100), nullable=True),

    # Registered address
    sa.Column("reg_address_line1", sa.String(length=255), nullable=True),
    sa.Column("reg_address_line2", sa.String(length=255), nullable=True),
    sa.Column("reg_city", sa.String(length=100), nullable=True),
    sa.Column("reg_state", sa.String(length=100), nullable=True),
    sa.Column("reg_country", sa.String(length=100), nullable=True),
    sa.Column("reg_postal_code", sa.String(length=20), nullable=True),

    # Billing address
    sa.Column("billing_same_as_registered", sa.Boolean(), nullable=True, server_default=sa.true()),
    sa.Column("billing_address_line1", sa.String(length=255), nullable=True),
    sa.Column("billing_address_line2", sa.String(length=255), nullable=True),
    sa.Column("billing_city", sa.String(length=100), nullable=True),
    sa.Column("billing_state", sa.String(length=100), nullable=True),
    sa.Column("billing_country", sa.String(length=100), nullable=True),
    sa.Column("billing_postal_code", sa.String(length=20), nullable=True),

    # Business profile
    sa.Column("industry", sa.String(length=100), nullable=True),
    sa.Column("company_size_band", sa.String(length=30), nullable=True),
    sa.Column("years_in_business", sa.Integer(), nullable=True),
    sa.Column("website_url", sa.String(length=500), nullable=True),
    sa.Column("description", sa.Text(), nullable=True),

    # Hiring footprint
    sa.Column("expected_hires_per_year", sa.Integer(), nullable=True),
    sa.Column("recruiter_count", sa.Integer(), nullable=True),
    sa.Column("hiring_locations", sa.Text(), nullable=True),
    sa.Column("existing_ats", sa.String(length=255), nullable=True),
    sa.Column("role_categories", sa.Text(), nullable=True),
]

SUBSCRIPTION_NEW_COLUMNS = [
    sa.Column("currency", sa.String(length=3), nullable=True, server_default="INR"),
    sa.Column("payment_method", sa.String(length=30), nullable=True),
    sa.Column("po_required", sa.Boolean(), nullable=True, server_default=sa.false()),
    sa.Column("po_number", sa.String(length=100), nullable=True),
]


def upgrade() -> None:
    with op.batch_alter_table("companies") as batch:
        for col in COMPANY_NEW_COLUMNS:
            batch.add_column(col)

    with op.batch_alter_table("subscription_plans") as batch:
        for col in SUBSCRIPTION_NEW_COLUMNS:
            batch.add_column(col)


def downgrade() -> None:
    with op.batch_alter_table("subscription_plans") as batch:
        for col in reversed(SUBSCRIPTION_NEW_COLUMNS):
            batch.drop_column(col.name)

    with op.batch_alter_table("companies") as batch:
        for col in reversed(COMPANY_NEW_COLUMNS):
            batch.drop_column(col.name)
