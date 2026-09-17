"""
Unit tests for the RBAC permission catalog loader.

Pure unit tests — no app, no DB, no SECRET_KEY. The loader reads a bundled CSV
and must never raise, so the failure paths matter as much as the happy one.
"""

import pytest

from src.services import permissions_catalog as pc


# The whole point of the catalog is that these numbers come from the real route
# gates. If someone regenerates the CSV with scripts/rbac_extract_gates.py and
# the gates have shifted, this is the test that says so out loud instead of
# silently showing users the wrong permissions.
EXPECTED_GRANTS = {
    "superadmin": 25,
    "admin": 102,
    "hr": 78,
    "interviewer": 19,
    "candidate": 18,
}
EXPECTED_TOTAL = 136
EXPECTED_GROUPS = 18
EXPECTED_CONFLICTS = 15


@pytest.fixture(autouse=True)
def _fresh_catalog():
    """Each test starts from a clean cache and leaves one behind."""
    pc.reload_catalog()
    yield
    pc.reload_catalog()


def test_catalog_loads_expected_row_count():
    assert len(pc.get_catalog()) == EXPECTED_TOTAL
    assert pc.get_load_error() is None


def test_every_entry_is_well_formed():
    for entry in pc.get_catalog():
        assert entry["slug"], "every entry needs a slug"
        assert entry["group"], "every entry needs a group"
        assert entry["label"], "every entry needs a display label"
        assert isinstance(entry["endpoints"], list)


def test_slugs_are_unique():
    slugs = [e["slug"] for e in pc.get_catalog()]
    assert len(slugs) == len(set(slugs))


def test_documentation_rows_are_excluded():
    """The 92 rows below the banner have no Slug and must never reach the UI —
    they are an audit finding (endpoints with no permission), not a feature."""
    groups = {e["group"] for e in pc.get_catalog()}
    assert "UNGATED ENDPOINTS" not in groups
    assert all(e["slug"].strip() for e in pc.get_catalog())


def test_group_count():
    assert len({e["group"] for e in pc.get_catalog()}) == EXPECTED_GROUPS


def test_groups_are_contiguous_and_in_authored_order():
    """Sorting must preserve the CSV's authored group sequence (Jobs first,
    System & Database last) — alphabetising would scramble it."""
    seen = []
    for entry in pc.get_catalog():
        if not seen or seen[-1] != entry["group"]:
            assert entry["group"] not in seen, f"group {entry['group']} is not contiguous"
            seen.append(entry["group"])
    assert seen[0] == "Jobs"
    assert seen[-1] == "System & Database"


def test_display_order_ascends_within_each_group():
    by_group = {}
    for entry in pc.get_catalog():
        by_group.setdefault(entry["group"], []).append(entry["_display_order"])
    for group, orders in by_group.items():
        assert orders == sorted(orders), f"{group} is out of display order"


def test_conflict_rows_carry_their_gate():
    conflicted = [e for e in pc.get_catalog() if e["conflict"]]
    assert len(conflicted) == EXPECTED_CONFLICTS
    for entry in conflicted:
        assert entry["current_gate"], f"{entry['slug']} is flagged conflicting but has no gate"


def test_endpoints_are_split_not_concatenated():
    entry = next(e for e in pc.get_catalog() if e["slug"] == "jobs.approve")
    assert entry["endpoints"] == ["PUT /api/v1/hr/jobs/{job_id}/approve"]
    for e in pc.get_catalog():
        assert all("|" not in ep for ep in e["endpoints"])


@pytest.mark.parametrize("role,expected", sorted(EXPECTED_GRANTS.items()))
def test_granted_count_per_role(role, expected):
    payload = pc.get_permissions_for_role(role)
    assert payload["granted_count"] == expected
    assert payload["total"] == EXPECTED_TOTAL
    assert payload["known_role"] is True


def test_payload_groups_all_present_even_when_none_granted():
    """Superadmin has permissions in only 4 of 18 groups. All 18 must still be
    returned with a 0 count — an absent section reads as a broken page."""
    payload = pc.get_permissions_for_role("superadmin")
    assert payload["group_count"] == EXPECTED_GROUPS
    assert payload["granted_group_count"] == 4
    assert sum(g["total"] for g in payload["groups"]) == EXPECTED_TOTAL


