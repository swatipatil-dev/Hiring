"""
Shared symbols for the Company onboarding-profile surface.

Both ``routes/superadmin_routes.py`` and ``routes/admin_routes.py`` need the
exact same list of editable profile columns and the exact same GET serializer
shape — keeping a single source of truth here prevents the two surfaces from
drifting apart as fields are added.
"""

from src.models.models import Company


# Field names that can be set/updated on Company via either the SuperAdmin
# onboarding flow or the Tenant Admin self-service profile page, beyond the
# always-required company_name + slug.
#
# Platform-managed fields intentionally excluded:
#   slug, api_key, is_active, custom_domain, domain_verified,
#   domain_pending, domain_verify_token
COMPANY_PROFILE_FIELDS = (
    # Branding
    "logo_url", "favicon_url", "primary_color", "secondary_color",
    "accent_color", "font_family", "tagline", "support_email", "logo_dark_url",
    # Legal & tax identity
    "legal_name", "brand_name", "company_type", "cin", "incorporation_date",
    "authorized_signatory_name", "authorized_signatory_designation",
    # Contacts
    "primary_contact_name", "primary_contact_email", "primary_contact_phone", "primary_contact_designation",
    "billing_contact_name", "billing_contact_email", "billing_contact_phone", "billing_contact_designation",
    "technical_contact_name", "technical_contact_email", "technical_contact_phone", "technical_contact_designation",
    "dpo_contact_name", "dpo_contact_email", "dpo_contact_phone", "dpo_contact_designation",
    "escalation_contact_name", "escalation_contact_email", "escalation_contact_phone", "escalation_contact_designation",
    # Addresses
    "reg_address_line1", "reg_address_line2", "reg_city", "reg_state", "reg_country", "reg_postal_code",
    "billing_same_as_registered",
    "billing_address_line1", "billing_address_line2", "billing_city", "billing_state",
    "billing_country", "billing_postal_code",
    # Business profile
    "industry", "company_size_band", "years_in_business", "website_url", "description",
    # Hiring footprint
    "expected_hires_per_year", "recruiter_count", "hiring_locations", "existing_ats", "role_categories",
)


def serialize_company(company: Company) -> dict:
    """Full company onboarding-profile payload for GET/PUT responses."""
    out = {
        "id": company.id,
        "name": company.name,
        "slug": company.slug,
        "api_key": company.api_key,
        "is_active": company.is_active,
        "created_at": company.created_at,
        "custom_domain": company.custom_domain,
        "domain_verified": company.domain_verified,
    }
    for field in COMPANY_PROFILE_FIELDS:
        out[field] = getattr(company, field, None)
    return out
