# models package
# Importing src.tenancy here registers the SQLAlchemy tenant hooks
# (in src/tenancy/events.py). Models import this hook indirectly via
# `from ..tenancy import TenantScoped` in models.py, so the registration
# happens at first model import — before any query is compiled.
from .. import tenancy  # noqa: F401