def test_group_counts_are_internally_consistent():
    payload = pc.get_permissions_for_role("hr")
    assert sum(g["granted_count"] for g in payload["groups"]) == payload["granted_count"]
    for group in payload["groups"]:
        assert group["granted_count"] == sum(1 for p in group["permissions"] if p["granted"])
        assert group["total"] == len(group["permissions"])


def test_payload_omits_generator_exhaust():
    """Description ('1 endpoint under /api/v1/hr') is generator output, not
    user-facing copy, and must not be sent."""
    payload = pc.get_permissions_for_role("admin")
    for group in payload["groups"]:
        for perm in group["permissions"]:
            assert "description" not in perm
            assert "Description" not in perm
            if not perm.get("conflict"):
                assert "current_gate" not in perm


def test_unknown_role_gets_full_catalog_with_nothing_granted():
    payload = pc.get_permissions_for_role("recruiter")
    assert payload["known_role"] is False
    assert payload["granted_count"] == 0
    assert payload["total"] == EXPECTED_TOTAL
    assert payload["group_count"] == EXPECTED_GROUPS


@pytest.mark.parametrize("role", ["HR", " hr ", "Admin"])
def test_role_matching_is_case_and_whitespace_insensitive(role):
    payload = pc.get_permissions_for_role(role)
    assert payload["known_role"] is True
    assert payload["granted_count"] == EXPECTED_GRANTS[role.strip().lower()]


def test_none_role_does_not_raise():
    payload = pc.get_permissions_for_role(None)
    assert payload["granted_count"] == 0
    assert payload["known_role"] is False


def test_missing_file_degrades_instead_of_raising(monkeypatch):
    """A missing CSV must leave the app serving an empty list, not a 500."""
    monkeypatch.setattr(pc, "_MATRIX_PATH", "/nonexistent/rbac_permission_matrix.csv")
    pc.reload_catalog()

    assert pc.get_catalog() == []
    assert pc.get_load_error() is not None

    payload = pc.get_permissions_for_role("hr")
    assert payload["total"] == 0
    assert payload["groups"] == []
    assert payload["granted_count"] == 0


def test_malformed_file_degrades_instead_of_raising(tmp_path, monkeypatch):
    bad = tmp_path / "broken.csv"
    bad.write_bytes(b"\xff\xfe\x00not,a,valid\x00csv")
    monkeypatch.setattr(pc, "_MATRIX_PATH", str(bad))
    pc.reload_catalog()

    assert pc.get_catalog() == []


def test_row_missing_a_role_column_is_not_granted(tmp_path, monkeypatch):
    """A regenerated CSV that drops a column must degrade to 'not granted',
    not KeyError."""
    csv_path = tmp_path / "partial.csv"
    csv_path.write_text(
        "Group,Permission,Slug,Display_Order,admin,Endpoints\n"
        "Jobs,Create jobs,jobs.create,10,Y,POST /api/v1/hr/jobs\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pc, "_MATRIX_PATH", str(csv_path))
    pc.reload_catalog()

    assert len(pc.get_catalog()) == 1
    assert pc.get_permissions_for_role("admin")["granted_count"] == 1
    assert pc.get_permissions_for_role("hr")["granted_count"] == 0


def test_non_y_cell_is_not_a_grant(tmp_path, monkeypatch):
    csv_path = tmp_path / "cells.csv"
    csv_path.write_text(
        "Group,Permission,Slug,Display_Order,superadmin,admin,hr,interviewer,candidate,Endpoints\n"
        "Jobs,A,jobs.a,10,,N,  ,x,Y,GET /a\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pc, "_MATRIX_PATH", str(csv_path))
    pc.reload_catalog()

    assert pc.get_permissions_for_role("admin")["granted_count"] == 0
    assert pc.get_permissions_for_role("hr")["granted_count"] == 0
    assert pc.get_permissions_for_role("interviewer")["granted_count"] == 0
    assert pc.get_permissions_for_role("candidate")["granted_count"] == 1


def test_unparseable_display_order_does_not_break_the_sort(tmp_path, monkeypatch):
    csv_path = tmp_path / "order.csv"
    csv_path.write_text(
        "Group,Permission,Slug,Display_Order,hr,Endpoints\n"
        "Jobs,B,jobs.b,notanumber,Y,GET /b\n"
        "Jobs,A,jobs.a,10,Y,GET /a\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(pc, "_MATRIX_PATH", str(csv_path))
    pc.reload_catalog()

    slugs = [e["slug"] for e in pc.get_catalog()]
    assert slugs == ["jobs.a", "jobs.b"], "the malformed row should sink, not crash"
