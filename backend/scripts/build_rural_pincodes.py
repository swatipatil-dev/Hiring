"""
Build data/rural_pincodes.json from the India Post "All India Pincode Directory".

There is no official column that labels a pincode rural vs urban, so we use a
proxy based on the mix of post-office types under each pincode:

    * B.O  (Branch Office)  -> serves a village
    * S.O  (Sub Office)     -> serves a town
    * H.O  (Head Office)    -> serves a city

A pincode's delivery area almost always includes its parent Sub Office, and
rural village Branch Offices share that S.O's pincode. So "has any S.O" is
meaningless. Instead we look at which office type DOMINATES the pincode:

    rural  if Branch Offices outnumber Sub/Head Offices  (BO > SO + HO)
    urban  otherwise

This cleanly separates metros/towns (served purely or mostly by S.O/H.O) from
village-dominated delivery areas. It is a heuristic, not ground truth, but it
tracks rural/urban well at pincode granularity and is fully reproducible.
On the current directory it yields ~77% rural / ~23% urban pincodes.

Usage:
    # download the source CSV automatically:
    python scripts/build_rural_pincodes.py

    # or process a local copy you already downloaded:
    python scripts/build_rural_pincodes.py --csv path/to/all-india-pincode.csv

Source CSV (mirror of the India Post directory, columns include officeType):
    https://github.com/saravanakumargn/All-India-Pincode-Directory
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import urllib.request

DEFAULT_URL = (
    "https://raw.githubusercontent.com/saravanakumargn/"
    "All-India-Pincode-Directory/master/all-india-pincode-html-csv.csv"
)

OUT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),  # repo root
    "data",
    "rural_pincodes.json",
)

README = (
    "Pincode -> region classification used for the ATS rural-address bonus. "
    "Keys are 6-digit Indian pincodes (as strings); values are 'rural' or 'urban'. "
    "Pincodes not present here are treated as 'unknown' and earn NO bonus. "
    "Derived from the India Post All-India Pincode Directory: a pincode is 'rural' "
    "when Branch Offices outnumber Sub/Head Offices (BO > SO + HO), else 'urban'. "
    "Regenerate with: python scripts/build_rural_pincodes.py"
)


def _office_class(office_type: str) -> str | None:
    """Map an India Post office type to 'BO', 'SO', or 'HO' (None if unrecognized)."""
    t = office_type.strip().upper()
    if t.startswith("B"):
        return "BO"   # Branch Office (incl. "B.O directly a/w Head Office")
    if t.startswith("S"):
        return "SO"   # Sub Office
    if t.startswith("H") or t.startswith("G"):
        return "HO"   # Head Office / GPO
    return None


def _open_source(csv_path: str | None) -> io.TextIOBase:
    if csv_path:
        print(f"Reading local CSV: {csv_path}")
        return open(csv_path, "r", encoding="utf-8", errors="replace", newline="")
    print(f"Downloading source CSV:\n  {DEFAULT_URL}")
    with urllib.request.urlopen(DEFAULT_URL, timeout=180) as resp:  # noqa: S310
        data = resp.read().decode("utf-8", errors="replace")
    print(f"Downloaded {len(data):,} bytes")
    return io.StringIO(data)


def build(csv_path: str | None) -> dict:
    # Per pincode, count Branch vs Sub/Head offices.
    bo: dict[str, int] = {}
    other: dict[str, int] = {}  # SO + HO

    fh = _open_source(csv_path)
    with fh:
        reader = csv.DictReader(fh)
        # Normalize header lookup (the mirror uses 'pincode' / 'officeType').
        field_map = {k.lower(): k for k in (reader.fieldnames or [])}
        pin_key = field_map.get("pincode")
        type_key = field_map.get("officetype")
        if not pin_key or not type_key:
            sys.exit(f"CSV missing pincode/officeType columns; found: {reader.fieldnames}")

        for row in reader:
            pin = (row.get(pin_key) or "").strip()
            if len(pin) != 6 or not pin.isdigit():
                continue
            cls = _office_class(row.get(type_key) or "")
            if cls == "BO":
                bo[pin] = bo.get(pin, 0) + 1
            elif cls in ("SO", "HO"):
                other[pin] = other.get(pin, 0) + 1

    seen = set(bo) | set(other)
    # rural when Branch Offices dominate the pincode's delivery network.
    pincodes = {
        pin: ("rural" if bo.get(pin, 0) > other.get(pin, 0) else "urban")
        for pin in sorted(seen)
    }
    rural_count = sum(1 for v in pincodes.values() if v == "rural")
    print(
        f"Classified {len(pincodes):,} unique pincodes: "
        f"{rural_count:,} rural / {len(pincodes) - rural_count:,} urban"
    )
    return pincodes


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--csv", help="Path to a local India Post pincode CSV", default=None)
    args = ap.parse_args()

    pincodes = build(args.csv)
    payload = {
        "_README": README,
        "_schema_version": 1,
        "pincodes": pincodes,
    }
    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as out:
        json.dump(payload, out, separators=(",", ":"), ensure_ascii=False)
    size_kb = os.path.getsize(OUT_PATH) / 1024
    print(f"Wrote {OUT_PATH} ({size_kb:,.0f} KB)")


if __name__ == "__main__":
    main()
