"""
Rural / urban classification of Indian pincodes for ATS bonus scoring.

A candidate whose *permanent* address falls in a rural pincode earns a small
affirmative bonus. Classification is a straight lookup against a bundled
reference dataset (``data/rural_pincodes.json``).

The lookup degrades gracefully: a missing dataset, an unparseable pincode, or a
pincode that isn't in the dataset all resolve to ``"unknown"`` — which earns no
bonus rather than raising. This keeps scoring correct even before the full
dataset has been populated.

- ``classify_pincode(pincode)`` → ``"rural"`` | ``"urban"`` | ``"unknown"``
- ``is_rural(pincode)``         → bool
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict

_DATASET_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),  # repo root
    "data",
    "rural_pincodes.json",
)

_PINCODE_RE = re.compile(r"\d{6}")

# Module-level cache, loaded lazily on first use.
_PINCODE_MAP: Dict[str, str] | None = None


def _load_map() -> Dict[str, str]:
    """Load and cache the pincode->region map. Never raises — returns {} on error."""
    global _PINCODE_MAP
    if _PINCODE_MAP is not None:
        return _PINCODE_MAP
    try:
        with open(_DATASET_PATH, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        pincodes = raw.get("pincodes", {}) if isinstance(raw, dict) else {}
        # Normalize values to lowercase strings.
        _PINCODE_MAP = {
            str(k).strip(): str(v).strip().lower()
            for k, v in pincodes.items()
        }
    except (OSError, ValueError, AttributeError):
        _PINCODE_MAP = {}
    return _PINCODE_MAP


def _clean_pincode(pincode) -> str | None:
    """Extract a 6-digit pincode from arbitrary input, or None if absent."""
    if pincode is None:
        return None
    match = _PINCODE_RE.search(str(pincode))
    return match.group(0) if match else None


def classify_pincode(pincode) -> str:
    """Return ``"rural"``, ``"urban"`` or ``"unknown"`` for a pincode."""
    code = _clean_pincode(pincode)
    if not code:
        return "unknown"
    value = _load_map().get(code)
    if value in ("rural", "urban"):
        return value
    return "unknown"


def is_rural(pincode) -> bool:
    """True only when the pincode is positively classified rural."""
    return classify_pincode(pincode) == "rural"


def reload_dataset() -> None:
    """Clear the cache so the dataset is re-read on next lookup (useful in tests)."""
    global _PINCODE_MAP
    _PINCODE_MAP = None
