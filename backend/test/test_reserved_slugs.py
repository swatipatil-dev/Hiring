"""
The reserved-slug list must not drift behind the routes.

A company's slug is its public URL path (``career.innomaxskills.com/<slug>``), so a
company called "Jobs" would shadow the job board for every visitor, and one called "Api"
would be unreachable behind the Apache proxy. ``RESERVED_SLUGS`` is the only thing
preventing that, and it lives in a different file from the routes it protects — so it is
exactly the kind of list that goes stale the next time someone adds a route.

These tests read the routes from their real sources and fail when a new one is not
covered. The fix when one fails is a one-line addition to ``RESERVED_SLUGS``.
"""
import re
from pathlib import Path

import pytest

from src.tenancy.slug import is_reserved_slug, slugify

_REPO = Path(__file__).resolve().parents[2]
_APP_JSX = _REPO / "frontend" / "src" / "App.jsx"
_VHOST = _REPO / "deploy" / "cpanel" / "hireflow.conf"


def _claimable(names):
    """Names a company could actually be given.

    ``slugify`` strips dots and underscores, so paths like ``openapi.json`` and
    ``/cover_letters/`` can never be produced as a slug and need no reservation.
    """
    return {n for n in names if n and slugify(n) == n}


def _spa_top_level_routes():
    src = _APP_JSX.read_text(encoding="utf-8")
    paths = re.findall(r'<Route path="([^"]+)"', src)
    assert paths, "no routes parsed from App.jsx — did the file move?"
    return {
        p.strip("/").split("/")[0]
        for p in paths
        if p != "*" and not p.strip("/").startswith(":")
    }


def _proxy_prefixes():
    src = _VHOST.read_text(encoding="utf-8")
    prefixes = re.findall(r"^ProxyPass\s+/([A-Za-z0-9_.-]+)", src, re.M)
    assert prefixes, "no ProxyPass prefixes parsed from hireflow.conf — did the file move?"
    return set(prefixes)


@pytest.mark.parametrize("source", ["spa", "proxy"])
def test_every_route_name_is_reserved(source):
    names = _spa_top_level_routes() if source == "spa" else _proxy_prefixes()
    gaps = sorted(n for n in _claimable(names) if not is_reserved_slug(n))
    assert not gaps, (
        f"These {source} route names are not in RESERVED_SLUGS, so a company could "
        f"claim one and shadow the route: {gaps}. Add them to "
        f"backend/src/tenancy/slug.py."
    )


def test_the_brand_url_we_ship_is_not_itself_reserved():
    """Sanity check on the list: it must block routes without blocking real companies."""
    assert slugify("Innomax IT Solutions") == "innomax-it-solutions"
    assert not is_reserved_slug("innomax-it-solutions")
    assert not is_reserved_slug("acme-corp")
