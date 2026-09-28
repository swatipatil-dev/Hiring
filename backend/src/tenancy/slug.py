"""
Company slug rules — the server-side source of truth.

A company's slug is not a cosmetic identifier. It is both the tenant's public URL path
(``career.innomaxskills.com/acme-corp``) and a subdomain (``acme.hireflow.io``) that
tenant_domain.py resolves tenants by. So it has to be a valid DNS label, and it must not
be allowed to shadow a hostname or a route the platform itself needs.
`frontend/src/utils/slugify.js` mirrors slugify() so the UI previews exactly what the API
will store; this module is what actually decides, including the reserved-name check.
"""
import re
import unicodedata

# Longest a single DNS label may be. Company.slug is String(100), but the subdomain
# constraint is the tighter one, so it wins.
MAX_SLUG_LENGTH = 63

# Hostnames and URL paths the platform routes on, serves from, or is likely to want. A
# tenant that claimed one of these would either break routing or intercept traffic meant
# elsewhere — "api" being the obvious case, since api.hireflow.io must never resolve to a
# tenant.
#
# A slug is also the company's public URL path (career.innomaxskills.com/<slug>), so every
# top-level route in frontend/src/App.jsx and every ProxyPass prefix in
# deploy/cpanel/hireflow.conf has to be here too — a company called "Jobs" would otherwise
# shadow the job board for everyone. test/test_reserved_slugs.py keeps this in step.
RESERVED_SLUGS = frozenset({
    # Platform hostnames and generic infrastructure names.
    "admin", "api", "app", "apps", "assets", "auth", "billing", "blog", "cdn",
    "dashboard", "dev", "developer", "docs", "files", "ftp", "help", "hireflow",
    "images", "internal", "login", "logout", "mail", "media", "monitoring", "news",
    "portal", "preview", "public", "root", "security", "signup", "smtp", "staging",
    "static", "status", "superadmin", "support", "system", "test", "testing",
    "uploads", "webhook", "webhooks", "www",
    # Top-level SPA routes (frontend/src/App.jsx).
    "c", "candidate", "forgot-password", "hr", "interviewer", "jobs", "profile",
    "register",
    # Apache ProxyPass prefixes (deploy/cpanel/hireflow.conf). These never reach the SPA,
    # so a company holding one would simply be unreachable.
    "health", "interviews", "logos", "mcq", "proctor", "redoc", "resumes",
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
