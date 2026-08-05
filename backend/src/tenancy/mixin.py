"""
TenantScoped — marker mixin used by the before_compile event hook to know
which mapped classes should be auto-filtered by company_id.

This class adds NO columns and NO behaviour of its own. The `company_id`
column is already defined on each model individually; this mixin only opts the
class into the global filter.
"""


class TenantScoped:
    """Marker mixin. Subclass it on any ORM model that has a `company_id` column."""
    __tenant_scoped__ = True
