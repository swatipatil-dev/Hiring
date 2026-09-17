"""
Role -> permission catalog, read from the bundled RBAC matrix.

``data/rbac_permission_matrix.csv`` is generated from the live route gates by
``scripts/rbac_extract_gates.py``. Until now nothing read it at runtime; this
module loads it so the UI can answer "what is my role allowed to do?".

This is *descriptive*, not an enforcement path — the real gates are the
``require_role`` / ``AuthMiddleware.get_current_*`` dependencies in ``src/auth.py``.
Nothing here should ever be used to decide whether a request is permitted.

Note: ``src.auth.get_user_permissions()`` predates this and uses a different,
incomplete vocabulary (``create_jobs`` vs ``jobs.create``). It has no callers.
This module supersedes it.

The load degrades gracefully: a missing or malformed CSV yields an empty
catalog and a warning, never an exception. A page with no permission list is a
cosmetic failure; a 500 on every profile view is not.

- ``get_catalog()``                  -> list of normalised permission dicts
- ``get_permissions_for_role(role)`` -> the grouped API payload for one role
- ``reload_catalog()``               -> clear the cache (tests)
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Bundled source data, resolved relative to this module — never against the
# process CWD (systemd, cron, Passenger and an interactive shell all differ),
# and deliberately NOT against HIREFLOW_DATA_DIR: that points at the uploads
# volume, which is a different thing that would not contain this file.
_MATRIX_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),  # -> backend/
    "data",
    "rbac_permission_matrix.csv",
)

ROLES = ("superadmin", "admin", "hr", "interviewer", "candidate")

# Rows past the "UNGATED ENDPOINTS" banner carry no Slug — they document
# endpoints that have no permission at all. Sorting them last would be wrong;
# they are excluded entirely (see _load).
_UNORDERED = 10 ** 9

# Module-level cache, loaded lazily on first use.
_CATALOG: Optional[List[Dict[str, Any]]] = None
_LOAD_ERROR: Optional[str] = None


def _cell(row: Dict[str, Any], key: str) -> str:
    """A stripped cell value, tolerating a missing column or a None."""
    return (row.get(key) or "").strip()


def _normalise_row(row: Dict[str, Any], group_order: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """Turn one CSV row into a catalog entry, or None if it isn't a real permission."""
    slug = _cell(row, "Slug")
    group = _cell(row, "Group")
    # Slug-emptiness is the real invariant that separates the 136 seedable
    # permissions from the blank separator, the banner, and the 92
    # documentation-only rows below it. Don't match on the banner text — the
    # generator may add more sections later.
    if not slug or not group:
        return None

    try:
        display_order = int(_cell(row, "Display_Order"))
    except (TypeError, ValueError):
        display_order = _UNORDERED  # sinks to the bottom of its group, doesn't crash the sort

    if group not in group_order:
        group_order[group] = len(group_order)  # first appearance == authored order

    entry: Dict[str, Any] = {
        "slug": slug,
        "group": group,
        "label": _cell(row, "Permission") or slug,
        "endpoints": [e.strip() for e in _cell(row, "Endpoints").split(" | ") if e.strip()],
        "conflict": _cell(row, "Conflict").upper() == "Y",
        "current_gate": _cell(row, "Current_Gate"),
        "_group_order": group_order[group],
        "_display_order": display_order,
    }
    # A cell is a grant only when it literally says Y. Blank, "N" and stray
    # whitespace are all "not granted" — never truthiness-test the raw value.
    for role in ROLES:
        entry[role] = _cell(row, role).upper() == "Y"
    return entry


def _load() -> List[Dict[str, Any]]:
    """Load and cache the catalog. Never raises — returns [] on any failure."""
    global _CATALOG, _LOAD_ERROR
    if _CATALOG is not None:
        return _CATALOG

    rows: List[Dict[str, Any]] = []
    group_order: Dict[str, int] = {}
    skipped = 0
    try:
        # utf-8-sig is a no-op on the current file but survives someone
        # re-saving it from Excel; newline="" is the required csv idiom.
        with open(_MATRIX_PATH, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                try:
                    entry = _normalise_row(row, group_order)
                except Exception:
                    skipped += 1  # one bad row must not zero the other 135
                    continue
                if entry is not None:
                    rows.append(entry)
        _LOAD_ERROR = None
    except (OSError, ValueError, csv.Error, UnicodeDecodeError, AttributeError) as exc:
        _LOAD_ERROR = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "RBAC permission catalog unavailable (%s) at %s — profile pages will "
            "show no permission details.", _LOAD_ERROR, _MATRIX_PATH
        )
        _CATALOG = []
        return _CATALOG

    if skipped:
        logger.warning("RBAC permission catalog: skipped %d malformed row(s)", skipped)

    rows.sort(key=lambda r: (r["_group_order"], r["_display_order"], r["slug"]))
    _CATALOG = rows
    return _CATALOG


def get_catalog() -> List[Dict[str, Any]]:
    """Every real permission, sorted by group (authored order) then display order."""
    return _load()


def get_load_error() -> Optional[str]:
    """Why the catalog is empty, or None if it loaded cleanly."""
    _load()
    return _LOAD_ERROR


def get_permissions_for_role(role: str) -> Dict[str, Any]:
    """
    The grouped payload for one role.

    Every permission is returned, each flagged ``granted`` — not just the
    granted ones. A user opens the profile page *because* something is greyed
    out, so "Jobs: 0 of 16" is the answer they came for; an absent Jobs section
    is indistinguishable from a broken page.
    """
    normalised = (role or "").strip().lower()
    known_role = normalised in ROLES

    groups: List[Dict[str, Any]] = []
    index: Dict[str, Dict[str, Any]] = {}
    granted_total = 0

    for entry in get_catalog():
        bucket = index.get(entry["group"])
        if bucket is None:
            bucket = {"group": entry["group"], "total": 0, "granted_count": 0, "permissions": []}
            index[entry["group"]] = bucket
            groups.append(bucket)

        granted = bool(entry.get(normalised)) if known_role else False
        perm: Dict[str, Any] = {
            "slug": entry["slug"],
            "label": entry["label"],
            "granted": granted,
            "endpoints": entry["endpoints"],
        }
        # Only the 15 inconsistent rows carry these — omitted elsewhere so the
        # client can just test `perm.conflict`.
        if entry["conflict"]:
            perm["conflict"] = True
            perm["current_gate"] = entry["current_gate"]

        bucket["permissions"].append(perm)
        bucket["total"] += 1
        if granted:
            bucket["granted_count"] += 1
            granted_total += 1

    return {
        "role": normalised,
        "known_role": known_role,
        "total": len(get_catalog()),
        "granted_count": granted_total,
        "group_count": len(groups),
        "granted_group_count": sum(1 for g in groups if g["granted_count"] > 0),
        "groups": groups,
    }


def reload_catalog() -> None:
    """Clear the cache so the CSV is re-read on next use (useful in tests)."""
    global _CATALOG, _LOAD_ERROR
    _CATALOG = None
    _LOAD_ERROR = None
