"""
TenantScoped — marker mixin used by the tenant filter in src/tenancy/events.py
to know which mapped classes are scoped by company_id.

The `company_id` column is defined on each model individually. The class-level
placeholder below adds no column: it exists because SQLAlchemy evaluates the
`with_loader_criteria(TenantScoped, lambda cls: cls.company_id == ...)` lambda
once against this mixin itself before applying it to each mapped subclass, and
that first evaluation fails if the attribute is missing.
"""


class TenantScoped:
    """Marker mixin. Subclass it on any ORM model that has a `company_id` column."""
    __tenant_scoped__ = True
    company_id = None  # placeholder; every subclass defines the real column
