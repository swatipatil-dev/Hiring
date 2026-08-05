"""
Company slug rules — the server-side source of truth.

A company's slug is not a cosmetic identifier: it becomes a subdomain
(``acme.hireflow.io``) that tenant_domain.py resolves tenants by. So it has to be a
valid DNS label, and it must not be allowed to shadow a hostname the platform itself
needs. `frontend/src/utils/slugify.js` mirrors slugify() so the UI previews exactly what
the API will store; this module is what actually decides.
"""
import re
import unicodedata

# Longest a single DNS label may be. Company.slug is String(100), but the subdomain
# constraint is the tighter one, so it wins.
MAX_SLUG_LENGTH = 63

# Hostnames the platform routes on, serves from, or is likely to want. A tenant that
# claimed one of these would either break routing or intercept traffic meant elsewhere —
# "api" being the obvious case, since api.hireflow.io must never resolve to a tenant.
RESERVED_SLUGS = frozenset({
    "admin", "api", "app", "apps", "assets", "auth", "billing", "blog", "cdn",
    "dashboard", "dev", "developer", "docs", "files", "ftp", "help", "hireflow",
    "images", "internal", "login", "logout", "mail", "media", "monitoring", "news",
    "portal", "preview", "public", "root", "security", "signup", "smtp", "staging",
    "static", "status", "superadmin", "support", "system", "test", "testing",
    "uploads", "webhook", "webhooks", "www",
})

_INVALID_CHARS = re.compile(r"[^a-z0-9]+")
_REPEATED_HYPHEN = re.compile(r"-{2,}")


def slugify(value: str) -> str:
    """
    Normalise arbitrary text into a safe subdomain label.

    Lowercases, turns every run of non-alphanumerics into a single hyphen, and trims
    hyphens from both ends — a leading or trailing hyphen is invalid in a DNS label.
    Returns "" when nothing usable survives (e.g. "!!!"), which callers must treat as
    invalid rather than storing.

        >>> slugify("Innomax  Sol!!")
        'innomax-sol'
        >>> slugify("New Slug Name")
        'new-slug-name'
        >>> slugify("!!!")
        ''
    """
    if not value:
        return ""
    # Fold accents to their base letters first, so "Café" gives "cafe" rather than the
    # "caf" you get from simply dropping every character outside a-z0-9. Real company
    # names have accents (Zürich, São Paulo) and losing the letter reads as a bug.
    decomposed = unicodedata.normalize("NFKD", str(value))
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))

    slug = _INVALID_CHARS.sub("-", ascii_only.strip().lower())
    slug = _REPEATED_HYPHEN.sub("-", slug).strip("-")
    # Truncating can re-expose a trailing hyphen, so strip again afterwards.
    return slug[:MAX_SLUG_LENGTH].strip("-")


def is_reserved_slug(slug: str) -> bool:
    """True if the (already slugified) value is one the platform keeps for itself."""
    return (slug or "").lower() in RESERVED_SLUGS
